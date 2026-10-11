"""
tests/test_examples.py — 시연 예시와 실제 출력 일치 (작업지시서 32 C9).

examples/synthetic/examples.json의 각 질문을 B4로 실제 실행해, 문서에 적은 `decision`과 같은지 검사한다.
해설서 원문을 쓰지 않으므로 공개판에서도 실행된다(시연과 실제가 어긋나지 않게 자동 고정).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "synthetic" / "examples.json"

pytestmark = pytest.mark.skipif(not EXAMPLES.exists(), reason="examples.json 없음")


def _cases():
    data = json.loads(EXAMPLES.read_text(encoding="utf-8"))
    return [(e["q"], e["decision"]) for e in data["examples"]]


@pytest.mark.parametrize("question,expected", _cases())
def test_example_decision_matches(question, expected):
    from law_rag import build_default_pipeline
    from law_rag.knowledge.loader import build_edges, load_corpus
    corpus = load_corpus(ROOT / "tests" / "fixtures", split=True)
    pipe = build_default_pipeline(corpus, build_edges(corpus))
    r = pipe.decide(question, config="B4")
    assert r["decision"] == expected, f"{question}: 문서 {expected} ≠ 실제 {r['decision']}"
