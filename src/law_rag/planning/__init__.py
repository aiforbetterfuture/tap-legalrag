"""
law_rag.planning — 계획 계층 (인터페이스만, 구현은 V3).

역할(V3에서):
    검색 '전에' 검색 전략을 결정한다. 복합질문 분해, 유형별 검색기 선택,
    명확화 필요 판단 등. 즉 '무엇을 검색할지'가 아니라 '어떻게 검색할지'를 정한다.

왜 지금은 인터페이스만 두나:
    V1에 계획 로직을 넣으면 속도만 잡아먹고 측정할 골든셋도 없다.
    그러나 자리(인터페이스)를 미리 만들어 두면, V3에서 파이프라인 구조를
    바꾸지 않고 AdaptivePlanner를 끼워넣을 수 있다.
    → '값싼 준비': 지금 비용 거의 0, 나중에 확장 쉬움.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from law_rag.models import Query


@dataclass
class Plan:
    """검색 계획. V1에서는 원질의 하나만 담는 단순 구조.
    V3에서 sub_queries(분해된 하위질의), need_clarification 등이 채워진다."""
    queries: list[Query]
    need_clarification: bool = False
    clarification_slots: list[str] = field(default_factory=list)
    note: str = ""


class Planner(ABC):
    """계획 인터페이스. 입력 Query → 출력 Plan."""

    @abstractmethod
    def plan(self, query: Query) -> Plan:
        ...


class PassThroughPlanner(Planner):
    """V1 기본: 계획 없이 원질의를 그대로 통과시킨다.
    파이프라인이 Planner를 호출하지만 아무 변형도 하지 않는다.
    V3에서 AdaptivePlanner로 교체하면 같은 자리에서 계획이 활성화된다."""

    def plan(self, query: Query) -> Plan:
        return Plan(queries=[query], need_clarification=False, note="passthrough")
