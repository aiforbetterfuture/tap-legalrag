"""
tests/test_temporal.py — T1 시점 검증(낡은 조문 번호 탐지, 소규모 프로토타입).

B2 성격: 낡은 인용(POSITIVE)은 HISTORIC_VALID로 교정, 올바른 현행 인용(CONTROL)은 CURRENT_VALID.
"""
from __future__ import annotations

from pathlib import Path

from law_rag.knowledge.loader import load_corpus
from law_rag.temporal import classify, load_lineage

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _idx():
    return {c.chunk_id: c for c in load_corpus(FIXTURES, split=True)}


def test_stale_citations_that_coexist_are_ambiguous():
    """rc3: 계보상 낡은 번호가 현행에도 존재하면 단정 교정(HISTORIC) 대신 AMBIGUOUS(후보 2개).
    64:2·31:3·35:5 모두 현행 코퍼스에 존재하므로 AMBIGUOUS_HISTORIC."""
    idx, lin = _idx(), load_lineage()
    for art, para, corr in [("64", "2", "3"), ("31", "3", "4")]:
        r = classify("013933", art, para, idx, lin)
        assert r["status"] == "AMBIGUOUS_HISTORIC" and r["corrected_paragraph"] == corr
    r = classify("013933", "35", "5", idx, lin)   # M-096: 35:5 → 34:5(조 이동), 35:5도 현행 존재
    assert r["status"] == "AMBIGUOUS_HISTORIC" and r["corrected_article"] == "34"


def test_control_current_citations_valid():
    idx, lin = _idx(), load_lineage()
    # 64조3항(현행 적용제외) 올바른 인용 → CURRENT_VALID (오탐 금지)
    assert classify("013933", "64", "3", idx, lin)["status"] == "CURRENT_VALID"
    assert classify("013774", "13", "8", idx, lin)["status"] == "CURRENT_VALID"


def test_nonexistent_is_drift():
    idx, lin = _idx(), load_lineage()
    assert classify("013933", "999", "1", idx, lin)["status"] == "DRIFT_UNRESOLVED"


# ── rc3 버그 수정 회귀 ──────────────────────────────────────────
def test_rc3_article_only_citation_is_current():
    """버그1: 항으로 분할된 조를 '조만' 인용해도 그 조가 존재하면 CURRENT_VALID(DRIFT 아님)."""
    idx, lin = _idx(), load_lineage()
    # 시행령 64조는 H1~H3로 분할 → 'LAW-013933-J64' 단독 chunk_id는 없음
    assert classify("013933", "64", None, idx, lin)["status"] == "CURRENT_VALID"


def test_rc3_stale_number_also_current_is_ambiguous():
    """버그2: 낡은 번호(64:2)가 현행에도 존재 → 단정 교정(CORRECT) 금지, AMBIGUOUS로 후보 2개."""
    idx, lin = _idx(), load_lineage()
    r = classify("013933", "64", "2", idx, lin)
    assert r["status"] == "AMBIGUOUS_HISTORIC"
    assert len(r["candidates"]) == 2


def test_rc3_question_citation_extracts_paragraph_after_title():
    """버그1: '제64조(제목) 제1항'에서 괄호 제목을 건너뛰고 항까지 추출."""
    from law_rag.gate import _question_citation
    from law_rag.knowledge.loader import load_manifest
    from law_rag.provenance import resolve
    cite = _question_citation("시행령 제64조(연구개발과제 수의 제한) 제1항에 따른 과제 수는?")
    ref = resolve(cite, "시행령 제64조(연구개발과제 수의 제한) 제1항", load_manifest(), mode="pgate")
    assert ref.article == "64" and ref.paragraph == "1"
