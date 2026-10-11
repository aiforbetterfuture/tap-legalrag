"""
tests/test_decision.py — E3 게이트·결정 + 결정성 + 골든 미접근.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from conftest import requires_manual  # noqa: E402
from law_rag.gate import decide_answer
from law_rag.knowledge.loader import load_corpus
from law_rag.models import Query

FIXTURES = ROOT / "tests" / "fixtures"
GOLDEN = ROOT / "tests" / "golden" / "golden_set_v3_draft_manual.csv"


def test_gate_PT_apply_to_question_citation_only():
    """§1 수정: P·T는 질문 속 인용에만. 검색된 현행 조문엔 적용 안 함."""
    corpus = load_corpus(FIXTURES, split=True)
    idx = {c.chunk_id: c for c in corpus}
    from law_rag.knowledge.loader import build_edges
    from law_rag.knowledge import Assembler, InMemoryEdgeStore
    from law_rag.reasoning import RuleEngine
    from law_rag.retrieval import Retriever
    from law_rag.router import RuleRouter
    asm = Assembler(InMemoryEdgeStore(build_edges(corpus)), idx)
    cfg = {"P": True, "T": True, "A": True, "gate": True}

    def run(text):
        q = RuleRouter().route(Query(text=text))
        scored, rel = Retriever().retrieve_with_signal(q, corpus, final_k=6)
        ranked = RuleEngine().rank(asm.assemble(scored, hops=1))
        return decide_answer(q, ranked, rel, cfg, idx)

    # 질문에 인용 없음 → P·T는 n/a 통과(스스로 CONFIRM/CORRECT 유발 안 함)
    d = run("학생인건비는 어떻게 지급하나요?")
    assert "P=n/a" in d["path"] and "T=n/a" in d["path"]
    # rc3: '낡은 번호'(64:2)가 현행에도 있으므로 단정 교정(CORRECT) 금지 → CONFIRM(후보 2개)
    d2 = run("시행령 제64조 제2항에 따른 3책5공 적용제외 대상은?")
    assert d2["decision"] == "CONFIRM"


def test_gate_force_answer_and_empty_evidence():
    idx = {}
    assert decide_answer(Query(text="x"), None, 0.5, {"force_answer": True}, idx)["decision"] == "ANSWER"
    # gate on + 근거 없음 → ABSTAIN
    class R:  # 빈 ranked
        ordered = []
    assert decide_answer(Query(text="x"), R(), 0.0, {"gate": True}, idx)["decision"] == "ABSTAIN"


@requires_manual
@pytest.mark.skipif(not GOLDEN.exists(), reason="golden 없음")
def test_e3_determinism_and_gate_effect():
    import eval_decision as E
    rows = [r for r in csv.DictReader(GOLDEN.open(encoding="utf-8-sig"))
            if r["최종판정(1차)"] not in ("REMOVE", "HOLD")]
    from law_rag.knowledge.loader import build_edges
    corpus = load_corpus(FIXTURES, split=True)
    edges = build_edges(corpus)
    idx = {c.chunk_id: c for c in corpus}
    cfgs = E._load_configs()
    # 결정성: 같은 설정 2회 → 동일
    p1 = E.run_config(rows, corpus, edges, idx, cfgs["B4"])
    p2 = E.run_config(rows, corpus, edges, idx, cfgs["B4"])
    assert p1 == p2
    # 게이트 효과: B4는 '답변 불가 문항'에 B0보다 ANSWER를 덜 고른다
    b0 = E.run_config(rows, corpus, edges, idx, cfgs["B0"])
    m0, m4 = E.metrics(rows, b0), E.metrics(rows, p1)
    a0 = int(m0["답변불가_ANSWER선택"].split("/")[0])
    a4 = int(m4["답변불가_ANSWER선택"].split("/")[0])
    assert a4 < a0
    assert m4["macro_F1"] > m0["macro_F1"]  # 균형 성능 향상


def test_decide_path_unified_pipeline_equals_eval():
    """§4: pipeline.decide()와 평가 경로(decide_for_query)가 같은 결정을 내야 한다(경로 통일)."""
    from law_rag import build_default_pipeline
    from law_rag.decide import decide_for_query
    from law_rag.knowledge.loader import build_edges
    from law_rag.router import RuleRouter
    corpus = load_corpus(FIXTURES, split=True)
    edges = build_edges(corpus)
    idx = {c.chunk_id: c for c in corpus}
    pipe = build_default_pipeline(corpus, edges)
    for qtext in ["학생인건비는 어떻게 지급하나요?", "시행령 제64조 제2항에 따른 3책5공 적용제외 대상은?"]:
        via_pipe = pipe.decide(qtext, config="B4")["decision"]
        q = RuleRouter().route(Query(text=qtext))
        via_eval = decide_for_query(q, corpus, edges, idx, "B4")["decision"]
        assert via_pipe == via_eval


def test_golden_not_read_at_runtime(monkeypatch):
    """실행 경로(파이프라인 answer)에서 골든 파일을 열면 실패. 골든=평가 전용."""
    import builtins
    real_open = builtins.open

    def guard(file, *a, **k):
        if "golden_set" in str(file):
            raise AssertionError(f"실행 중 골든 접근 금지: {file}")
        return real_open(file, *a, **k)

    from law_rag import build_default_pipeline
    from law_rag.knowledge.loader import build_edges
    corpus = load_corpus(FIXTURES, split=True)
    pipe = build_default_pipeline(corpus, build_edges(corpus))
    monkeypatch.setattr(builtins, "open", guard)
    ans = pipe.answer("연구책임자 변경은 협약변경 대상인가요?")
    assert ans.conclusion  # 골든 접근 없이 정상 동작
