"""
tests/test_mcp_server.py — TAP MCP 도구가 기존 결정 경로의 얇은 포장인지 확인 (작업지시서 24 §C).
mcp 미설치면 전체 skip. 설치면: 도구 3개 등록·스키마 존재·결과가 decide_question과 동일.
"""
from __future__ import annotations

import pytest

pytest.importorskip("mcp", reason="mcp 미설치 — 선택 의존성 [mcp]")

from law_rag import mcp_server as S  # noqa: E402


def test_three_tools_registered():
    import asyncio
    tools = asyncio.run(S.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {"tap_decide", "tap_resolve_citation", "tap_check_currency"}
    for t in tools:  # 스키마·설명 존재
        assert t.description and t.inputSchema


def test_tap_decide_matches_pipeline():
    q = "시행령 제64조 제2항은 무엇을 규정하나요?"
    direct = S._ctx()[0].decide(q, config="B4")
    viatool = S.tap_decide(q, config="B4")
    assert viatool["decision"] == direct["decision"]
    assert viatool["path"] == direct["path"]
    assert viatool["audit"].startswith("[")


def test_tap_resolve_citation_pgate():
    out = S.tap_resolve_citation("제64조 제2항", context="「국가연구개발혁신법 시행령」 제64조 제2항")
    assert out["status"] in {"RESOLVED", "PARTIAL", "AMBIGUOUS", "UNRESOLVED"}
    assert out["article"] == "64" and out["paragraph"] == "2"


def test_tap_check_currency_returns_status():
    out = S.tap_check_currency("013933", "64", "2")
    assert out["status"] in {"CURRENT_VALID", "HISTORIC_VALID",
                             "AMBIGUOUS_HISTORIC", "DRIFT_UNRESOLVED"}
    assert "audit" in out
