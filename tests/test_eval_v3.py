"""
tests/test_eval_v3.py — A-1 골든 v3 평가 하니스의 '기준선 재현' 완료 기준.

작업보조 LLM 측정(P1-A 시점, 86문항)을 ±0.02로 재현해야 한다:
  R@5 ANY 0.372 · ALL 0.302 · MRR 0.217 · 완결성 0.349 → 0.401
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
GOLDEN = ROOT / "tests" / "golden" / "golden_set_v3_draft_manual.csv"

from conftest import requires_manual  # noqa: E402

pytestmark = pytest.mark.skipif(not GOLDEN.exists(), reason="golden v3 CSV 없음")

TARGET = {"R@5_ANY": 0.372, "R@5_ALL": 0.302, "MRR@10": 0.217,
          "완결성_검색만": 0.349, "완결성_조립후": 0.401}
TOL = 0.02


@requires_manual
def test_a1_reproduces_cowork_baseline():
    import eval_golden_v3 as E
    from law_rag.knowledge.loader import build_edges, load_corpus

    rows = list(csv.DictReader(GOLDEN.open(encoding="utf-8-sig")))
    active = [r for r in rows if r["최종판정(1차)"] not in ("REMOVE", "HOLD")]
    corpus = load_corpus(E.FIXTURES, split=True)
    res = E._fmt(E.evaluate(active, corpus, build_edges(corpus)))["전체"]
    assert res["n"] == 86, res["n"]
    for k, want in TARGET.items():
        assert abs(res[k] - want) <= TOL, f"{k}: {res[k]} vs {want} (±{TOL})"


def test_run_manifest_has_required_fields():
    """R1: RunManifest 최소 필드가 모두 있어야 한다(감사·재현)."""
    import eval_golden_v3 as E
    m = E.run_manifest()
    for key in ("run_id", "timestamp", "golden_sha256", "corpus_manifest_sha256",
                "code_commit", "dirty", "eval_spec_version", "retriever_config_hash",
                "llm", "embedding", "seed"):
        assert key in m, key
    assert m["eval_spec_version"] == "v1"
    assert m["llm"] is None and m["embedding"] is None
