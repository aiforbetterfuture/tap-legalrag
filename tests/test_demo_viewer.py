"""
tests/test_demo_viewer.py — 시연 화면이 실제 판정 결과와 같은지 (작업지시서 34 §B-2).
build_demo_viewer.collect()의 7건 결정이 examples.json과 같고, 생성 HTML에 미치환 자리표시자가 없는지.
공개판에서도 실행된다(해설서 원문 불필요).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
EXAMPLES = ROOT / "examples" / "synthetic" / "examples.json"

pytestmark = pytest.mark.skipif(not EXAMPLES.exists(), reason="examples.json 없음")


def test_collect_matches_examples():
    import build_demo_viewer as B
    expected = [(e["q"], e["decision"]) for e in json.loads(EXAMPLES.read_text(encoding="utf-8"))["examples"]]
    rows = B.collect()
    got = [(r["q"], r["result"]["decision"]) for r in rows]
    assert got == expected, got


def test_viewer_html_has_no_placeholder():
    viewer = ROOT / "docs" / "demo" / "viewer.html"
    if not viewer.exists():
        pytest.skip("viewer.html 미생성(개발판에서 build_demo_viewer 실행 필요)")
    text = viewer.read_text(encoding="utf-8")
    assert "/*__DATA__*/" not in text, "자리표시자가 치환되지 않음"
