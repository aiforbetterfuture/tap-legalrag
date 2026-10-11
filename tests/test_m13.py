"""
tests/test_m13.py — A-4 진단 M13 판본 간 참조 정합성.

다른 법령의 '항'을 가리키는 참조에서 "제X조제Y항에 따른 ○○"의 ○○가 대상 항 본문에 없으면 WARN.
완료 기준: 시행령 26조 → 법 13조7항 '정산' 불일치를 검출(지금은 WARN, FAIL 아님).
"""
from __future__ import annotations

import re
from pathlib import Path

from law_rag.knowledge.loader import build_edges, load_corpus

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _m13_warnings(corpus, edges):
    idx = {c.chunk_id: c for c in corpus}
    warns = []
    for e in edges:
        if e.edge_type.value not in ("refers_to", "delegates_to"):
            continue
        src, tgt = idx.get(e.from_chunk_id), idx.get(e.to_ref)
        if not src or not tgt or tgt.law_id == src.law_id or not tgt.paragraph_no:
            continue
        m = re.search(rf"제{tgt.article_no}조\s*제{tgt.paragraph_no}항에\s*따[른라]\s*([가-힣]{{2,6}})", src.body_text)
        if not m:
            continue
        noun = re.sub(r"(으로|로서|로써|에서|에게|를|을|이|가|은|는|의|에|로|와|과|및)$", "", m.group(1))
        if len(noun) >= 2 and noun not in tgt.body_text:
            warns.append((src.law_id, src.article_no, tgt.law_id, tgt.article_no, tgt.paragraph_no, noun))
    return warns


def test_m13_detects_decree26_to_act13_7_jeongsan():
    corpus = load_corpus(FIXTURES, split=True)
    warns = _m13_warnings(corpus, build_edges(corpus))
    # 시행령(013933) 26조 → 법(013774) 13조7항 '정산' 불일치가 포함돼야 한다
    hit = [w for w in warns if w[0] == "013933" and w[1] == "26"
           and w[2] == "013774" and w[3] == "13" and w[4] == "7" and "정산" in w[5]]
    assert hit, f"정산 불일치 미검출. warns 예: {warns[:3]}"
