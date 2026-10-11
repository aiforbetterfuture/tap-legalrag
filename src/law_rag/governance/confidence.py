"""
law_rag.governance.confidence — 확신도 산정 (Governor에서 분리).

왜 분리했나:
    확신도 산정 방식은 논문에서 바꿔가며 실험할 대상이다
    (휴리스틱 vs LLM 자기평가 vs 근거 커버리지 기반).
    별도 인터페이스로 두면 Governor를 안 건드리고 교체·비교할 수 있다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from law_rag.models import Confidence, Query, QueryKind, RankedEvidence


class ConfidenceScorer(ABC):
    """확신도 산정 인터페이스. 입력: 질의+정렬근거. 출력: HIGH/MEDIUM/LOW."""

    @abstractmethod
    def score(self, query: Query, evidence: RankedEvidence) -> Confidence:
        ...


class HeuristicScorer(ConfidenceScorer):
    """V1 휴리스틱 산정.

    - 근거 1건 이하 → LOW
    - 주제가 상위 3개 근거 어디에도 없음 → LOW (P0-6: 주제는 추정치라 top-1만 보면 과민)
    - 최우선이 법률/시행령(위계<=2)이고 판단형이 아님 → HIGH
    - 그 외 → MEDIUM

    실제로는 LLM 자기평가·근거 커버리지로 교체(같은 인터페이스)."""

    def score(self, query: Query, evidence: RankedEvidence) -> Confidence:
        if not evidence.ordered or len(evidence.ordered) <= 1:
            return Confidence.LOW
        top = evidence.ordered[0]
        # P0-6: 주제 태그는 키워드 추정치라 top-1만 보면 과민하게 LOW가 된다.
        #       상위 3개 근거 어디에도 주제가 없을 때만 LOW로 완화한다.
        top3 = evidence.ordered[:3]
        if query.topic is not None and not any(query.topic in c.topic_tags for c in top3):
            return Confidence.LOW
        if top.hierarchy_level <= 2 and query.kind != QueryKind.judgment:
            return Confidence.HIGH
        return Confidence.MEDIUM
