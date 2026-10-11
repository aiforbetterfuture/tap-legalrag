"""
tests/test_label_views.py — E1 세 기준(gold/final/context) 재현 고정 (작업지시서 30 P0-2).
판정 로직 불변. 라벨 정의만 다른 세 과제의 수치를 대조값으로 고정한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from conftest import requires_manual  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (ROOT / "data/eval/provenance_adjudicated.csv").exists(),
    reason="provenance_adjudicated.csv 없음",
)


def _views():
    import eval_provenance as E
    from law_rag.knowledge.loader import load_manifest
    return E.build_label_views(load_manifest())


@requires_manual
def test_test60_three_criteria():
    v = _views()["test"]
    assert [v["gold"][m]["판정정확도"] for m in ("naive", "fallback", "pgate")] == [0.617, 0.75, 0.65]
    assert [v["final"][m]["판정정확도"] for m in ("naive", "fallback", "pgate")] == [0.483, 0.617, 0.75]
    assert [v["context"][m]["판정정확도"] for m in ("naive", "fallback", "pgate")] == [0.45, 0.583, 0.717]
    assert v["gold"]["pgate"]["잘못된보류"] == "17/50"
    assert v["final"]["pgate"]["잘못된보류"] == "10/41"
    assert v["context"]["pgate"]["잘못된보류"] == "10/39"
    assert v["context"]["pgate"]["answerable_n"] == 39


@requires_manual
def test_all100_three_criteria():
    v = _views()["all"]
    assert [v["gold"][m]["판정정확도"] for m in ("naive", "fallback", "pgate")] == [0.58, 0.7, 0.67]
    assert [v["final"][m]["판정정확도"] for m in ("naive", "fallback", "pgate")] == [0.48, 0.6, 0.74]
    assert [v["context"][m]["판정정확도"] for m in ("naive", "fallback", "pgate")] == [0.45, 0.57, 0.71]
    assert v["context"]["pgate"]["answerable_n"] == 64
