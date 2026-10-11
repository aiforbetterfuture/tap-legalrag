"""
scripts/eval_clarify.py — 명확화 정책 일치율 (A-5).

골든 v3의 '명확화_정책' 정답과 decide() 결과를 비교. '불필요한 되묻기 비율'(정답 NONE인데 ASK)도 출력.
사용: python scripts/eval_clarify.py
"""
from __future__ import annotations

import csv
import io
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from law_rag.clarify import decide  # noqa: E402
from law_rag.knowledge.loader import load_corpus  # noqa: E402
from law_rag.models import Query  # noqa: E402
from law_rag.retrieval import Retriever  # noqa: E402
from law_rag.router import RuleRouter  # noqa: E402

GOLDEN = ROOT / "tests" / "golden" / "golden_set_v3_draft_manual.csv"


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    rows = list(csv.DictReader(GOLDEN.open(encoding="utf-8-sig")))
    corpus = load_corpus(ROOT / "tests" / "fixtures", split=True)
    router, retriever = RuleRouter(), Retriever()

    match = total = unnecessary = 0
    gold_none = 0
    conf = Counter()
    for r in rows:
        gold = (r.get("명확화_정책") or "").strip()
        if gold not in ("NONE", "ASSUME", "BRANCH", "ASK"):
            continue
        q = router.route(Query(text=r["질문"]))
        scored, _ = retriever.retrieve_with_signal(q, corpus, final_k=5)
        pol = decide(q, [s.chunk for s in scored])["policy"]
        total += 1
        match += int(pol == gold)
        conf[(gold, pol)] += 1
        if gold == "NONE":
            gold_none += 1
            unnecessary += int(pol == "ASK")

    print(f"명확화 정책 일치율: {match}/{total} ({100*match/total:.1f}%)")
    print(f"불필요한 되묻기 비율(정답 NONE인데 ASK): {unnecessary}/{gold_none} "
          f"({100*unnecessary/gold_none:.1f}%)" if gold_none else "NONE 문항 없음")
    for i in ("M-172", "M-246", "M-063"):
        row = next((x for x in rows if x["id"] == i), None)
        if row:
            q = router.route(Query(text=row["질문"]))
            sc, _ = retriever.retrieve_with_signal(q, corpus, final_k=5)
            print(f"  {i}: 정답={row['명확화_정책']} 예측={decide(q, [s.chunk for s in sc])['policy']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
