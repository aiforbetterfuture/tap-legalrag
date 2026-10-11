"""
tests/test_check_labels.py — 라벨·데이터셋 정합성 + pending 처리 (작업지시서 30 P0-1).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

pytestmark = pytest.mark.skipif(
    not (ROOT / "data/eval/provenance_adjudicated.csv").exists(),
    reason="provenance_adjudicated.csv 없음",
)


def test_label_integrity_passes():
    import check_labels as C
    issues, _ = C.check()
    assert not issues, issues


def test_confirmed_five_reflected():
    import csv
    adj = {r["pg_id"]: r for r in csv.DictReader(
        (ROOT / "data/eval/provenance_adjudicated.csv").open(encoding="utf-8-sig"))}
    assert adj["PG-002"]["final_law"].strip() == "UNRESOLVABLE"
    assert adj["PG-003"]["final_law"].strip() == "UNRESOLVABLE"
    assert adj["PG-027"]["context_resolvable"].strip() == "Y"
    assert adj["PG-050"]["context_resolvable"].strip() == "N"
    assert adj["PG-013"]["context_resolvable"].strip() == "N"


def test_no_pending_in_eval_input():
    """pending 토큰이 평가 입력에 남아 있으면 안 된다(평가가 자동 정답·오답 처리하지 않도록)."""
    import check_labels as C
    issues, _ = C.check()
    assert not [i for i in issues if "pending" in i]
