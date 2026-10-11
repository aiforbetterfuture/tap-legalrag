"""
law_rag.governance — 통제 계층 (통합설계 4장 ④).

역할:
    답변 생성을 감독한다. 시스템 정책 부과, 인용 검증(환각 차단),
    확신도 산정, 면책 문구, 감사 로그.

왜 독립 계층인가:
    공공기관 AI에서 인용검증·확신도·감사는 '기능'이 아니라 책임 요건이다.
    답변 생성에 섞지 않고 분리하면 '이 답이 왜 나왔나'를 언제든 추적할 수 있다.

LLM 경계:
    답변 문장 생성만 LLM이 한다(AnswerGenerator 인터페이스).
    확신도·인용검증·정렬은 코드가 한다 → 테스트 가능, 환각 차단.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from law_rag.governance.confidence import ConfidenceScorer, HeuristicScorer
from law_rag.models import (
    Answer,
    Citation,
    Confidence,
    Query,
    RankedEvidence,
)
from law_rag.prompts import load as load_prompt


# P1-A: HIGH(확신도 '높음')을 줄 수 있는 최소 관련도 신호.
# 1순위 근거가 상위법이라도 질문과의 관련도가 낮으면 '높음'은 위험하다(과신 방지).
HIGH_RELEVANCE_MIN = 0.35  # TODO(golden): 골든셋으로 조정


def _system_policy() -> str:
    """시스템 정책을 prompts/system_policy.txt에서 읽는다.
    프롬프트를 코드에서 분리 → 수정·버전관리·논문부록 활용이 쉽다."""
    return load_prompt("system_policy")


# 하위호환: 기존에 SYSTEM_POLICY 상수를 참조하던 코드를 위해 지연 로드 제공
SYSTEM_POLICY = _system_policy()


class AnswerGenerator(ABC):
    """답변 문장 생성 인터페이스. 실제로는 상용/국산 LLM으로 구현.
    입력: 질의 + 정렬된 근거 + 시스템정책. 출력: (결론, 본문)."""

    @abstractmethod
    def generate(self, query: Query, evidence: RankedEvidence, policy: str) -> tuple[str, str]:
        ...


class TemplateGenerator(AnswerGenerator):
    """V1 참조구현: LLM 없이 템플릿으로 답변을 조립한다.
    LLM 연동 전에 전체 파이프라인을 검증할 수 있게 한다.
    실제로는 이 자리에 LLM 호출을 넣되, 반환 형식(결론, 본문)만 지키면 된다."""

    def generate(self, query: Query, evidence: RankedEvidence, policy: str) -> tuple[str, str]:
        if not evidence.ordered:
            return ("근거를 찾지 못했습니다.", "관련 조문을 검색하지 못했습니다. 담당자 확인이 필요합니다.")
        top = evidence.ordered[0]
        conclusion = f"[{top.law_name} {top.article_no}조] 기준으로 검토가 필요합니다."
        body_lines = [
            "■ 근거",
            f"- {top.law_name} {top.article_no}조({top.article_title}), 시행 {top.article_enforce_date}",
            f"  {top.body_text[:120]}...",
            "",
            "■ 정렬 근거(감사)",
            f"- {evidence.reason}",
        ]
        return (conclusion, "\n".join(body_lines))


class Governor:
    """통제 계층 오케스트레이터(코디네이터). 생성 → 인용검증 → 확신도 → 면책 → 감사로그.

    확신도 산정은 주입받은 ConfidenceScorer에 위임한다(책임 분리).
    Governor 자신은 '순서를 조율'만 하고, 각 판단은 전문 부품이 한다."""

    def __init__(self, generator: AnswerGenerator, scorer: ConfidenceScorer | None = None):
        self.generator = generator
        self.scorer = scorer or HeuristicScorer()

    def compose(
        self,
        query: Query,
        evidence: RankedEvidence,
        *,
        top_score: float | None = None,
        min_score: float = 0.15,
    ) -> Answer:
        """근거를 종합해 최종 답변을 만든다.

        top_score: 검색 최상위 관련도 점수(선택). 주어지고 min_score 미만이면
                   '근거 없음'으로 간주한다 → 무관한 질문에 억지 답변 방지(환각 차단).
        """
        audit: list[str] = []
        audit.append(f"질의유형={query.kind.value if query.kind else '미분류'}")
        audit.append(f"근거 조문 수={len(evidence.ordered)}")

        # 관련도가 임계 미만이면 근거가 있어도 '없음'으로 처리 (방어선)
        if top_score is not None and top_score < min_score:
            audit.append(f"최상위 관련도 {top_score:.3f} < 임계 {min_score} → 근거 불충분")
            return Answer(
                conclusion="질문과 충분히 관련된 근거를 찾지 못했습니다.",
                body="검색된 조문의 관련도가 낮습니다. 질문을 구체화하거나 담당 부서 확인을 권장합니다.",
                citations=[],
                confidence=Confidence.LOW,
                needs_human_review=True,
                audit_log=audit,
            )

        # 근거 없음 → 즉시 Low + 담당자 확인 (환각 차단의 첫 관문)
        if not evidence.ordered:
            audit.append("근거 없음 → LOW, 담당자 확인")
            return Answer(
                conclusion="답변을 위한 근거를 찾지 못했습니다.",
                body="관련 법령 조문을 확인하지 못했습니다. 담당 부서 확인을 권장합니다.",
                citations=[],
                confidence=Confidence.LOW,
                needs_human_review=True,
                audit_log=audit,
            )

        # 1) 답변 생성 (LLM 경계)
        conclusion, body = self.generator.generate(query, evidence, SYSTEM_POLICY)
        audit.append("답변 생성 완료")

        # 2) 인용 검증: 근거에 실제 존재하는 조문만 Citation으로 (환각 차단)
        citations = self._verified_citations(evidence)
        audit.append(f"검증된 인용 수={len(citations)}")

        # 3) 확신도 산정
        confidence = self.scorer.score(query, evidence)
        # P1-A 과신 방지: 관련도 신호가 낮으면 HIGH를 MEDIUM으로 낮춘다.
        #   상위법 조문이 1위여도 질문과 겉돌면 '높음'은 공공기관에서 가장 위험한 실패다.
        if confidence == Confidence.HIGH and (
            top_score is None or top_score < HIGH_RELEVANCE_MIN
        ):
            audit.append(
                f"과신 방지: 관련도 {top_score if top_score is None else round(top_score, 3)}"
                f" < {HIGH_RELEVANCE_MIN} → HIGH를 MEDIUM으로"
            )
            confidence = Confidence.MEDIUM
        audit.append(f"확신도={confidence.value}")

        needs_review = confidence == Confidence.LOW

        return Answer(
            conclusion=conclusion,
            body=body,
            citations=citations,
            confidence=confidence,
            needs_human_review=needs_review,
            audit_log=audit,
        )

    @staticmethod
    def coverage_miss_answer(as_of, coverage_lines: list[str]) -> Answer:
        """기준일이 보유 판본 범위 밖일 때의 정직한 답변 (P0-5).

        '근거 없음'이 아니라 '그 시점 판본 미보유'임을 명시한다."""
        lines = [
            "■ 보유 판본(법령별 시행일)",
            *[f"- {ln}" for ln in coverage_lines],
            "",
            "■ 안내",
            "- 요청하신 기준일 시점의 과거 판본은 아직 수집되어 있지 않습니다.",
            "- 국가법령정보센터(law.go.kr)에서 기준일을 지정해 확인하시기 바랍니다.",
        ]
        return Answer(
            conclusion=f"요청하신 기준일({as_of}) 시점의 법령 판본을 보유하고 있지 않습니다.",
            body="\n".join(lines),
            citations=[],
            confidence=Confidence.LOW,
            needs_human_review=True,
            audit_log=[f"판본 미보유: as_of={as_of} < 보유 시작(모든 법령)"],
        )

    @staticmethod
    def _verified_citations(evidence: RankedEvidence) -> list[Citation]:
        """근거로 실제 쓰인 조문만 인용으로 만든다. 지어낸 인용을 원천 차단."""
        cites = []
        for c in evidence.ordered[:5]:
            # 별표는 이미 '별표6' 형태 → '조' 접미 금지(표기버그 수정). 조문만 '제N조'.
            article = c.article_no if c.article_no.startswith("별표") else f"제{c.article_no}조"
            if c.paragraph_no:
                article += f"제{c.paragraph_no}항"
            cites.append(
                Citation(
                    law_name=c.law_name,
                    article=article,
                    enforce_date=c.article_enforce_date,
                    source_url=c.source_url,
                )
            )
        return cites

