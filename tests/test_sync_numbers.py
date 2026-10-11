"""
tests/test_sync_numbers.py — 문서 숫자 자동 대조 (작업지시서 24 §A-5).
문서(RESULTS·HUMAN_REVIEW)의 핵심 수치가 결과 JSON과 다르면 실패.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

pytestmark = pytest.mark.skipif(
    not (ROOT / "results" / "e3_decision_rc3.json").exists(),
    reason="결과 파일 없음 — eval 먼저 실행",
)


def test_docs_numbers_match_results():
    import sync_numbers as S
    _, missing = S.check()
    assert not missing, f"문서-결과 수치 불일치: {missing}"
