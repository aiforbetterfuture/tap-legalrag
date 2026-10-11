"""
tests/test_bm25.py — E3 B0용 '실제 BM25' 기준선 (순수 파이썬, 새 의존성 없음).
"""
from __future__ import annotations

from pathlib import Path

from law_rag.knowledge.loader import load_corpus
from law_rag.retrieval import BM25Searcher, Searcher, meta_filter

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_bm25_is_searcher_and_scores_positive():
    corpus = load_corpus(FIXTURES, split=True)
    bm = BM25Searcher()
    assert isinstance(bm, Searcher)
    res = bm.search("연구책임자 변경 협약", meta_filter(corpus), 5)
    assert res and all(r.score > 0 for r in res)
    # 점수 내림차순
    assert [r.score for r in res] == sorted((r.score for r in res), reverse=True)


def test_bm25_idf_downweights_common_terms():
    """흔한 토큰만 겹치는 문서보다, 희귀 토큰이 겹치는 문서가 더 높게 나와야 한다."""
    corpus = load_corpus(FIXTURES, split=True)
    res = BM25Searcher().search("간접비 비율", meta_filter(corpus), 3)
    # 간접비 관련 사용기준 조문이 상위
    assert any(r.chunk.law_id == "75386" for r in res)
