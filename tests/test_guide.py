"""
tests/test_guide.py — 매뉴얼 '해설' 코퍼스의 완료 기준 (M).

  - 본권 QA 300개 이상 추출
  - 모든 청크에 section_path와 쪽수 존재
  - cited_refs가 코퍼스 주소로 해석됨(실패율 출력, 100% 요구는 아님)
  - use_guide=False일 때 기존 답변과 완전히 동일
  - 해설이 법령 Citation에 섞이지 않음
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from law_rag import PipelineBuilder
from law_rag.guide import GuideRetriever, load_guide_chunks
from law_rag.knowledge.loader import _Indexes, _resolve_ref, build_edges, load_corpus

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
GUIDE_DIR = ROOT / "data" / "guide"
BON = GUIDE_DIR / "BON.json"

pytestmark = pytest.mark.skipif(not BON.exists(), reason="data/guide/BON.json 없음 — parse_manuals 먼저 실행")


@pytest.fixture(scope="module")
def guide():
    return load_guide_chunks(GUIDE_DIR)


@pytest.fixture(scope="module")
def world():
    corpus = load_corpus(FIXTURES, split=True)
    return corpus, build_edges(corpus)


def test_bon_has_300_plus_qa():
    bon = json.loads(BON.read_text(encoding="utf-8"))
    assert len(bon) >= 300, len(bon)


# ── R4: 골든 ↔ 해설 매핑 불변식 ─────────────────────────────────
MAP = GUIDE_DIR / "golden_guide_map.json"
EXCL = GUIDE_DIR / "exclude_test.json"


@pytest.mark.skipif(not MAP.exists(), reason="golden_guide_map.json 없음 — build_golden_guide_map 먼저 실행")
def test_r4_golden_guide_map_invariants():
    mapping = json.loads(MAP.read_text(encoding="utf-8"))
    bon_ids = {c["guide_id"] for c in json.loads(BON.read_text(encoding="utf-8"))}
    gids = [v["guide_id"] for v in mapping.values()]
    assert len(gids) == len(set(gids)), "중복 매칭 존재"          # 중복 0
    assert all(g in bon_ids for g in gids)                        # 매핑된 guide_id 실재
    assert all(v["ratio"] >= 0.9 for v in mapping.values())       # 임계 0.9 이상만 매핑
    excl = json.loads(EXCL.read_text(encoding="utf-8"))
    assert set(excl).issubset(set(gids))                          # test 제외는 매핑 부분집합


# ── R2 완료 기준: 청크 경계·머리글 정리 + question/answer 분리 ──
def test_r2_boundaries_and_cleanup():
    bon = json.loads(BON.read_text(encoding="utf-8"))
    assert len(bon) == 312, len(bon)

    def is_pagenum(ln):
        s = ln.strip().strip("-/\\").strip()
        return s.isdigit() and 1 <= len(s) <= 4

    over = [c["guide_id"] for c in bon if len(c["text"]) > 4000]
    assert not over, f"4000자 초과: {over}"
    for c in bon:
        assert "￭" not in c["text"] and "￭" not in (c.get("section_path") or ""), c["guide_id"]
        assert "」 매뉴얼" not in c["text"], c["guide_id"]
        assert not any(is_pagenum(l) for l in c["text"].split("\n")), c["guide_id"]
        assert c.get("question") and c.get("answer"), c["guide_id"]


def test_all_guide_chunks_have_section_and_pages(guide):
    assert guide
    for c in guide:
        assert c.section_path, c.guide_id
        assert c.page_start >= 1 and c.page_end >= c.page_start
        assert c.legal_effect is False


def test_cited_refs_resolution_rate(guide, capsys):
    """R1: cited_refs는 'LAWID:J조[:H항]' 주소. 해석률·UNRESOLVED 비율을 나눠 출력.
    한정어가 복원되어 여러 법령으로 귀속돼야 한다(전부 013774면 오해석)."""
    total = unresolved = 0
    laws = set()
    for c in guide:
        for r in c.cited_refs:
            total += 1
            if r.startswith("UNRESOLVED"):
                unresolved += 1
            else:
                laws.add(r.split(":")[0])
    with capsys.disabled():
        print(f"\n[cited_refs] 총 {total}, 해석 {total-unresolved} "
              f"({100*(total-unresolved)/total:.1f}%), UNRESOLVED {unresolved} "
              f"({100*unresolved/total:.1f}%), 귀속 법령 {sorted(laws)}")
    assert total > 0 and (total - unresolved) > 0
    # 한정어 복원 증거: 시행령(013933)·사용기준(75386)이 각각 귀속돼야 한다(전부 013774 아님)
    assert "013933" in laws and "75386" in laws


# ── R1 완료 기준: 법령명 한정어 → 올바른 주소 ──────────────────
def test_r1_guide_qualifier_resolution():
    from law_rag.knowledge.loader import load_manifest
    from law_rag.knowledge.refs import extract_guide_addresses as E
    m = load_manifest()
    cases = {
        "시행령 제9조제1항": "013933:J9:H1",
        "혁신법 시행령 제64조제3항": "013933:J64:H3",
        "사용기준 제73조": "75386:J73",
        "연구개발비 사용 기준 제36조": "75386:J36",
        "혁신법 제13조제8항": "013774:J13:H8",
        "영 제26조": "013933:J26",
        "시행규칙 제5조": "013980:J5",
        "「국가연구개발혁신법 시행령」 제32조": "013933:J32",
        "혁신법에 따른다. 같은 법 시행령 제14조": "013933:J14",
    }
    for text, expect in cases.items():
        assert expect in E(text, m), (text, E(text, m))
    # "시행령 제32조"는 013774(혁신법 32조 제재처분)로 오해석되면 안 된다
    got = E("시행령 제32조", m)
    assert "013933:J32" in got and "013774:J32" not in got


def test_use_guide_false_identical(world):
    """use_guide=False면 해설을 전혀 붙이지 않아 기존 답변과 동일(guide_notes 비어 있음)."""
    corpus, edges = world
    q = "연구책임자를 변경하려는데 협약변경 대상인가요?"
    base = PipelineBuilder(corpus, edges).build().answer(q, as_of=date(2026, 10, 1))
    gr = GuideRetriever.from_dir(GUIDE_DIR)
    off = PipelineBuilder(corpus, edges).with_guide(gr, use_guide=False).build().answer(q, as_of=date(2026, 10, 1))
    assert off.conclusion == base.conclusion
    assert off.body == base.body
    assert [c.model_dump() for c in off.citations] == [c.model_dump() for c in base.citations]
    assert off.guide_notes == []


def test_guide_notes_never_in_citations(world):
    """use_guide=True여도 해설은 guide_notes에만, 법령 Citation에는 절대 섞이지 않는다."""
    corpus, edges = world
    gr = GuideRetriever.from_dir(GUIDE_DIR)
    pipe = PipelineBuilder(corpus, edges).with_guide(gr, use_guide=True).build()
    ans = pipe.answer("기술료는 누가 징수하나요?", as_of=date(2026, 10, 1))
    # 모든 Citation은 법령 코퍼스의 law_name을 쓴다. 해설 문서명이 인용에 등장하면 안 된다.
    for cit in ans.citations:
        assert "매뉴얼" not in cit.law_name and "가이드라인" not in cit.law_name
