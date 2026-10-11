"""
tests/test_applicability.py — A-2 적용요건 레지스트리 완료 기준.

  - 준용 검출: 54→36, 55→37, 59→43, 49→40, 45→34~44
  - 블록별 applies_to 개수(영리·정부출연·대학·기타비영리)
  - 로더가 LawChunk.applies_to를 채움
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from law_rag.knowledge.loader import load_corpus

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
REG = ROOT / "data" / "registry" / "applicability.json"

pytestmark = pytest.mark.skipif(not REG.exists(), reason="applicability.json 없음 — build_applicability 먼저 실행")


@pytest.fixture(scope="module")
def reg():
    return json.loads(REG.read_text(encoding="utf-8"))


@pytest.mark.parametrize("frm,expect", [
    ("54", ["36"]), ("55", ["37"]), ("59", ["43"]), ("49", ["40"]),
    ("45", [str(n) for n in range(34, 45)]),
])
def test_junyong_detection(reg, frm, expect):
    j = {x["from"]: x for x in reg["준용"]}
    assert frm in j, f"{frm}조 준용 미검출"
    assert j[frm]["to"] == expect, (j[frm]["to"], expect)


def test_junyong_substitute(reg):
    j = {x["from"]: x for x in reg["준용"]}
    assert j["54"]["substitute"] == {"정부출연기관": "기타비영리기관"}


def test_applies_to_blocks(reg):
    ap = reg["applies_to"]["75386"]
    # 블록별 대표 조가 올바른 기관유형으로 분류
    assert ap.get("15") == "영리기관" and ap.get("34") == "정부출연기관"
    assert ap.get("45") == "대학" and ap.get("52") == "기타비영리기관"
    from collections import Counter
    dist = Counter(ap.values())
    assert dist["영리기관"] >= 10 and dist["정부출연기관"] >= 5
    assert dist["대학"] >= 5 and dist["기타비영리기관"] >= 5


def test_loader_fills_applies_to():
    corpus = load_corpus(FIXTURES, split=True)
    by = {(c.law_id, c.article_no): c for c in corpus}
    assert by[("75386", "34")].applies_to == "정부출연기관"
    assert by[("75386", "45")].applies_to == "대학"
    # 적용요건 없는 법령(혁신법)은 None
    assert by[("013774", "2")].applies_to is None
