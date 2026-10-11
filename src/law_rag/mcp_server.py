"""
law_rag.mcp_server — 에이전트용 TAP MCP 도구 (작업지시서 24 §C).

목적: LLM 에이전트가 **답하기 전에** TAP에게 "이 근거로 답해도 되나"를 묻게 한다.
다른 MCP(예: korean-rnd-regs-mcp)와 나란히 등록해, '조문 조회 → tap_decide → CONFIRM·ABSTAIN이면 사용자 확인' 순서로 쓴다.

원칙:
- **기존 함수의 얇은 포장만**. 판정 로직을 여기서 새로 만들지 않는다(decide/provenance/temporal 그대로 호출).
- 모든 도구 readOnly, **네트워크 없음**, 로컬 픽스처·레지스트리만 읽는다.
- 선택 의존성: `pip install .[mcp]`. mcp 미설치 환경에선 import 자체를 하지 않는다.

주의: 여기서 측정한 것은 TAP의 판정뿐이다. **LLM과의 종단 간(end-to-end) 성능은 평가하지 않았다.**
"""
from __future__ import annotations

import datetime as _dt
from functools import lru_cache
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from law_rag import build_default_pipeline
from law_rag.knowledge.loader import build_edges, load_corpus, load_manifest
from law_rag.provenance import resolve as _resolve
from law_rag.temporal import classify as _classify, load_lineage

_ROOT = Path(__file__).resolve().parents[2]

mcp = FastMCP("tap-legalrag")


@lru_cache(maxsize=1)
def _ctx():
    """파이프라인·인덱스·매니페스트·계보를 한 번만 만든다(읽기 전용)."""
    corpus = load_corpus(_ROOT / "tests" / "fixtures", split=True)
    pipe = build_default_pipeline(corpus, build_edges(corpus))
    idx = {c.chunk_id: c for c in corpus}
    return pipe, idx, load_manifest(), load_lineage()


def _audit(tool: str, **kv) -> str:
    kvs = " ".join(f"{k}={v}" for k, v in kv.items())
    return f"[{_dt.datetime.now().isoformat(timespec='seconds')}] tap.{tool} {kvs}"


@mcp.tool()
def tap_decide(question: str, config: str = "B4") -> dict:
    """질문에 '답해도 되는지'를 판정한다(ANSWER/CONFIRM/ABSTAIN/CORRECT).

    기존 결정 경로(decide_question)의 얇은 포장. readOnly, 네트워크 없음.
    반환: decision·reason·path·검색 상위 3건·질문 인용 확인·run_id·판본 + audit 1줄.
    """
    pipe, *_ = _ctx()
    r = pipe.decide(question, config=config)
    r["audit"] = _audit("decide", decision=r["decision"], config=r.get("config"), run_id=r["run_id"][:8])
    return r


@mcp.tool()
def tap_resolve_citation(citation: str, context: str = "") -> dict:
    """인용 문자열이 코퍼스의 실제 조문으로 확정되는지(P, 출처) pgate 모드로 해석한다.

    provenance.resolve의 얇은 포장. 근거 없으면 보류(UNRESOLVED). readOnly.
    반환: status(RESOLVED/PARTIAL/AMBIGUOUS/UNRESOLVED)·law_id·article·paragraph·method·is_external·candidates.
    """
    _, _, manifest, _ = _ctx()
    ref = _resolve(citation, context, manifest, mode="pgate")
    out = {"status": ref.status, "law_id": ref.law_id, "article": ref.article,
           "paragraph": ref.paragraph, "item": ref.item, "method": ref.method,
           "is_external": ref.is_external, "candidates": list(ref.candidates)}
    out["audit"] = _audit("resolve_citation", status=ref.status, law_id=ref.law_id, method=ref.method)
    return out


@mcp.tool()
def tap_check_currency(law_id: str, article: str, paragraph: str | None = None) -> dict:
    """인용한 조문 번호가 '낡은 번호'인지(T, 시점)를 계보로 확인한다.

    temporal.classify의 얇은 포장. 낡은 번호가 현행에도 있으면 단정하지 않고 후보를 준다. readOnly.
    반환: status(CURRENT_VALID/HISTORIC_VALID/AMBIGUOUS_HISTORIC/DRIFT_UNRESOLVED)·corrected_*·method·evidence·candidates?.
    """
    _, idx, _, lineage = _ctx()
    r = _classify(law_id, article, paragraph, idx, lineage)
    r["audit"] = _audit("check_currency", law_id=law_id, article=article,
                        paragraph=paragraph, status=r["status"])
    return r


def main() -> None:  # pragma: no cover - stdio 서버 실행 진입점
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
