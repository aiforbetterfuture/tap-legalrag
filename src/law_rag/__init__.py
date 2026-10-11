"""
law_rag.pipeline — 전체 파이프라인 오케스트레이터.

라우터 판정에 따라 경로가 갈린다 (통합설계 3장, 속도 설계):
    lookup(조회형)   → 표/정의 직접 조회 (검색 생략, 가장 빠름)  [V1은 검색 폴백]
    search(검색형)   → 하이브리드 검색 → 조립 → 규칙엔진 → 생성
    judgment(판단형) → 검색형 경로 + (V3에서 명확화·검증 루프 삽입)

계층 순서: Router → Planner → Retriever → Assembler → RuleEngine → Governor
    Planner는 V1에서 PassThrough(무동작)지만, 자리를 미리 둬서
    V3의 적응형 검색·명확화를 파이프라인 변경 없이 끼울 수 있다.

이 파일이 '조립도'다. 각 계층은 자기 일만 하고, 여기서 순서대로 연결한다.
계층 교체는 PipelineBuilder로 주입 객체만 바꾼다(의존성 주입).
"""
from __future__ import annotations

from law_rag.clarify import decide as decide_clarification
from law_rag.governance import Governor
from law_rag.knowledge import Assembler
from law_rag.models import Answer, LawChunk, Query, QueryKind
from law_rag.planning import Planner, PassThroughPlanner
from law_rag.reasoning import RuleEngine
from law_rag.retrieval import CoverageGuard, Retriever
from law_rag.router import Router


class LawRagPipeline:
    """V1 파이프라인. 모든 계층을 주입받아 조립한다."""

    def __init__(
        self,
        router: Router,
        retriever: Retriever,
        assembler: Assembler,
        rule_engine: RuleEngine,
        governor: Governor,
        corpus: list[LawChunk],
        planner: Planner | None = None,
        guide_retriever=None,
        use_guide: bool = False,
    ):
        self.router = router
        self.planner = planner or PassThroughPlanner()
        self.retriever = retriever
        self.assembler = assembler
        self.rule_engine = rule_engine
        self.governor = governor
        self.corpus = corpus
        self.coverage_guard = CoverageGuard(corpus)
        # 매뉴얼 해설(M-3): 기본 OFF. 켜도 법령 Citation과 분리되어 guide_notes에만 담긴다.
        self.guide_retriever = guide_retriever
        self.use_guide = use_guide

    def answer(self, question: str, as_of=None) -> Answer:
        # 0) 라우팅 — 유형·주제 판정 (빠른 규칙, LLM 없음)
        query = self.router.route(Query(text=question, as_of=as_of))

        # 0.5) 기준일 보유 판본 확인 (P0-5) — 과거 판본 미보유를 '근거 없음'과 구분
        cov_status, out_laws = self.coverage_guard.check(query.as_of)
        if cov_status == "all_missing":
            ans = self.governor.coverage_miss_answer(
                query.as_of, self.coverage_guard.coverage_lines()
            )
            ans.audit_log.insert(0, f"라우팅={query.kind.value if query.kind else '미분류'}")
            return ans

        # 1) 계획 — V1은 PassThrough(원질의 그대로). V3에서 분해·명확화 결정.
        plan = self.planner.plan(query)
        # V1은 단일 질의만 사용. V3에서 plan.queries를 순회·병합.
        eff_query = plan.queries[0] if plan.queries else query

        # 2) 검색 — 라우터 유형과 무관하게 V1은 공통 검색 경로.
        # TODO(V1.1): lookup은 구조화 표 조회기(StructuredLookup)로 연결.
        #             간접비 비율·인건비 상한 등은 표 즉답이 정확하고 빠르다.
        scored, relevance = self.retriever.retrieve_with_signal(eff_query, self.corpus)

        # 3) 관계 조립 — 위임·인용·예외 1홉 확장
        bundle = self.assembler.assemble(scored, hops=1)

        # 4) 규칙 엔진 — 위계·시행일 정렬 (결정론적, 플러그인 규칙)
        ranked = self.rule_engine.rank(bundle)

        # 5) 판단형: (V3) 명확화·검증 루프 삽입 지점
        #    if eff_query.kind == QueryKind.judgment and plan.need_clarification: ...

        # 6) 통제·생성 — 인용검증·확신도·면책·감사로그
        #    관련도 신호를 넘겨 무관한 질문에 억지 답변하는 것을 막는다.
        answer = self.governor.compose(eff_query, ranked, top_score=relevance)
        answer.audit_log.insert(0, f"라우팅={eff_query.kind.value if eff_query.kind else '미분류'}")
        # A-5: 명확화 정책(답이 갈릴 때만 되묻기). 근거의 applies_to·준용으로 결정.
        answer.clarification = decide_clarification(eff_query, ranked.ordered)

        # 0.5) 일부 법령만 판본 범위 밖이면 경고를 덧붙인다(검색은 진행). (P0-5)
        if cov_status == "partial":
            warn = "일부 법령 판본 미보유(" + ", ".join(out_laws) + ")"
            answer.audit_log.append(warn)
            answer.body += f"\n\n⚠ {warn} — 해당 법령은 기준일 시점 판본을 확인하세요."

        # 7) 매뉴얼 해설(M-3): use_guide=True일 때만. Citation이 아니라 guide_notes에만 담는다.
        if self.use_guide and self.guide_retriever is not None:
            for g in self.guide_retriever.search(eff_query.text, top_k=3):
                answer.guide_notes.append(
                    f"[{g.doc_title} {g.section_path or ''} p.{g.page_start}] {g.text[:200]}"
                )
                # A-3: 낡은 조문 번호 인용 경고도 해설 칸에 함께(법령 Citation엔 섞지 않음)
                for w in getattr(g, "drift_warnings", None) or []:
                    answer.guide_notes.append(f"⚠ {w}")
            if answer.guide_notes:
                answer.audit_log.append(f"해설(법적효력 없음) {len(answer.guide_notes)}건 첨부")
        return answer

    def decide(self, question: str, config: str = "B4", as_of=None) -> dict:
        """근거 게이트 결정(평가·시연과 동일 경로, §4 통일). answer()와 달리 결정 등급을 낸다."""
        from law_rag.decide import decide_question
        from law_rag.knowledge.loader import build_edges
        idx = {c.chunk_id: c for c in self.corpus}
        return decide_question(question, self.corpus, build_edges(self.corpus), idx, config=config, as_of=as_of)


class PipelineBuilder:
    """파이프라인 조립 빌더. 부품을 하나씩 등록하고 build()한다.

    실험에서 유용:
        (PipelineBuilder(corpus, edges)
            .with_rule_engine(RuleEngine([HierarchyRule()]))  # 시점 규칙 제거
            .build())
    → 한 부품만 바꾼 변형을 쉽게 만들어 절제 연구를 돌린다.
    등록하지 않은 부품은 기본 구현으로 채워진다.
    """

    def __init__(self, corpus: list[LawChunk], edges):
        self.corpus = corpus
        self._edges = edges
        self._router = None
        self._planner = None
        self._retriever = None
        self._assembler = None
        self._rule_engine = None
        self._governor = None
        self._guide_retriever = None
        self._use_guide = False

    def with_guide(self, retriever, use_guide: bool = True):
        self._guide_retriever = retriever; self._use_guide = use_guide; return self

    def with_router(self, r): self._router = r; return self
    def with_planner(self, p): self._planner = p; return self
    def with_retriever(self, r): self._retriever = r; return self
    def with_assembler(self, a): self._assembler = a; return self
    def with_rule_engine(self, e): self._rule_engine = e; return self
    def with_governor(self, g): self._governor = g; return self

    def build(self) -> LawRagPipeline:
        from law_rag.governance import Governor, TemplateGenerator
        from law_rag.knowledge import InMemoryEdgeStore
        from law_rag.router import RuleRouter

        index = {c.chunk_id: c for c in self.corpus}
        return LawRagPipeline(
            router=self._router or RuleRouter(),
            planner=self._planner or PassThroughPlanner(),
            retriever=self._retriever or Retriever(),
            assembler=self._assembler or Assembler(InMemoryEdgeStore(self._edges), index),
            rule_engine=self._rule_engine or RuleEngine(),
            governor=self._governor or Governor(TemplateGenerator()),
            corpus=self.corpus,
            guide_retriever=self._guide_retriever,
            use_guide=self._use_guide,
        )


def build_default_pipeline(corpus: list[LawChunk], edges) -> LawRagPipeline:
    """기본 부품으로 파이프라인을 조립하는 편의 함수(빌더의 기본값 build)."""
    return PipelineBuilder(corpus, edges).build()
