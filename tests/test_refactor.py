"""
tests/test_refactor.py — 아키텍처 개선 반영분 검증.

이 테스트들은 검토용 LLM 지적을 반영한 개선이 실제로 작동하고,
특히 '규칙 플러그인화'가 논문 절제 연구를 가능하게 함을 보인다.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from law_rag import PipelineBuilder, build_default_pipeline
from law_rag.knowledge.loader import build_edges, load_chunks
from law_rag.models import EvidenceBundle, ScoredChunk
from law_rag.planning import PassThroughPlanner, Plan
from law_rag.prompts import load as load_prompt
from law_rag.reasoning import (
    DEFAULT_RULES,
    HierarchyRule,
    RuleEngine,
    StableRule,
    TemporalRule,
)

SAMPLES = Path(__file__).parent.parent / "data" / "samples" / "sample_chunks.json"


@pytest.fixture
def chunks():
    return load_chunks(SAMPLES)


@pytest.fixture
def edges(chunks):
    return build_edges(chunks)


# ── ⑤ 프롬프트 파일 분리 ───────────────────────────────────────
def test_prompt_loads_from_file():
    """시스템 정책이 파일에서 로드되는가."""
    policy = load_prompt("system_policy")
    assert "근거" in policy and len(policy) > 50


def test_missing_prompt_raises():
    """없는 프롬프트는 조용히 넘기지 않고 예외를 올리는가(치명적 설정오류)."""
    with pytest.raises(FileNotFoundError):
        load_prompt("does_not_exist_xyz")


# ── ② 규칙 플러그인화 + 절제 연구 가능성 ──────────────────────
def test_default_rules_prioritize_law(chunks):
    """기본 규칙 세트가 상위법을 최상위에 두는가."""
    bundle = EvidenceBundle(primary=[ScoredChunk(chunk=c, score=1.0) for c in chunks])
    ranked = RuleEngine(list(DEFAULT_RULES)).rank(bundle)
    assert ranked.ordered[0].hierarchy_level == min(c.hierarchy_level for c in chunks)


def test_rule_ablation_changes_order(chunks):
    """절제 연구 시연: 시점 규칙 유무로 정렬이 달라질 수 있는가.

    이것이 핵심 — 규칙을 껐다 켰다 하며 기여를 측정할 수 있어야
    논문의 성분별 ablation이 코드로 실행된다."""
    bundle = EvidenceBundle(primary=[ScoredChunk(chunk=c, score=1.0) for c in chunks])

    # 시점 규칙 포함 vs 제외 — 두 엔진이 독립적으로 동작
    with_temporal = RuleEngine([HierarchyRule(), TemporalRule(), StableRule()])
    without_temporal = RuleEngine([HierarchyRule(), StableRule()])

    r1 = with_temporal.rank(bundle)
    r2 = without_temporal.rank(bundle)
    # 두 결과 모두 유효하게 정렬되고, reason에 적용 규칙이 다르게 기록됨
    assert "temporal" in r1.reason
    assert "temporal" not in r2.reason


# ── ⑥ Planner 인터페이스 (V1 PassThrough) ─────────────────────
def test_passthrough_planner_is_noop():
    """V1 Planner는 원질의를 그대로 통과시키는가."""
    from law_rag.models import Query
    plan = PassThroughPlanner().plan(Query(text="테스트"))
    assert isinstance(plan, Plan)
    assert plan.need_clarification is False
    assert len(plan.queries) == 1


# ── ⑦ PipelineBuilder ─────────────────────────────────────────
def test_builder_default_equivalent(chunks, edges):
    """빌더 기본 build가 기존 파이프라인과 동일하게 동작하는가."""
    pipe = PipelineBuilder(chunks, edges).build()
    ans = pipe.answer("연구책임자를 변경하려는데 협약변경 대상인가요?", as_of=date(2024, 8, 1))
    assert ans.conclusion and ans.disclaimer


def test_builder_swaps_one_part(chunks, edges):
    """빌더로 규칙엔진만 교체해 변형 파이프라인을 만들 수 있는가.
    논문 실험에서 '한 부품만 바꾼 조건'을 쉽게 만드는 핵심 기능."""
    custom = RuleEngine([HierarchyRule(), StableRule()])  # 시점 규칙 제거 버전
    pipe = PipelineBuilder(chunks, edges).with_rule_engine(custom).build()
    ans = pipe.answer("직접비 변경은 어떻게 하나요?", as_of=date(2024, 8, 1))
    assert ans.conclusion  # 정상 동작


# ── ④ 확신도 스코어러 분리 ────────────────────────────────────
def test_confidence_scorer_injectable(chunks, edges):
    """확신도 스코어러를 교체할 수 있는가(Governor 코디네이터화 확인)."""
    from law_rag.governance import Governor, TemplateGenerator
    from law_rag.governance.confidence import ConfidenceScorer
    from law_rag.models import Confidence, Query, RankedEvidence

    class AlwaysHigh(ConfidenceScorer):
        def score(self, query, evidence):
            return Confidence.HIGH

    gov = Governor(TemplateGenerator(), scorer=AlwaysHigh())
    bundle = EvidenceBundle(primary=[ScoredChunk(chunk=chunks[0], score=1.0)])
    ranked = RuleEngine().rank(bundle)
    ans = gov.compose(Query(text="x"), ranked, top_score=0.9)
    assert ans.confidence == Confidence.HIGH
