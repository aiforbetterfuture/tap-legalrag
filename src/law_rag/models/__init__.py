"""
law_rag.models — 모든 계층이 공유하는 데이터 계약(contract).

설계 원칙:
    각 계층(router/retrieval/reasoning/governance)은 서로를 직접 호출하지 않고,
    이 파일에 정의된 타입으로만 데이터를 주고받는다.
    → 한 계층의 내부 구현을 바꿔도, 입력/출력 타입만 지키면 다른 계층은 안 깨진다.
    → 이것이 유지보수성의 핵심이다. 이 파일은 신중히 바꾸고, 나머지는 자유롭게 바꾼다.

이 파일을 바꿀 때 주의:
    필드를 '추가'하는 것은 대체로 안전하다.
    필드를 '삭제/이름변경'하면 그 필드를 쓰는 모든 계층을 함께 고쳐야 한다.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────
# 열거형: 시스템 전역에서 쓰는 고정 어휘. 오타로 인한 버그를 막는다.
# ─────────────────────────────────────────────────────────────
class LawType(str, Enum):
    """법령 종류. hierarchy_level과 일관되어야 한다."""
    법률 = "법률"
    대통령령 = "대통령령"
    총리령 = "총리령"
    부령 = "부령"
    고시 = "고시"
    훈령 = "훈령"
    예규 = "예규"
    기관내규 = "기관내규"


class EdgeType(str, Enum):
    """온톨로지 관계 종류. 통합설계 4장에서 정한 5종만 사용한다.
    새 관계를 추가할 때는 '실제로 조립/규칙/연구에 쓰이는가'를 먼저 확인한다."""
    delegates_to = "delegates_to"      # 위임: 법 → 시행령 → 고시
    refers_to = "refers_to"            # 인용: "제○조에 따른"
    exception_to = "exception_to"      # 예외: "~에도 불구하고"
    amends = "amends"                  # 개정: 구 조문 → 신 조문
    requires = "requires"              # 적용요건: V3 명확화 연구용


class QueryKind(str, Enum):
    """라우터가 판정하는 질문 유형. 통합설계 3장의 3속도등급.
    lookup=조회형(즉답), search=검색형(표준RAG), judgment=판단형(자기점검 루프)."""
    lookup = "lookup"
    lookup_conditional = "lookup_conditional"  # 값이 기관유형·조건에 따라 갈림(A-5)
    search = "search"
    judgment = "judgment"


class Confidence(str, Enum):
    """답변 확신도 3단계. LOW면 담당자 확인을 자동 안내한다."""
    HIGH = "HIGH"      # 조문에 명시
    MEDIUM = "MEDIUM"  # 해석 필요
    LOW = "LOW"        # 근거 부족 → 담당자 확인


class TopicTag(str, Enum):
    """업무주제 태그. 문의유형 분류표의 대분류와 연동. 메타필터·라우팅에 사용."""
    협약변경 = "협약변경"
    연구비집행 = "연구비집행"
    정산 = "정산"
    장비심의 = "장비심의"
    성과관리 = "성과관리"
    제재환수 = "제재환수"
    기술료 = "기술료"
    보안 = "보안"
    정의일반 = "정의일반"


# ─────────────────────────────────────────────────────────────
# 핵심 저장 단위: LawChunk (law_chunk.schema.json의 파이썬 표현)
# ─────────────────────────────────────────────────────────────
class LawChunk(BaseModel):
    """원자적 저장 단위(조 또는 항). Knowledge Layer의 산출물이자
    Retrieval Layer의 입력. law_chunk.schema.json과 1:1 대응한다."""
    chunk_id: str = Field(..., description="PK. 조문: LAW-{law_id}-J{조}[-H{항}], 별표: LAW-{law_id}-B{번호}")
    law_id: str
    law_name: str
    law_short_name: Optional[str] = None
    law_type: LawType
    hierarchy_level: int = Field(..., ge=1, le=5, description="1법률>2시행령>3시행규칙>4고시>5내규")
    binding_power: int = Field(..., ge=1, le=5, description="법적 구속력 별점(UI 신뢰도 배지)")
    article_no: str = Field(..., description="조 번호. 가지번호('12의2')·별표('별표3') 포함")
    article_title: str
    paragraph_no: Optional[str] = None
    chapter_section: Optional[str] = None
    promulgation_date: date
    enforce_date: date
    article_enforce_date: date = Field(..., description="이 조문의 시행일. 시점검색 핵심")
    is_repealed: bool = False
    valid_from: date
    valid_to: Optional[date] = Field(None, description="None이면 현행")
    topic_tags: list[TopicTag] = Field(..., min_length=1)
    parent_law_ref: Optional[str] = Field(None, description="위임 상위근거(고시→법)")
    cites: list[str] = Field(default_factory=list, description="인용 조문/별표")
    exception_of: Optional[str] = None
    context_prefix: Optional[str] = Field(None, description="임베딩 앞에 붙이는 문맥문장")
    body_text: str = Field(..., description="조문 원문. 답변 생성 시 LLM에 전달. 손상 금지")
    parent_chunk_id: Optional[str] = None
    source_url: str
    version_hash: Optional[str] = None
    is_form: bool = Field(False, description="별지/서식 여부. True면 기본 검색에서 제외(별표구분=별지·서식).")
    is_split_parent: bool = Field(False, description="대형 별표의 원본(분할 전 전체). True면 기본 검색에서 제외. 자식(-S{n})으로 검색하고 부모로 전체 확인.")
    applies_to: Optional[str] = Field(None, description="적용 기관유형(A-2 레지스트리). 예: 영리기관·정부출연기관·대학·기타비영리기관. 로더가 채움.")

    def is_valid_at(self, ref: date) -> bool:
        """기준 시점 ref에 이 조문이 유효한가? (시점 필터의 판단 함수)"""
        if self.is_repealed and self.valid_to and ref > self.valid_to:
            return False
        if ref < self.valid_from:
            return False
        if self.valid_to is not None and ref > self.valid_to:
            return False
        return True


# ─────────────────────────────────────────────────────────────
# 관계 그래프 엣지
# ─────────────────────────────────────────────────────────────
class Edge(BaseModel):
    """관계 그래프의 엣지 하나. edges 테이블의 한 행."""
    from_chunk_id: str
    to_ref: str = Field(..., description="도착: 조문 참조 문자열 또는 chunk_id")
    edge_type: EdgeType


# ─────────────────────────────────────────────────────────────
# 질의·검색·답변 흐름 타입
# ─────────────────────────────────────────────────────────────
class Query(BaseModel):
    """사용자 질의 + 라우팅/필터 정보. 계층 사이를 흐르는 요청 객체."""
    text: str
    kind: Optional[QueryKind] = Field(None, description="라우터가 채운다")
    topic: Optional[TopicTag] = Field(None, description="라우터가 채운다")
    as_of: Optional[date] = Field(None, description="기준 시점(협약연도 등). None이면 현행")


class ScoredChunk(BaseModel):
    """검색 결과 청크 + 점수. 리랭킹 전후로 쓰인다."""
    chunk: LawChunk
    score: float


class EvidenceBundle(BaseModel):
    """관계 조립으로 만들어진 근거 번들. Reasoning Layer의 입력.
    primary=검색으로 직접 찾은 청크, related=그래프로 끌어온 상위근거/인용."""
    primary: list[ScoredChunk]
    related: list[LawChunk] = Field(default_factory=list)
    links: list[tuple[str, str, str]] = Field(
        default_factory=list,
        description="조립 경로: (1순위근거 chunk_id, 딸려온 chunk_id, 관계명). 감사로그·UI 근거경로용",
    )

    def all_chunks(self) -> list[LawChunk]:
        seen: dict[str, LawChunk] = {}
        for sc in self.primary:
            seen[sc.chunk.chunk_id] = sc.chunk
        for c in self.related:
            seen.setdefault(c.chunk_id, c)
        return list(seen.values())


class RankedEvidence(BaseModel):
    """규칙 엔진이 위계·시행일로 정렬한 근거. Governance/생성 Layer의 입력.
    ordered[0]이 최우선 근거. reason은 왜 이 순서인지(감사로그용)."""
    ordered: list[LawChunk]
    reason: str = Field(..., description="정렬 근거 설명(감사로그)")


class Citation(BaseModel):
    """답변에 포함되는 인용 하나."""
    law_name: str
    article: str = Field(..., description="예: '제12조제2항'")
    enforce_date: date
    source_url: str


class GuideChunk(BaseModel):
    """매뉴얼 '해설' 청크 (M-1). 법령(LawChunk)과 엄격히 분리된 별도 타입.

    왜 별도 타입인가:
        매뉴얼·FAQ는 '법적 효력이 없는 보조 설명'이다. 법령 Citation과 절대 섞이면 안 된다.
        타입을 분리해 두면 검색·인용 경로에서 해설이 법령 근거로 둔갑하는 것을 구조적으로 막는다.
    """
    guide_id: str = Field(..., description="MAN-{문서}-{번호}")
    doc_title: str
    edition: str = Field(..., description="예: '2026.07'")
    section_path: Optional[str] = Field(None, description="장 > 절 > 항목")
    kind: str = Field(..., description="해설 | QA | 사례")
    text: str
    question: Optional[str] = Field(None, description="QA의 질문부(R2)")
    answer: Optional[str] = Field(None, description="QA의 답변부(R2)")
    page_start: int
    page_end: int
    cited_refs: list[str] = Field(default_factory=list, description="본문이 인용한 조문 주소")
    sha256: Optional[str] = None
    legal_effect: bool = Field(False, description="항상 False — 법적 효력 없음(보조 근거)")
    drift_warnings: list[str] = Field(
        default_factory=list,
        description="낡은 조문 번호 인용 경고(A-3). 예: 매뉴얼 인용 '시행령 제64조제2항'은 현행 제3항으로 보임",
    )


class Answer(BaseModel):
    """최종 답변. Governance Layer를 통과한 출력."""
    conclusion: str = Field(..., description="한 줄 결론")
    body: str = Field(..., description="7단 템플릿 본문")
    citations: list[Citation]
    confidence: Confidence
    needs_human_review: bool = Field(False, description="True면 담당자 확인 안내 표시")
    disclaimer: str = "본 답변은 참고용이며 최종 판단은 전문기관 담당자 확인을 거쳐야 합니다."
    audit_log: list[str] = Field(default_factory=list, description="처리 과정 추적(설명가능성)")
    guide_notes: list[str] = Field(
        default_factory=list,
        description="매뉴얼 해설(법적 효력 없음). use_guide=True일 때만 채움. Citation에는 절대 섞지 않는다.",
    )
    clarification: dict = Field(
        default_factory=dict,
        description="명확화 정책(A-5). {policy: NONE|ASSUME|BRANCH|ASK, slot, question, branches[]}",
    )
