"""
tests/test_provenance.py — E1 출처 검증 (P 게이트).

  - 무한정어 기본 귀속 금지: 맥락 없는 bare 인용은 pgate에서 UNRESOLVED, default에서만 013774.
  - 법령명/별칭 인용은 올바른 법령으로 확정.
  - E1 대비: pgate 오귀속률 < default, pgate RESOLVED 정밀도 ≥ default (test 분할).
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from conftest import requires_manual  # noqa: E402
from law_rag.knowledge.loader import load_manifest
from law_rag.provenance import resolve

GOLD = ROOT / "data" / "eval" / "provenance_gold.csv"


def test_bare_without_context_not_attributed_in_pgate():
    m = load_manifest()
    assert resolve("제5조", "", m, mode="pgate").status == "UNRESOLVED"
    r = resolve("제5조", "", m, mode="default")  # default는 fallback 별칭
    assert r.status == "RESOLVED" and r.law_id == "013774"


def test_naive_ignores_context_and_external():
    """naive: 인용에 법령명 없으면 문맥·외부 감지 없이 무조건 013774."""
    m = load_manifest()
    # 문맥이 시행령을 가리켜도 naive는 013774
    assert resolve("제5조", "시행령 제5조에 따라", m, mode="naive").law_id == "013774"
    # 외부 법령 맥락이어도 naive는 013774(외부 감지 없음)
    assert resolve("제2조", "「고등교육법」 제2조", m, mode="naive").law_id == "013774"


def test_named_citation_resolves():
    m = load_manifest()
    assert resolve("사용기준 제73조", "", m, mode="pgate").law_id == "75386"
    assert resolve("「국가연구개발혁신법 시행령」 제14조", "", m, mode="pgate").law_id == "013933"


def test_external_citation_withheld():
    m = load_manifest()
    r = resolve("제2조", "「고등교육법」 제2조에 따른", m, mode="pgate")
    assert r.status == "UNRESOLVED" and r.is_external


@requires_manual
@pytest.mark.skipif(not GOLD.exists(), reason="provenance_gold.csv 없음")
def test_e1_pgate_reduces_misattribution():
    import eval_provenance as E
    rows = [r for r in csv.DictReader(GOLD.open(encoding="utf-8-sig")) if r["split"] == "test"]
    m = load_manifest()
    d = E.evaluate(rows, m, "fallback")
    p = E.evaluate(rows, m, "pgate")
    assert p["오귀속률"] < d["오귀속률"]          # 오귀속 감소
    assert p["RESOLVED정밀도"] >= d["RESOLVED정밀도"]  # 확정 시 정밀도 향상


@requires_manual
def test_e1_transition_fallback_to_pgate_count():
    """fallback이 '혁신법 기본귀속'한 문항의 pgate 전환 합계(test=18) 고정."""
    import eval_provenance as E
    rows = [r for r in csv.DictReader(GOLD.open(encoding="utf-8-sig")) if r["split"] == "test"]
    t = E.transition_table(rows, load_manifest())
    assert sum(v["n"] for v in t.values()) == 18, t
