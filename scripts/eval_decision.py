"""
scripts/eval_decision.py — E3 답변 결정 실험 (v2 전략 2.3 / 18번 §4).

설정(configs/*.json)의 on/off만으로 B0~B4·B4−X·always-ANSWER를 구성하고, 같은 문항·같은 정답으로
결정 클래스를 비교한다. 지표: 결정 정확도, macro-F1, '답변 불가 문항 ANSWER 선택률', '잘못된 보류율',
always-ANSWER 기준선, McNemar(불일치 b/c 건수 함께). n<20 클래스는 k/n.

평가 클래스 매핑(명시): 시스템 ASK·BRANCH→CONFIRM / 골든 ANSWER_WITH_ASSUMPTION→ANSWER 등(아래 GOLD_MAP).
골든은 평가 전용(실행 경로에서 읽지 않음). 사용: python scripts/eval_decision.py [--out PATH]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from law_rag.gate import decide_answer  # noqa: E402
from law_rag.knowledge import Assembler, InMemoryEdgeStore  # noqa: E402
from law_rag.knowledge.loader import build_edges, load_corpus  # noqa: E402
from law_rag.models import Query  # noqa: E402
from law_rag.reasoning import RuleEngine  # noqa: E402
from law_rag.retrieval import BM25Searcher, Retriever  # noqa: E402
from law_rag.router import RuleRouter  # noqa: E402

GOLDEN = ROOT / "tests" / "golden" / "golden_set_v3_draft_manual.csv"
CONFIGS = ROOT / "configs"

GOLD_MAP = {
    "ANSWER": "ANSWER", "ANSWER_MARKED_INTERPRETIVE": "ANSWER", "ANSWER_WITH_ASSUMPTION": "ANSWER",
    "ASK_FIRST": "CONFIRM", "VERIFY_ASSUMPTION": "CONFIRM", "ANSWER_BRANCHED": "CONFIRM",
    "OUT_OF_SCOPE": "ABSTAIN", "NO_BASIS": "ABSTAIN", "CORRECT_PREMISE": "CORRECT",
}
CLASSES = ["ANSWER", "CONFIRM", "ABSTAIN", "CORRECT"]


def _load_configs():
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(CONFIGS.glob("*.json"))}


def _system_class(query, ranked, relevance, cfg, idx):
    d = decide_answer(query, ranked, relevance, cfg, idx)["decision"]
    return d  # ANSWER | CONFIRM | ABSTAIN (gate v1은 CORRECT 미생성)


def run_config(rows, corpus, edges, idx, cfg):
    """§4 통일: 평가도 law_rag.decide.decide_for_query(사용자·시연과 같은 경로)를 쓴다."""
    from law_rag.decide import decide_for_query
    router = RuleRouter()
    preds = {}
    for r in rows:
        as_of = None
        if (r.get("기준일") or "").strip():
            try:
                as_of = dt.date.fromisoformat(r["기준일"].strip())
            except ValueError:
                as_of = None
        q = router.route(Query(text=r["질문"], as_of=as_of))
        preds[r["id"]] = decide_for_query(q, corpus, edges, idx, cfg)["decision"]
    return preds


def _gold_class(label):
    if label not in GOLD_MAP:
        raise KeyError(f"GOLD_MAP에 없는 기대_행동: {label!r}")
    return GOLD_MAP[label]


def metrics(rows, preds):
    gold = {r["id"]: _gold_class(r["기대_행동"]) for r in rows}
    n = len(rows)
    correct = sum(int(preds[r["id"]] == gold[r["id"]]) for r in rows)
    # 답변 불가 문항 ANSWER 선택률 = (gold!=ANSWER 중 시스템 ANSWER) / (gold!=ANSWER)
    non_ans = [r["id"] for r in rows if gold[r["id"]] != "ANSWER"]
    ans_on_nonans = sum(int(preds[i] == "ANSWER") for i in non_ans)
    # 잘못된 보류율 = (gold==ANSWER 중 시스템 != ANSWER) / (gold==ANSWER)
    ans_ids = [r["id"] for r in rows if gold[r["id"]] == "ANSWER"]
    wrong_withhold = sum(int(preds[i] != "ANSWER") for i in ans_ids)
    # macro-F1
    f1s = []
    for c in CLASSES:
        tp = sum(int(preds[r["id"]] == c and gold[r["id"]] == c) for r in rows)
        fp = sum(int(preds[r["id"]] == c and gold[r["id"]] != c) for r in rows)
        fn = sum(int(preds[r["id"]] != c and gold[r["id"]] == c) for r in rows)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return {
        "n": n, "결정정확도": round(correct / n, 3), "macro_F1": round(sum(f1s) / len(CLASSES), 3),
        "답변불가_ANSWER선택": f"{ans_on_nonans}/{len(non_ans)}",
        "잘못된보류": f"{wrong_withhold}/{len(ans_ids)}",
        "분포": dict(Counter(preds[r["id"]] for r in rows)),
    }


def mcnemar(rows, preds_a, preds_b):
    gold = {r["id"]: _gold_class(r["기대_행동"]) for r in rows}
    b = c = 0  # b: A맞고 B틀림, c: A틀리고 B맞음
    for r in rows:
        ca = preds_a[r["id"]] == gold[r["id"]]
        cb = preds_b[r["id"]] == gold[r["id"]]
        b += int(ca and not cb)
        c += int(cb and not ca)
    return {"b(A>B)": b, "c(B>A)": c, "n_discordant": b + c}


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args()

    allrows = list(csv.DictReader(GOLDEN.open(encoding="utf-8-sig")))
    rows = [r for r in allrows if r["최종판정(1차)"] not in ("REMOVE", "HOLD")]
    corpus = load_corpus(ROOT / "tests" / "fixtures", split=True)
    edges = build_edges(corpus)
    idx = {c.chunk_id: c for c in corpus}
    cfgs = _load_configs()

    preds = {name: run_config(rows, corpus, edges, idx, cfg) for name, cfg in cfgs.items()}
    res = {"n": len(rows), "configs": {}, "mcnemar": {}}
    print(f"[E3 결정 실험] n={len(rows)} (REMOVE·HOLD 제외)")
    print(f"{'설정':14}{'정확도':>7}{'macroF1':>8}  답변불가ANSWER  잘못된보류  분포")
    for name in ("B0", "B1", "B2", "B3", "B4", "B4-P", "B4-T", "B4-A", "always-ANSWER"):
        if name not in preds:
            continue
        m = metrics(rows, preds[name])
        res["configs"][name] = m
        print(f"{name:14}{m['결정정확도']:>7}{m['macro_F1']:>8}  {m['답변불가_ANSWER선택']:>10}  "
              f"{m['잘못된보류']:>8}  {m['분포']}")
    # McNemar: B0 vs B4, always-ANSWER vs B4 (게이트 효과)
    res["mcnemar"]["B0_vs_B4"] = mcnemar(rows, preds["B0"], preds["B4"])
    res["mcnemar"]["alwaysANSWER_vs_B4"] = mcnemar(rows, preds["always-ANSWER"], preds["B4"])
    print("\nMcNemar 불일치 쌍:")
    for k, v in res["mcnemar"].items():
        print(f"  {k}: b={v['b(A>B)']} c={v['c(B>A)']} (불일치 {v['n_discordant']})")
    if args.out:
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
