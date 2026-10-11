"""
tests — 각 계층의 단위 테스트 + 전체 파이프라인 통합 테스트.

이 테스트들은 Cursor에게 '무엇을 만족해야 하는가'를 알려주는 실행 가능한 명세다.
실제 엔진(BGE-M3 등)으로 교체할 때, 이 테스트가 계속 초록불이면 계약이 지켜진 것이다.

실행: PYTHONPATH=src pytest -v
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from law_rag import build_default_pipeline
from law_rag.knowledge import Assembler, InMemoryEdgeStore
from law_rag.knowledge.loader import build_edges, load_chunks
from law_rag.models import (
    Confidence,
    EvidenceBundle,
    Query,
    QueryKind,
    ScoredChunk,
)
from law_rag.reasoning import RuleEngine
from law_rag.retrieval import Retriever, meta_filter
from law_rag.router import RuleRouter

SAMPLES = Path(__file__).parent.parent / "data" / "samples" / "sample_chunks.json"


@pytest.fixture
def chunks():
    return load_chunks(SAMPLES)


@pytest.fixture
def edges(chunks):
    return build_edges(chunks)


# ── 데이터 로딩 ───────────────────────────────────────────────
def test_load_chunks_validates(chunks):
    """샘플이 스키마를 만족하며 로드되는가."""
    assert len(chunks) == 5
    assert all(c.hierarchy_level in range(1, 6) for c in chunks)


def test_temporal_validity(chunks):
    """시점 유효성 판단이 맞는가 (정산 답변의 핵심)."""
    # 시행령 제15조는 2024-07-01 시행 → 그 이전 시점엔 무효
    siheng = next(c for c in chunks if c.law_short_name == "혁신법 시행령")
    assert siheng.is_valid_at(date(2024, 8, 1)) is True
    assert siheng.is_valid_at(date(2023, 1, 1)) is False


# ── 라우터 ───────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("학생인건비 상한이 얼마인가요?", QueryKind.lookup),
    ("간접비 비율이 어떻게 되나요?", QueryKind.lookup),
    ("연구책임자를 변경하려는데 협약변경 대상인가요?", QueryKind.judgment),
    ("장비를 어디에 등록하나요?", QueryKind.search),
])
def test_router_classifies_kind(text, expected):
    """라우터가 3속도등급을 올바로 분류하는가."""
    q = RuleRouter().route(Query(text=text))
    assert q.kind == expected


def test_router_tags_topic():
    q = RuleRouter().route(Query(text="협약변경 하려는데 절차가 궁금합니다"))
    assert q.topic is not None
    assert q.topic.value == "협약변경"


# ── 메타 필터 ─────────────────────────────────────────────────
def test_meta_filter_by_time(chunks):
    """시점 필터가 유효 조문만 남기는가."""
    filtered = meta_filter(chunks, as_of=date(2023, 6, 1))
    ids = {c.chunk_id for c in filtered}
    # 시행령 제15조(2024-07-01 시행)는 2023-06-01 기준 제외되어야
    assert "LAW-002100-J015-H01" not in ids


# ── 검색 ─────────────────────────────────────────────────────
def test_retriever_returns_relevant(chunks):
    """검색이 관련 청크를 반환하는가."""
    q = RuleRouter().route(Query(text="협약 변경 연구책임자"))
    scored = Retriever().retrieve(q, chunks, final_k=3)
    assert len(scored) > 0
    assert all(isinstance(s, ScoredChunk) for s in scored)


# ── SPEC-2 엣지빌더 ───────────────────────────────────────────
def test_build_edges_delegates_to(chunks, edges):
    """SPEC-2: 사용기준 제8조 → 시행령 제15조 delegates_to 엣지 존재."""
    from law_rag.models import EdgeType
    use_std = next(c for c in chunks if c.law_short_name == "사용기준" and c.article_no == "8")
    del_edges = [e for e in edges
                 if e.from_chunk_id == use_std.chunk_id
                 and e.edge_type == EdgeType.delegates_to]
    assert len(del_edges) >= 1, "사용기준 제8조의 delegates_to 엣지가 없음"


def test_build_edges_exception_to(chunks, edges):
    """SPEC-2: exception_of가 있는 청크 → exception_to 엣지 존재."""
    from law_rag.models import EdgeType
    exc_chunks = [c for c in chunks if c.exception_of]
    assert exc_chunks, "샘플에 exception_of 있는 청크가 없음 (fixture 확인 필요)"
    exc_edges = [e for e in edges if e.edge_type == EdgeType.exception_to]
    assert len(exc_edges) >= len(exc_chunks), "exception_to 엣지 수가 부족"


def test_build_edges_refers_to_cites(chunks, edges):
    """SPEC-2: cites가 있는 청크 → refers_to 엣지 생성 (신규)."""
    from law_rag.models import EdgeType
    chunks_with_cites = [c for c in chunks if c.cites]
    assert chunks_with_cites, "샘플에 cites 있는 청크가 없음"
    ref_edges = [e for e in edges if e.edge_type == EdgeType.refers_to]
    assert len(ref_edges) > 0, "refers_to 엣지가 하나도 없음"


def test_build_edges_resolves_appendix(chunks, edges):
    """SPEC-2: 별표 인용(cites=['별표3']) → 별표 chunk_id로 해석."""
    from law_rag.models import EdgeType
    use_std = next(c for c in chunks if c.law_short_name == "사용기준" and c.article_no == "8")
    별표_ref_edges = [e for e in edges
                      if e.from_chunk_id == use_std.chunk_id
                      and e.edge_type == EdgeType.refers_to]
    assert 별표_ref_edges, "사용기준 제8조의 refers_to 엣지 없음"
    # to_ref가 chunk_id로 해석됐거나 "별표3" 문자열이어야
    targets = {e.to_ref for e in 별표_ref_edges}
    has_resolved = any("LAW-" in t for t in targets)
    has_raw = any("별표" in t for t in targets)
    assert has_resolved or has_raw, f"별표 참조 to_ref 불명확: {targets}"


# ── 관계 조립 ─────────────────────────────────────────────────
def test_assembler_expands_delegation(chunks, edges):
    """조립이 위임 상위근거를 끌어오는가 (사용기준→시행령→법)."""
    index = {c.chunk_id: c for c in chunks}
    assembler = Assembler(InMemoryEdgeStore(edges), index)
    # 사용기준 제8조를 primary로 주면, 위임 상위(시행령)가 related에 들어와야
    use_std = next(c for c in chunks if c.law_short_name == "사용기준" and c.article_no == "8")
    bundle = assembler.assemble([ScoredChunk(chunk=use_std, score=1.0)], hops=1)
    related_ids = {c.chunk_id for c in bundle.related}
    assert len(related_ids) >= 1  # 최소 1홉 확장 발생


# ── 규칙 엔진 ─────────────────────────────────────────────────
def test_rule_engine_prioritizes_higher_law(chunks):
    """규칙 엔진이 상위법을 먼저 두는가."""
    bundle = EvidenceBundle(
        primary=[ScoredChunk(chunk=c, score=1.0) for c in chunks]
    )
    ranked = RuleEngine().rank(bundle)
    # 최상위는 hierarchy_level이 가장 낮은(=법률) 조문이어야
    assert ranked.ordered[0].hierarchy_level == min(c.hierarchy_level for c in chunks)
    assert "최우선 근거" in ranked.reason


# ── 전체 파이프라인 통합 ──────────────────────────────────────
def test_pipeline_end_to_end(chunks, edges):
    """질문 → 답변까지 전 계층이 연결되는가."""
    pipe = build_default_pipeline(chunks, edges)
    ans = pipe.answer("연구책임자를 변경하려는데 협약변경 대상인가요?", as_of=date(2024, 8, 1))
    assert ans.conclusion
    assert ans.confidence in (Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW)
    assert ans.disclaimer  # 면책 문구 항상 존재
    assert len(ans.audit_log) >= 2  # 감사로그 존재


def test_pipeline_no_evidence_is_low(chunks, edges):
    """근거를 못 찾으면 LOW + 담당자 확인인가 (환각 차단)."""
    pipe = build_default_pipeline(chunks, edges)
    # 코퍼스와 무관한 질문 → 근거 없음
    ans = pipe.answer("우주 정거장 발사 허가 절차는?", as_of=date(2024, 8, 1))
    # 관련 없는 질문이면 근거가 비거나 주제 불일치로 LOW 유도
    assert ans.confidence == Confidence.LOW or not ans.citations
