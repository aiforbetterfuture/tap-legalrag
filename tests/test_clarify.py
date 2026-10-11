"""
tests/test_clarify.py — A-5 명확화 정책 v0 완료 기준.

  - M-172=NONE, M-246=BRANCH, M-063=ASK (3문항 정책 일치)
  - Answer.clarification이 채워지고 policy는 NONE/ASSUME/BRANCH/ASK 중 하나
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from law_rag import PipelineBuilder
from law_rag.clarify import decide
from law_rag.knowledge.loader import build_edges, load_corpus
from law_rag.models import Query
from law_rag.retrieval import Retriever
from law_rag.router import RuleRouter

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
GOLDEN = ROOT / "tests" / "golden" / "golden_set_v3_draft_manual.csv"

from conftest import requires_manual  # noqa: E402


@requires_manual
@pytest.mark.skipif(not GOLDEN.exists(), reason="golden v3 없음")
@pytest.mark.parametrize("qid,expect", [("M-172", "NONE"), ("M-246", "BRANCH"), ("M-063", "ASK")])
def test_a5_policy_targets(qid, expect):
    rows = {r["id"]: r for r in csv.DictReader(GOLDEN.open(encoding="utf-8-sig"))}
    corpus = load_corpus(FIXTURES, split=True)
    router, retriever = RuleRouter(), Retriever()
    q = router.route(Query(text=rows[qid]["질문"]))
    scored, _ = retriever.retrieve_with_signal(q, corpus, final_k=5)
    assert decide(q, [s.chunk for s in scored])["policy"] == expect


def test_a5_pipeline_sets_clarification():
    corpus = load_corpus(FIXTURES, split=True)
    pipe = PipelineBuilder(corpus, build_edges(corpus)).build()
    ans = pipe.answer("비영리기관의 간접비 적용 방법은?", as_of=date(2026, 10, 6))
    assert ans.clarification.get("policy") in ("NONE", "ASSUME", "BRANCH", "ASK")
