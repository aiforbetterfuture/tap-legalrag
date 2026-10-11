"""
tests/test_human_review.py — 사람 검수 R1(100건) 반영 재현 (작업지시서 26 §2).
동결 수치·판정 로직을 바꾸지 않고, 작업보조 LLM 계산을 재현 확인한다.
"""
from __future__ import annotations

import csv as _csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
REVIEW = ROOT / "data" / "eval" / "provenance_review_R1.csv"

from conftest import requires_manual  # noqa: E402

pytestmark = pytest.mark.skipif(not REVIEW.exists(), reason="provenance_review_R1.csv 없음")


def _load():
    rv = list(_csv.DictReader(REVIEW.open(encoding="utf-8-sig")))
    gold = {r["pg_id"]: r for r in _csv.DictReader(
        (ROOT / "data/eval/provenance_gold.csv").open(encoding="utf-8-sig"))}
    return rv, gold


def test_agreement_subsets_reproduce():
    import eval_human_review as H
    rv, gold = _load()
    agr = H._agreement(rv, gold)
    assert (agr["전체"]["엄격"], agr["전체"]["완화"]) == (74, 81)
    assert (agr["test"]["엄격"], agr["test"]["완화"]) == (44, 48)
    assert (agr["dev"]["엄격"], agr["dev"]["완화"]) == (30, 33)
    assert agr["열=재계산"], {k: agr[k]["불일치_문항"] for k in ("전체", "test", "dev")}


def test_judgment_basis_reproduce():
    import eval_human_review as H
    rv, _ = _load()
    b = H._by_judgment_basis(rv)
    assert b["인용 안에 법령명"] == {"n": 47, "엄격": 47}
    assert b["앞 문장의 법령명"] == {"n": 18, "엄격": 13}
    assert b["근거 없음(판단 불가)"] == {"n": 14, "엄격": 1}


@requires_manual
def test_wrong_withhold_17_human_confirmed():
    import eval_human_review as H
    from law_rag.knowledge.loader import load_manifest
    rv, _ = _load()
    ww = H._wrong_withhold_ids(load_manifest())
    assert len(ww) == 17
    assert ww == set(H.CONFIRMED_GROUP1) | set(H.CONFIRMED_GROUP2)  # 17 = ①10 ∪ ②7
    g1_ok, g2_na = H._human_confirm_17(rv)
    assert g1_ok == 10   # ① 사람이 10/10 올바른 법령으로 판단(시스템 결함 확정)
    assert g2_na == 7    # ② 사람도 7/7 판단 불가(정답 정의 문제)


@requires_manual
def test_sensitivity_reproduces():
    import eval_human_review as H
    from law_rag.knowledge.loader import load_manifest
    s = H._sensitivity(load_manifest())
    assert s["fallback"]["민감도_판정정확도"] == 0.75
    assert s["pgate"]["원정의_판정정확도"] == 0.65
    # 사람 확인 ②(7건, PG-025 포함)로 재분류 → pgate 0.767(fallback 근소 우위)
    assert s["pgate"]["민감도_판정정확도"] == 0.767
