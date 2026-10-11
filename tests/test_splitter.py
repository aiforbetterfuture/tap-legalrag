"""
tests/test_splitter.py — 대형 별표 분할(splitter) 검증.

핵심 계약:
    1) 부모(원본)는 코퍼스에 남되 is_split_parent=True → 기본 검색 제외.
    2) 자식은 {부모ID}-S{n}, parent_chunk_id=부모ID.
    3) 모든 자식 body_text는 머리말('[별표N] ...')로 시작 (인용 무결성).
    4) 자식은 1,500자 이하로 수렴 (임베딩 가능 크기).
    5) 짧은 청크는 분할하지 않는다.
"""
from __future__ import annotations

import pytest

from law_rag.knowledge.splitter import (
    DEFAULT_MAX_CHARS,
    split_appendix,
    split_corpus,
)
from law_rag.models import LawChunk
from law_rag.retrieval import meta_filter


def _mk(chunk_id: str, body: str, **over) -> LawChunk:
    base = dict(
        chunk_id=chunk_id, law_id="X", law_name="테스트법",
        law_type="고시", hierarchy_level=4, binding_power=3,
        article_no="별표1", article_title="테스트 표",
        promulgation_date="2023-01-01", enforce_date="2023-01-01",
        article_enforce_date="2023-01-01", valid_from="2023-01-01",
        topic_tags=["연구비집행"], body_text=body,
        source_url="https://example.com",
    )
    base.update(over)
    return LawChunk.model_validate(base)


# 대분류 3개 + 여러 행으로 구성된 큰 표 (>5000자)
def _big_table_body() -> str:
    head = "[별표1] 테스트 표(제10조 관련)\n│구분│기관명│비율(%)│\n"
    parts = []
    for major, name in [("1", "직접비"), ("2", "간접비"), ("3", "기타비")]:
        parts.append(f"{major}. {name}")
        for i in range(60):
            parts.append(f"│{name}{i:02d}기관 아주 긴 이름을 가진 연구개발기관 항목│ {i}.{major}0 │")
    return head + "\n".join(parts)


@pytest.fixture
def big_chunk():
    return _mk("LAW-X-B1", _big_table_body())


# ── 짧은 청크는 분할 안 함 ────────────────────────────────────
def test_small_chunk_not_split():
    small = _mk("LAW-X-B9", "[별표9] 작은 표\n│a│b│\n│1│2│")
    result = split_appendix(small)
    assert len(result) == 1
    assert result[0].chunk_id == "LAW-X-B9"
    assert result[0].is_split_parent is False


# ── 부모 + 자식 구조 ─────────────────────────────────────────
def test_split_produces_parent_and_children(big_chunk):
    result = split_appendix(big_chunk)
    parents = [c for c in result if c.is_split_parent]
    children = [c for c in result if "-S" in c.chunk_id]
    assert len(parents) == 1
    assert parents[0].chunk_id == "LAW-X-B1"      # 원본 유지
    assert len(children) >= 3                       # 최소 대분류 수만큼
    for ch in children:
        assert ch.parent_chunk_id == "LAW-X-B1"
        assert ch.is_split_parent is False


# ── 자식 chunk_id 형식 + 유일성 ──────────────────────────────
def test_child_ids_unique_and_formatted(big_chunk):
    result = split_appendix(big_chunk)
    children = [c for c in result if "-S" in c.chunk_id]
    ids = [c.chunk_id for c in children]
    assert len(ids) == len(set(ids))               # 중복 없음
    for i, ch in enumerate(children, start=1):
        assert ch.chunk_id == f"LAW-X-B1-S{i}"


# ── 인용 무결성: 모든 자식이 머리말로 시작 ──────────────────
def test_children_carry_heading(big_chunk):
    result = split_appendix(big_chunk)
    children = [c for c in result if "-S" in c.chunk_id]
    for ch in children:
        assert ch.body_text.startswith("[별표1]"), \
            f"{ch.chunk_id} 머리말 누락: {ch.body_text[:40]}"
        assert "부분" in ch.body_text                # 부분 표시 존재


# ── 자식 크기 상한 수렴 ──────────────────────────────────────
def test_children_within_max_chars(big_chunk):
    result = split_appendix(big_chunk)
    children = [c for c in result if "-S" in c.chunk_id]
    for ch in children:
        # 단일 행이 상한을 넘지 않는 한 max_chars 이하로 수렴
        max_line = max(len(L) for L in ch.body_text.split("\n"))
        assert len(ch.body_text) <= DEFAULT_MAX_CHARS or max_line > DEFAULT_MAX_CHARS


# ── 부모는 검색에서 제외, 자식은 포함 ────────────────────────
def test_meta_filter_excludes_parent_includes_children(big_chunk):
    result = split_appendix(big_chunk)
    filtered = meta_filter(result)
    assert not any(c.is_split_parent for c in filtered)      # 부모 제외
    assert all("-S" in c.chunk_id for c in filtered)          # 자식만 남음
    # 전체 조회 옵션으로는 부모 노출
    with_parent = meta_filter(result, include_split_parents=True)
    assert any(c.is_split_parent for c in with_parent)


# ── split_corpus: 서식/조문은 건드리지 않음 ─────────────────
def test_split_corpus_leaves_non_appendix():
    article = _mk("LAW-X-J1", "제1조(목적) 이 고시는...", article_no="1")
    form = _mk("LAW-X-F1", "[별지1] 신청서 " + "x" * 6000, is_form=True)
    big = _mk("LAW-X-B1", _big_table_body())
    out = split_corpus([article, form, big])
    # 조문과 서식은 그대로, 큰 별표만 분할
    assert any(c.chunk_id == "LAW-X-J1" for c in out)
    assert any(c.chunk_id == "LAW-X-F1" for c in out)
    assert any("-S" in c.chunk_id for c in out)
