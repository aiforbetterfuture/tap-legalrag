"""
law_rag CLI — 평가·시연과 같은 결정 경로(§4 통일)를 커맨드라인에서.

사용: python -m law_rag ask "질문" [--config B4] [--json]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="law_rag")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="질문에 대한 근거 게이트 결정")
    a.add_argument("question")
    a.add_argument("--config", default="B4")
    a.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from law_rag import build_default_pipeline
    from law_rag.knowledge.loader import build_edges, load_corpus
    corpus = load_corpus(ROOT / "tests" / "fixtures", split=True)
    pipe = build_default_pipeline(corpus, build_edges(corpus))
    r = pipe.decide(args.question, config=args.config)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(f"[{r['decision']}] {args.question}")
        print(f"  사유: {r['reason']}")
        if r.get("question_citation"):
            qc = r["question_citation"]
            print(f"  질문 인용: {qc['citation']} → P={qc['status']}"
                  + (f", 법령 {qc['law_id']} 제{qc['article']}조"
                     + (f"제{qc['paragraph']}항" if qc.get('paragraph') else "") if qc.get('law_id') else ""))
        print(f"  검색 상위 3건(검색 순위 점수): " + " · ".join(
            f"{e['ref']}({e['score']})" for e in r.get("top3_evidence", [])))
        if r.get("relevance") is not None:
            print(f"  범위 판정 신호(관련도): {r['relevance']}")
        print(f"  경로: {' → '.join(r['path'])}  config={r['config']} 판본={r['corpus_version']} run_id={r['run_id'][:8]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
