"""
tests/test_p1a_acceptance.py — P1-A 품질 보완의 완료 기준.

P1-A는 P0 직후 검증에서 찾은 주의점 2~5를 고친다:
  2. 별표 ↔ 근거조문 연결(제목의 '(제N조 관련)'만 읽어 양방향 조립 복원)
  3. 확신도 과신 방지(관련도 신호가 충분할 때만 HIGH)
  4. 표기 버그 2개(인용 '별표6조'→'별표6', context_prefix '별표별표6'→'별표6')
  5. 진단 M10을 '본문 첫 문장' 기반으로 교체(여기서는 헬퍼만 스모크 테스트)
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from law_rag.governance import HIGH_RELEVANCE_MIN, Governor, TemplateGenerator
from law_rag.knowledge import Assembler, InMemoryEdgeStore
from law_rag.knowledge.loader import build_edges, load_corpus
from law_rag.models import (
    Confidence,
    EvidenceBundle,
    LawType,
    Query,
    QueryKind,
    RankedEvidence,
    ScoredChunk,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def world():
    corpus = load_corpus(FIXTURES, split=True)
    edges = build_edges(corpus)
    idx = {c.chunk_id: c for c in corpus}
    return corpus, edges, idx


# ── P1-A #2: 별표 → 근거조문 연결 (제목 기반) ────────────────────
def test_p1a_appendix_links_to_basis_article(world):
    """사용기준 별표6(간접비율, 제목 '…(제114조제2항 관련)')으로 묶음을 만들면
    근거조문(사용기준 제114조)이 조립돼야 한다(P0에서 끊겼던 연결 복원)."""
    corpus, edges, idx = world
    child = next(
        c for c in corpus
        if c.law_id == "75386" and c.article_no == "별표6" and not c.is_split_parent
    )
    asm = Assembler(InMemoryEdgeStore(edges), idx)
    bundle = asm.assemble([ScoredChunk(chunk=child, score=1.0)])
    assert any(r.law_id == "75386" and r.article_no == "114" for r in bundle.related), \
        [r.chunk_id for r in bundle.related]


# ── P1-A #4: 표기 버그 ──────────────────────────────────────────
def test_p1a_appendix_citation_has_no_jo_suffix(world):
    """별표 인용은 '별표6'이어야 한다('별표6조' 금지)."""
    _, _, idx = world
    appx = next(c for c in idx.values() if c.article_no.startswith("별표"))
    ranked = RankedEvidence(ordered=[appx], reason="x")
    cites = Governor._verified_citations(ranked)
    assert cites and cites[0].article == appx.article_no  # '별표6' 등
    assert "조" not in cites[0].article


def test_p1a_context_prefix_no_double_byeolpyo(world):
    """별표 청크의 context_prefix에 '별표별표'가 없어야 한다."""
    corpus, _, _ = world
    appx = [c for c in corpus if c.article_no.startswith("별표")]
    assert appx, "별표 청크 없음"
    for c in appx:
        assert c.context_prefix and "별표별표" not in c.context_prefix, c.context_prefix


# ── P1-A #3: 확신도 과신 방지 (메커니즘 단위 검증) ──────────────
def _high_eligible_bundle(idx) -> EvidenceBundle:
    """HeuristicScorer가 HIGH를 줄 조건(상위법 1위 + 근거 2건 + 주제일치)을 만든 번들."""
    law = next(c for c in idx.values()
               if c.law_type == LawType.법률 and not c.is_form and c.topic_tags)
    other = next(c for c in idx.values() if c.chunk_id != law.chunk_id and not c.is_form)
    return EvidenceBundle(
        primary=[ScoredChunk(chunk=law, score=1.0), ScoredChunk(chunk=other, score=0.5)]
    ), law


def test_p1a_confidence_downgraded_when_relevance_low(world):
    """관련도 신호가 임계 미만이면 HIGH를 MEDIUM으로 낮춘다(과신 방지)."""
    _, _, idx = world
    bundle, law = _high_eligible_bundle(idx)
    ranked = RankedEvidence(ordered=[sc.chunk for sc in bundle.primary], reason="x")
    gov = Governor(TemplateGenerator())
    q = Query(text="x", kind=QueryKind.search, topic=law.topic_tags[0])
    low = gov.compose(q, ranked, top_score=HIGH_RELEVANCE_MIN - 0.05)
    assert low.confidence == Confidence.MEDIUM


def test_p1a_confidence_high_when_relevance_sufficient(world):
    """관련도 신호가 충분하면 HIGH를 유지한다."""
    _, _, idx = world
    bundle, law = _high_eligible_bundle(idx)
    ranked = RankedEvidence(ordered=[sc.chunk for sc in bundle.primary], reason="x")
    gov = Governor(TemplateGenerator())
    q = Query(text="x", kind=QueryKind.search, topic=law.topic_tags[0])
    high = gov.compose(q, ranked, top_score=HIGH_RELEVANCE_MIN + 0.3)
    assert high.confidence == Confidence.HIGH


# ── P1-A #5: M10 첫 문장 헬퍼 스모크 ────────────────────────────
def test_p1a_first_sentence_strips_header():
    """진단 M10 헬퍼가 '제N조(제목)'·항번호를 떼고 첫 문장만 남기는가."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    from diagnose import _first_sentence

    body = "제11조(협약의 변경) ① 중앙행정기관의 장은 협약을 변경할 수 있다. 다음은 생략."
    s = _first_sentence(body)
    assert "제11조" not in s and "①" not in s
    assert s.startswith("중앙행정기관")
