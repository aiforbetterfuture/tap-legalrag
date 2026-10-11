"""
law_rag.reasoning — 추론 계층의 규칙 엔진 (통합설계 4장 ③).

역할:
    근거 번들 안에서 조문들이 상충할 때, 어느 것이 우선인지를
    '결정론적 규칙'으로 정렬한다. LLM을 쓰지 않는다.

플러그인 구조 (개선 반영):
    규칙을 개별 Rule 객체로 만들고, RuleEngine이 규칙 리스트를 받는다.
    → 규칙을 하나씩 켜고 끌 수 있어, 논문의 절제 연구(ablation)가 코드로 실행된다.
      예: TemporalRule을 빼고 정산 정확도가 얼마나 떨어지는지 측정.
    → 새 규칙 추가 = Rule 하나 작성 + 리스트에 등록. 기존 코드 무변경.

왜 LLM에 우선순위 판단을 맡기지 않는가:
    법 우선순위를 LLM에 맡기면 환각이 생긴다. 규칙은 (1) 빠르고 (2) 정확하고
    (3) '왜 이 조문이 우선인가'를 감사로그로 설명할 수 있다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from law_rag.models import EvidenceBundle, LawChunk, RankedEvidence


class Rule(ABC):
    """정렬 규칙 인터페이스. 각 규칙은 청크에 '정렬키'를 부여한다.

    sort_key가 작을수록 우선. 여러 규칙을 순서대로 적용하면
    앞 규칙이 1차 기준, 뒤 규칙이 동점 처리 기준이 된다(사전식 정렬).
    """

    name: str = "rule"

    @abstractmethod
    def sort_key(self, chunk: LawChunk) -> float | int:
        """이 규칙이 부여하는 정렬 우선순위(작을수록 우선)."""
        ...

    def explain(self, chunk: LawChunk) -> str:
        """감사로그용 설명(선택)."""
        return ""


class HierarchyRule(Rule):
    """상위법 우선: hierarchy_level이 낮을수록(=법률에 가까울수록) 우선."""
    name = "hierarchy"

    def sort_key(self, chunk: LawChunk) -> int:
        return chunk.hierarchy_level

    def explain(self, chunk: LawChunk) -> str:
        return f"위계 {chunk.hierarchy_level}"


class TemporalRule(Rule):
    """최신 개정 우선: 조문시행일이 늦을수록 우선(음수로 뒤집어 오름차순 정렬).

    이 규칙을 껐을 때 정산 질문 정확도가 얼마나 떨어지는지가
    논문 절제 연구의 한 항목이 된다."""
    name = "temporal"

    def sort_key(self, chunk: LawChunk) -> int:
        return -chunk.article_enforce_date.toordinal()

    def explain(self, chunk: LawChunk) -> str:
        return f"시행 {chunk.article_enforce_date}"


class StableRule(Rule):
    """안정적 정렬: 위 규칙들이 모두 같으면 chunk_id로 고정(재현성 보장).
    hash가 아닌 문자열 순서라 실행마다 결과가 같다."""
    name = "stable"

    def sort_key(self, chunk: LawChunk) -> str:  # type: ignore[override]
        return chunk.chunk_id


# 기본 규칙 세트: 상위법 → 최신 → 안정. 논문에서 이 리스트를 바꿔 절제 연구.
DEFAULT_RULES: list[Rule] = [HierarchyRule(), TemporalRule(), StableRule()]


class RuleEngine:
    """결정론적 충돌 해소 엔진. 규칙 리스트를 주입받는다(플러그인).

    실험 예:
        RuleEngine([HierarchyRule(), StableRule()])        # 시점 규칙 제거
        RuleEngine([TemporalRule(), HierarchyRule(), ...]) # 최신 우선으로 순서 변경
    """

    def __init__(self, rules: list[Rule] | None = None):
        self.rules = rules if rules is not None else list(DEFAULT_RULES)

    def rank(self, bundle: EvidenceBundle) -> RankedEvidence:
        """1순위 근거(검색 결과)의 순위를 보존하고, 조립 근거는 그 뒤에 둔다(P0-6).

        왜 전체를 재정렬하지 않나:
            전부 '상위법 우선'으로 재정렬하면, 조립으로 딸려 온 상위법 조문이
            검색 1위(실제로 질문에 가장 관련된 조문)를 밀어낸다. 상위법 우선은
            '충돌·예외가 있을 때'의 규칙이지, 검색 관련도를 덮어쓰는 규칙이 아니다.
        정렬 규칙(결정론적):
            - 1순위 근거(primary): 검색 점수 내림차순, 동점이면 규칙 키(위계·시행일·id).
            - 조립 근거(related): 규칙 키로만 정렬해 1순위 근거 블록 뒤에 붙인다.
        """
        if not bundle.primary and not bundle.related:
            return RankedEvidence(ordered=[], reason="근거 없음")

        def composite_key(c: LawChunk) -> tuple:
            return tuple(rule.sort_key(c) for rule in self.rules)

        # 1순위 근거: 검색 순위 유지(동점만 규칙으로)
        primary_sorted = [
            sc.chunk for sc in sorted(
                bundle.primary, key=lambda sc: (-sc.score, composite_key(sc.chunk))
            )
        ]
        primary_ids = {c.chunk_id for c in primary_sorted}
        # 조립 근거: 규칙 키로 정렬, 1순위와 중복 제거
        related_sorted = sorted(
            (c for c in bundle.related if c.chunk_id not in primary_ids),
            key=composite_key,
        )
        ordered = primary_sorted + related_sorted
        reason = self._explain(ordered, len(primary_sorted), len(related_sorted))
        return RankedEvidence(ordered=ordered, reason=reason)

    def _explain(self, ordered: list[LawChunk], n_primary: int, n_related: int) -> str:
        if not ordered:
            return "근거 없음"
        top = ordered[0]
        applied = " → ".join(r.name for r in self.rules)
        detail = ", ".join(e for r in self.rules if (e := r.explain(top)))
        parts = [
            f"최우선 근거: {top.law_name} {top.article_no}조 ({detail}).",
            f"1순위 근거 {n_primary}건은 검색 순위 유지(동점은 규칙 적용: {applied}).",
        ]
        if n_related:
            parts.append(f"조립 근거 {n_related}건은 위계·시행일 순.")
        return " ".join(parts)
