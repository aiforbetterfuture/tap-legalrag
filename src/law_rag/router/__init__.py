"""
law_rag.router — 질문 라우터 (통합설계 3장: 속도를 지키는 심장).

역할:
    사용자 질문을 lookup(조회) / search(검색) / judgment(판단) 으로 분류하고,
    업무주제(topic)를 태깅한다. 이 분류가 이후 처리 경로와 속도를 결정한다.

왜 이 계층이 맨 앞인가:
    모든 질문을 무거운 파이프라인에 태우면 느려져서 사용자가 일반 LLM으로 이탈한다.
    조회형은 표 즉답(1~2초), 판단형만 자기점검 루프. 평균 응답속도를 낮게 유지한다.

교체 가능성:
    Router는 추상 인터페이스다. V1은 규칙기반(RuleRouter),
    V3에서 LLM기반(LLMRouter)으로 교체해도 나머지 계층은 안 바뀐다.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod

from law_rag.models import Query, QueryKind, TopicTag


class Router(ABC):
    """질문 분류 인터페이스. 입력 Query(text) → 출력 Query(kind, topic 채워짐)."""

    @abstractmethod
    def route(self, query: Query) -> Query:
        ...


class RuleRouter(Router):
    """규칙기반 라우터 (V1). 키워드 매칭으로 빠르게 분류한다. LLM 호출 없음 → 즉시.

    분류 우선순위:
        1) 조회형 신호(상한/비율/한도/금액기준 등 '값을 묻는' 표현) → lookup
        2) 판단형 신호(가능한가요/대상인가요 등 '조건부 판단') → judgment
        3) 그 외 → search
    """

    # 조회형: 특정 수치·기준값을 묻는다 → 구조화 표 즉답이 정확하고 빠르다
    _LOOKUP_PATTERNS = [
        r"상한", r"한도", r"비율", r"얼마", r"몇\s*%", r"몇\s*퍼센트",
        r"기준\s*금액", r"금액\s*기준", r"이상.*대상", r"몇\s*원",
    ]
    # 판단형: 조건에 따라 답이 갈린다 → 되묻기·검증이 필요할 수 있다
    _JUDGMENT_PATTERNS = [
        r"가능한가요", r"가능한가", r"대상인가요", r"대상인가", r"해도\s*되나요",
        r"받아야\s*하나요", r"해야\s*하나요", r"할\s*수\s*있나요", r"되나요",
    ]

    # 업무주제 태깅용 키워드
    _TOPIC_KEYWORDS: dict[TopicTag, list[str]] = {
        TopicTag.협약변경: ["협약변경", "협약 변경", "연구책임자 변경", "기간 연장", "연구기간"],
        TopicTag.연구비집행: ["연구비", "인건비", "간접비", "직접비", "카드", "출장비", "회의비", "재료비"],
        TopicTag.정산: ["정산", "이월", "불인정", "환수", "집행잔액"],
        TopicTag.장비심의: ["장비", "심의", "시설", "기자재", "구축", "ZEUS", "등록"],
        TopicTag.성과관리: ["성과", "논문", "특허", "NTIS"],
        TopicTag.제재환수: ["제재", "참여제한", "제재부가금", "부정"],
        TopicTag.기술료: ["기술료"],
        TopicTag.보안: ["보안"],
        TopicTag.정의일반: ["정의", "무엇인가", "뜻"],
    }

    def route(self, query: Query) -> Query:
        text = query.text
        kind = self._classify_kind(text)
        topic = self._tag_topic(text)
        # Query는 불변으로 다루지 않고, 채워서 반환(흐름 명확성)
        return query.model_copy(update={"kind": kind, "topic": topic})

    def _classify_kind(self, text: str) -> QueryKind:
        if any(re.search(p, text) for p in self._LOOKUP_PATTERNS):
            return QueryKind.lookup
        if any(re.search(p, text) for p in self._JUDGMENT_PATTERNS):
            return QueryKind.judgment
        return QueryKind.search

    def _tag_topic(self, text: str) -> TopicTag | None:
        for topic, kws in self._TOPIC_KEYWORDS.items():
            if any(kw in text for kw in kws):
                return topic
        return None
