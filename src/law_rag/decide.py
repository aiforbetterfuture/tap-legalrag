"""
law_rag.decide — 평가·시연·CLI가 공유하는 단일 결정 경로 (작업지시서 23 §4).

문제: 기존엔 평가(eval_decision)·시연(build_demo)만 게이트를 호출하고, 사용자 answer()는 호출하지 않았다.
'평가한 경로'와 '쓰는 경로'가 달랐다. 이 모듈의 `decide_for_query` 하나로 통일한다.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from law_rag.gate import decide_answer
from law_rag.knowledge import Assembler, InMemoryEdgeStore
from law_rag.models import Query
from law_rag.reasoning import RuleEngine
from law_rag.retrieval import BM25Searcher, Retriever
from law_rag.router import RuleRouter

_CONFIGS = Path(__file__).resolve().parents[2] / "configs"
CORPUS_VERSION = "CORPUS-20260802"


def load_config(name_or_cfg) -> dict:
    if isinstance(name_or_cfg, dict):
        return name_or_cfg
    p = _CONFIGS / f"{name_or_cfg}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def decide_for_query(query: Query, corpus, edges, idx, config, *, final_k: int = 6) -> dict:
    """단일 질의 → 결정. 검색 → 조립 → 규칙정렬 → 게이트. 모든 소비자가 이 함수를 쓴다."""
    cfg = load_config(config)
    retriever = Retriever(keyword=BM25Searcher()) if cfg.get("retriever") == "bm25" else Retriever()
    scored, relevance = retriever.retrieve_with_signal(query, corpus, final_k=final_k)
    ranked = RuleEngine().rank(Assembler(InMemoryEdgeStore(edges), idx).assemble(scored, hops=1))
    g = decide_answer(query, ranked, relevance, cfg, idx)

    def _label(c):
        return (f"{c.law_short_name or c.law_name} 제{c.article_no}조"
                + (f"제{c.paragraph_no}항" if c.paragraph_no else ""))

    top = ranked.ordered[0] if ranked.ordered else None
    # §A-4: 최상위 1건만 보여 주면 약한 검색이 '대표 근거'처럼 보인다 → 상위 3건 + 점수.
    top3 = [{"ref": _label(sc.chunk), "score": round(sc.score, 3)} for sc in scored[:3]]
    # 질문 속 인용의 확인 결과(있으면)를 따로.
    from law_rag.provenance import _RE_CITE
    from law_rag.gate import _question_citation
    qc = None
    cite = _question_citation(query.text)
    if cite:
        from law_rag.knowledge.loader import load_manifest
        ref = __import__("law_rag.provenance", fromlist=["resolve"]).resolve(
            cite, query.text, load_manifest(), mode="pgate")
        qc = {"citation": cite.strip(), "status": ref.status,
              "law_id": ref.law_id, "article": ref.article, "paragraph": ref.paragraph}
    return {
        "decision": g["decision"], "reason": g["reason"], "path": g["path"],
        "evidence": _label(top) if top else None, "top3_evidence": top3,
        "question_citation": qc, "relevance": round(relevance, 3),
        "config": cfg.get("name", "?"), "corpus_version": CORPUS_VERSION,
        "run_id": str(uuid.uuid4()),
    }


def decide_question(question: str, corpus, edges, idx, config="B4", as_of=None) -> dict:
    """문자열 질문 편의 래퍼(라우팅 포함)."""
    q = RuleRouter().route(Query(text=question, as_of=as_of))
    return decide_for_query(q, corpus, edges, idx, config)
