"""
tests/test_p0_acceptance.py — P0 수리의 '실행 가능한 완료 기준'.

사용법(래칫 방식):
    - 지금은 각 테스트에 xfail(strict=True)가 붙어 있다 → 실패해도 전체 테스트는 초록.
    - 해당 결함을 고치면 테스트가 통과(XPASS)하고, strict=True라서 그 순간 빨간불이 된다.
    - 그때 그 테스트의 @P0 마커를 지운다. → 고친 결함이 다시 깨지면 바로 잡힌다.
    - P0 완료 = 이 파일에 @P0 마커가 하나도 남지 않고 전체 pytest 초록.

규칙: 테스트 '기대값'을 고쳐서 통과시키지 말 것. 바꿔야 한다면 근거를 specs/P0_REPAIR_SPEC.md에 먼저 적는다.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from law_rag import build_default_pipeline
from law_rag.knowledge.loader import build_edges, load_corpus
from law_rag.models import Query, ScoredChunk

FIXTURES = Path(__file__).parent / "fixtures"
P0 = pytest.mark.xfail(strict=True, reason="P0 미수리 — 고치면 이 마커를 삭제")


@pytest.fixture(scope="module")
def world():
    corpus = load_corpus(FIXTURES, split=True)
    edges = build_edges(corpus)
    idx = {c.chunk_id: c for c in corpus}
    pipe = build_default_pipeline(corpus, edges)
    return corpus, edges, idx, pipe


def _targets(edges, idx, from_id):
    return [idx[e.to_ref] for e in edges if e.from_chunk_id == from_id and e.to_ref in idx]


# P0-1 ── 시행령 본문의 "법 제11조제2항"은 혁신법(013774) 제11조로 연결돼야 한다. [수리완료]
def test_p0_1_parent_law_reference_resolves_to_parent_law(world):
    corpus, edges, idx, _ = world
    tg = _targets(edges, idx, "LAW-013933-J14-H1")
    assert any(t.law_id == "013774" and t.article_no == "11" for t in tg)
    # 시행령 자기 자신의 제11조로 잘못 연결되면 안 된다(본문에 시행령 제11조 언급 없음)
    assert not any(t.law_id == "013933" and t.article_no == "11" for t in tg)


# P0-2 ── 항까지 지정된 참조는 그 항으로 연결돼야 한다(제2항 → H2, 제1항 아님). [수리완료]
def test_p0_2_paragraph_precision(world):
    corpus, edges, idx, _ = world
    tg = [t for t in _targets(edges, idx, "LAW-013933-J14-H1")
          if t.law_id == "013774" and t.article_no == "11"]
    assert tg and all(t.paragraph_no == "2" for t in tg)


# P0-3 ── 상위법 조문에서 출발해도 그것을 구체화한 하위 규정까지 조립돼야 한다(양방향). [수리완료]
def test_p0_3_downward_assembly(world):
    _, _, idx, pipe = world
    bundle = pipe.assembler.assemble([ScoredChunk(chunk=idx["LAW-013774-J11-H2"], score=1.0)], hops=1)
    assert any(c.chunk_id.startswith("LAW-013933-J14") for c in bundle.related)


# P0-3b ── 확장 상한: 1순위 근거 하나당 딸려 오는 조문 수는 3개 이하(노이즈 방지). 지금도 통과해야 함.
def test_p0_3b_assembly_fanout_cap(world):
    corpus, _, idx, pipe = world
    for cid in ["LAW-013774-J11-H2", "LAW-013774-J13-H8", "LAW-013933-J14-H1"]:
        bundle = pipe.assembler.assemble([ScoredChunk(chunk=idx[cid], score=1.0)], hops=1)
        assert len(bundle.related) <= 3, (cid, [c.chunk_id for c in bundle.related])


# P0-4 ── 주제 태그는 가산점이지 거름망이 아니다. 주제가 태깅돼도 정답 후보가 잘려 나가면 안 된다. [수리완료]
def test_p0_4_topic_is_boost_not_filter(world):
    corpus, _, _, pipe = world
    q = pipe.router.route(Query(text="연구책임자 변경은 협약변경 대상인가요?"))
    assert q.topic is not None  # 라우터는 여전히 주제를 태깅한다
    scored, _ = pipe.retriever.retrieve_with_signal(q, corpus, first_stage_k=30, final_k=30)
    assert "LAW-013774-J11-H2" in [s.chunk.chunk_id for s in scored]


# P0-5 ── 보유 판본보다 이른 기준일이면 '근거 없음'이 아니라 '판본 미보유'를 명시해야 한다. [수리완료]
def test_p0_5_asof_outside_coverage_is_explicit(world):
    _, _, _, pipe = world
    a = pipe.answer("연구책임자 변경은 협약변경 대상인가요?", as_of=dt.date(2023, 5, 1))
    assert any("판본 미보유" in line for line in a.audit_log)
    assert "판본" in a.conclusion or "판본" in a.body
    assert a.needs_human_review is True


# P0-6 ── 규칙엔진은 검색 1위(1순위 근거)를 밀어내지 않는다.
#         검색 점수가 같은(동점) 1순위 근거끼리만 규칙(상위법·최신)으로 순서를 정하고,
#         조립으로 딸려 온 조문(related)은 1순위 근거들 뒤에 둔다.
#         (기존 test_rule_engine_prioritizes_higher_law는 모든 점수가 1.0인 동점 상황이라 계속 통과해야 함)
# [수리완료]
def test_p0_6_primary_order_kept(world):
    corpus, _, _, pipe = world
    q = pipe.router.route(Query(text="정산은 각 단계 종료 후 언제까지 해야 하나요?"))
    scored, _ = pipe.retriever.retrieve_with_signal(q, corpus)
    bundle = pipe.assembler.assemble(scored, hops=1)
    ranked = pipe.rule_engine.rank(bundle)
    top = scored[0].score
    top_ids = {s.chunk.chunk_id for s in scored if s.score == top}
    assert ranked.ordered[0].chunk_id in top_ids
    primary_ids = [s.chunk.chunk_id for s in scored]
    first_related_pos = min(
        (i for i, c in enumerate(ranked.ordered) if c.chunk_id not in primary_ids), default=len(ranked.ordered)
    )
    assert all(c.chunk_id in primary_ids for c in ranked.ordered[:first_related_pos])
    assert first_related_pos == len(primary_ids)
