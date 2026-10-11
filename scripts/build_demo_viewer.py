"""
scripts/build_demo_viewer.py — 판정 과정 시연 페이지 생성 (docs/demo/viewer.html)

무엇을 하나:
    examples/synthetic/examples.json 의 직접 작성한 합성 질문을 **실제 판정 함수**(pipeline.decide, config=B4)로
    실행하고, 그 결과를 그대로 담은 정적 HTML 한 장을 만든다. 손으로 쓴 판정 결과는 넣지 않는다.
    페이지는 질문 → 검색 → 범위 확인 → P → T → A → 결정 순서로 판정 경로를 단계별로 보여 준다.

무엇을 하지 않나:
    - 판정 로직을 새로 만들지 않는다(기존 함수 호출만).
    - 서버를 띄우지 않고 네트워크를 쓰지 않는다. 결과 페이지는 브라우저에서 파일로 바로 열린다.
    - 새 질문을 받아 실시간으로 판정하지 않는다. 새 질문은 CLI(`python -m law_rag ask`)로 실행한다.

사용: PYTHONPATH=src python scripts/build_demo_viewer.py  → docs/demo/viewer.html
      (?autoplay=1 을 붙여 열면 예시를 차례로 자동 재생한다)
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from law_rag import build_default_pipeline  # noqa: E402
from law_rag.clarify import decide as clarify_decide  # noqa: E402
from law_rag.decide import load_config  # noqa: E402
from law_rag.knowledge import Assembler, InMemoryEdgeStore  # noqa: E402
from law_rag.knowledge.loader import build_edges, load_corpus  # noqa: E402
from law_rag.models import Query  # noqa: E402
from law_rag.reasoning import RuleEngine  # noqa: E402
from law_rag.retrieval import BM25Searcher, Retriever  # noqa: E402
from law_rag.router import RuleRouter  # noqa: E402

EXAMPLES = ROOT / "examples" / "synthetic" / "examples.json"
OUT = ROOT / "docs" / "demo" / "viewer.html"


def _clarify_detail(question, corpus, edges, idx, cfg):
    """게이트가 A 단계에서 호출하는 것과 같은 명확화 함수를 다시 불러, 화면에 보여 줄 질문·분기를 얻는다."""
    query = RuleRouter().route(Query(text=question))
    retr = Retriever(keyword=BM25Searcher()) if cfg.get("retriever") == "bm25" else Retriever()
    scored, _ = retr.retrieve_with_signal(query, corpus, final_k=6)
    ranked = RuleEngine().rank(Assembler(InMemoryEdgeStore(edges), idx).assemble(scored, hops=1))
    return clarify_decide(query, ranked.ordered)


def _snippet(result, corpus):
    qc = result.get("question_citation") or {}
    if not (qc.get("law_id") and qc.get("article")):
        return None
    for c in corpus:
        if c.law_id == qc["law_id"] and str(c.article_no) == str(qc["article"]):
            label = f"제{c.article_no}조" + (f"제{c.paragraph_no}항" if c.paragraph_no else "")
            # 조문 제목만 보여 준다(본문 인용은 하지 않음 — 공개판 원문 겹침 검사와 충돌하지 않도록).
            head = c.body_text.split(")")[0] + ")" if ")" in c.body_text[:60] else label
            return {"law": c.law_name, "article": label, "text": head}
    return None


def collect():
    examples = json.loads(EXAMPLES.read_text(encoding="utf-8"))["examples"]
    corpus = load_corpus(ROOT / "tests" / "fixtures", split=True)
    edges = build_edges(corpus)
    idx = {c.chunk_id: c for c in corpus}
    pipe = build_default_pipeline(corpus, edges)
    cfg = load_config("B4")
    law_names = {c.law_id: c.law_name for c in corpus}
    rows = []
    for e in examples:
        r = pipe.decide(e["q"], config="B4")
        a_hit = any(p.startswith("A=") and p[2:] in ("ASK", "BRANCH") for p in r["path"])
        rows.append({
            "q": e["q"],
            "result": r,
            "clarify": _clarify_detail(e["q"], corpus, edges, idx, cfg) if a_hit else None,
            "snippet": _snippet(r, corpus) if r["decision"] == "ANSWER" else None,
            "cite_law": law_names.get((r.get("question_citation") or {}).get("law_id")),
            "known_failure": bool(e.get("known_failure")),
            "note": e.get("why", ""),
        })
    return rows


TEMPLATE = Path(__file__).with_name("demo_viewer_template.html")


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    rows = collect()
    data = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/[]", data)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    for r in rows:
        print(f"  [{r['result']['decision']}] {r['q']}")
    print(f"작성: {OUT.relative_to(ROOT)} ({len(rows)}건, 실제 판정 결과)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
