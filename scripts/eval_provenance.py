"""
scripts/eval_provenance.py — E1 출처 검증 (v2 전략 2.2 / 18번 작업지시서 1).

provenance_gold.csv(100: dev 40 / test 60)로 세 해석기를 비교한다:
  naive(인용에 법령명 없으면 무조건 혁신법) / fallback(문맥·외부 감지 + 못 찾으면 혁신법) / pgate(근거 없으면 보류).
지표: 정확도 · 오귀속률 · coverage(RESOLVED 비율) · RESOLVED 정밀도 · 정당한 보류율 · 잘못된 보류율 ·
      판정 정확도=(정답+정당한 보류)/n. + fallback→pgate 전환표 + resolvability별 표 + EXT 관대/엄격.
"다 보류해서 오귀속 0" 착시를 coverage·잘못된 보류율·판정정확도로 함께 드러낸다.

동결: E1 규칙(provenance.py)은 동결. test를 본 뒤 규칙을 고치면 독립 평가로 보고하지 않는다.
사용: python scripts/eval_provenance.py [--split test|dev|all] [--out PATH]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from law_rag.knowledge.loader import load_manifest  # noqa: E402
from law_rag.provenance import resolve  # noqa: E402

GOLD = ROOT / "data" / "eval" / "provenance_gold.csv"
ADJ = ROOT / "data" / "eval" / "provenance_adjudicated.csv"
MODES = ("naive", "fallback", "pgate")


def _resolvable(r) -> bool:
    return r["gold_in_corpus"].strip().upper() == "Y" and not r["gold_law"].startswith("EXT:")


def _is_corpus_law(law: str) -> bool:
    law = (law or "").strip()
    return bool(law) and not law.startswith("EXT:") and law != "UNRESOLVABLE"


def _correct(ref, r) -> bool:
    if ref.law_id is None or ref.law_id != r["gold_law"]:
        return False
    if (ref.article or "") != (r["gold_article"] or ""):
        return False
    gp = (r["gold_paragraph"] or "").strip()
    return (not gp) or (ref.paragraph or "") == gp


def classify(ref, r) -> str:
    """CORRECT / MISATTRIB_HASGOLD / MISATTRIB_NOGOLD / WRONG_WITHHOLD / JUST_WITHHOLD."""
    resolved = ref.status == "RESOLVED" and ref.law_id is not None
    resolvable = _resolvable(r)
    if resolved:
        if _correct(ref, r):
            return "CORRECT"
        return "MISATTRIB_HASGOLD" if resolvable else "MISATTRIB_NOGOLD"
    return "WRONG_WITHHOLD" if resolvable else "JUST_WITHHOLD"


def evaluate(rows, manifest, mode):
    c = Counter(classify(resolve(r["raw_citation"], r.get("context", ""), manifest, mode=mode), r)
                for r in rows)
    n = len(rows) or 1
    correct = c["CORRECT"]
    misattrib = c["MISATTRIB_HASGOLD"] + c["MISATTRIB_NOGOLD"]
    resolved = correct + misattrib
    just_w, wrong_w = c["JUST_WITHHOLD"], c["WRONG_WITHHOLD"]
    n_resolvable = sum(_resolvable(r) for r in rows)
    n_withhold_ok = len(rows) - n_resolvable
    return {
        "n": len(rows),
        "정확도": round(correct / n, 3),
        "오귀속률": round(misattrib / n, 3),
        "coverage": round(resolved / n, 3),
        "RESOLVED정밀도": round(correct / resolved, 3) if resolved else 0.0,
        "정당한보류율": round(just_w / n_withhold_ok, 3) if n_withhold_ok else None,
        "잘못된보류율": round(wrong_w / n_resolvable, 3) if n_resolvable else None,
        "판정정확도": round((correct + just_w) / n, 3),
        "_counts": dict(c),
    }


def transition_table(rows, manifest):
    """fallback이 '못 찾아 혁신법으로 보낸'(method=fallback_default) 문항의 pgate 결과."""
    out = defaultdict(list)
    for r in rows:
        fb = resolve(r["raw_citation"], r.get("context", ""), manifest, mode="fallback")
        if fb.method != "fallback_default":
            continue
        fb_out = classify(fb, r)
        pg_out = classify(resolve(r["raw_citation"], r.get("context", ""), manifest, mode="pgate"), r)
        out[f"{fb_out}→{pg_out}"].append(r["pg_id"])
    return {k: {"n": len(v), "pg_ids": v} for k, v in sorted(out.items())}


def resolvability_table(rows, manifest):
    tbl = {}
    groups = defaultdict(list)
    for r in rows:
        groups[r["resolvability"]].append(r)
    for g, grp in sorted(groups.items()):
        tbl[g] = {}
        for mode in MODES:
            c = Counter(classify(resolve(r["raw_citation"], r.get("context", ""), manifest, mode=mode), r)
                        for r in grp)
            tbl[g][mode] = {"n": len(grp), "정답": c["CORRECT"],
                            "오귀속": c["MISATTRIB_HASGOLD"] + c["MISATTRIB_NOGOLD"],
                            "보류": c["JUST_WITHHOLD"] + c["WRONG_WITHHOLD"]}
    return tbl


def ext_table(rows, manifest):
    """EXT(외부 법령) 문항: 관대(코퍼스에 안 붙이면 정답) / 엄격(외부로 명시 감지)."""
    ext = [r for r in rows if r["gold_law"].startswith("EXT:")]
    out = {"n": len(ext)}
    for mode in MODES:
        lenient = strict = 0
        for r in ext:
            ref = resolve(r["raw_citation"], r.get("context", ""), manifest, mode=mode)
            not_attrib = not (ref.status == "RESOLVED" and ref.law_id is not None)
            lenient += int(not_attrib)
            strict += int(not_attrib and ref.is_external)
        out[mode] = {"관대정답": f"{lenient}/{len(ext)}", "엄격정답": f"{strict}/{len(ext)}"}
    return out


# ── 세 기준(gold / final / context) 평가 (작업지시서 30 P0-2) ─────────────────
# gold   = AI 정답표(동결)      — 법령 귀속 과제(기존 gold_* 사용)
# final  = 설계자 최종판정      — 법령 귀속 과제(adjudicated final_* 로 gold_* 치환)
# context= 답변 가능성 판정 과제 — 문맥만으로 법령 특정 가능한지를 시스템이 맞게 가렸는가(2×2)

def _load_adjudicated() -> dict:
    return {r["pg_id"]: r for r in csv.DictReader(ADJ.open(encoding="utf-8-sig"))}


def _rows_for_final(gold_rows, adj):
    """gold 행의 라벨 칸(gold_*)만 최종판정(final_*)으로 바꾼 사본. 나머지(인용·문맥·split)는 그대로."""
    out = []
    for r in gold_rows:
        a = adj.get(r["pg_id"])
        if a is None:
            continue
        nr = dict(r)
        nr["gold_law"] = a["final_law"].strip()
        nr["gold_article"] = a["final_article"].strip()
        nr["gold_paragraph"] = a["final_paragraph"].strip()
        # UNRESOLVABLE·EXT는 코퍼스 밖 → in_corpus=N (보류가 정답)
        nr["gold_in_corpus"] = "Y" if (a["final_in_corpus"].strip().upper() == "Y"
                                       and _is_corpus_law(a["final_law"])) else "N"
        out.append(nr)
    return out


def _answerable(a) -> bool:
    """문맥만으로 법령 특정 가능(답할 수 있음): context_resolvable=Y ∧ final_in_corpus=Y ∧ 코퍼스 법령."""
    return (a["context_resolvable"].strip().upper() == "Y"
            and a["final_in_corpus"].strip().upper() == "Y"
            and _is_corpus_law(a["final_law"]))


def evaluate_context(gold_rows, adj, manifest, mode):
    """답변 가능성 판정 과제(2×2). 답할 수 없는 문항은 '보류가 정답'."""
    cells = Counter()
    for r in gold_rows:
        a = adj.get(r["pg_id"])
        if a is None:
            continue
        ref = resolve(r["raw_citation"], r.get("context", ""), manifest, mode=mode)
        resolved = ref.status == "RESOLVED" and ref.law_id is not None
        if _answerable(a):
            if resolved:
                ok = (ref.law_id == a["final_law"].strip()
                      and (ref.article or "") == (a["final_article"].strip())
                      and ((not a["final_paragraph"].strip())
                           or (ref.paragraph or "") == a["final_paragraph"].strip()))
                cells["답가능×확정_맞음" if ok else "답가능×확정_틀림"] += 1
            else:
                cells["답가능×보류(잘못된보류)"] += 1
        else:
            cells["답불가×확정(근거없는확정)" if resolved else "답불가×보류(정당한보류)"] += 1
    n = sum(1 for r in gold_rows if r["pg_id"] in adj) or 1
    correct = cells["답가능×확정_맞음"] + cells["답불가×보류(정당한보류)"]
    misattrib = cells["답가능×확정_틀림"] + cells["답불가×확정(근거없는확정)"]
    answerable_n = sum(1 for r in gold_rows if r["pg_id"] in adj and _answerable(adj[r["pg_id"]]))
    return {
        "n": n, "answerable_n": answerable_n,
        "판정정확도": round(correct / n, 3),
        "오귀속": misattrib,
        "잘못된보류": f'{cells["답가능×보류(잘못된보류)"]}/{answerable_n}',
        "정당한보류": cells["답불가×보류(정당한보류)"],
        "근거없는확정": cells["답불가×확정(근거없는확정)"],
        "_cells": dict(cells),
    }


def _misattrib_n(m):
    return m["_counts"].get("MISATTRIB_HASGOLD", 0) + m["_counts"].get("MISATTRIB_NOGOLD", 0)


def build_label_views(manifest) -> dict:
    """gold/final/context × (test/dev/all) × 모드. e1_label_views.json 용."""
    gold_rows = list(csv.DictReader(GOLD.open(encoding="utf-8-sig")))
    adj = _load_adjudicated()
    splits = {"test": [r for r in gold_rows if r["split"] == "test"],
              "dev": [r for r in gold_rows if r["split"] == "dev"],
              "all": gold_rows}
    views = {"note": "gold=AI정답표(동결)·final=설계자 최종판정=법령 귀속 과제 / context=답변 가능성 판정 과제(2×2). "
                     "최종판정은 설계자 1인이 정답표를 본 뒤 내린 것(독립·외부 검증 아님).",
             "label_defs": {
                 "gold": "AI 정답표(동결). 법령 귀속 정확도.",
                 "final": "설계자 최종판정(adjudicated). 법령 귀속 정확도.",
                 "context": "C(문맥 충분성) 기준. 답할 수 있음=context_resolvable=Y∧final_in_corpus=Y∧코퍼스법령; "
                            "시스템 RESOLVED=답함, 그 외=보류. 답할 수 없으면 보류가 정답.",
                 "C_rules": "C1 인용 안 법령명→Y · C2 앞 문장 법령명 연결 명확→Y · C3 관례·배경지식 필요→N · "
                            "C4 조문 번호만→N · C5 여러 법령 해석 가능→N"}}
    for sp, rows in splits.items():
        views[sp] = {"n": len(rows), "gold": {}, "final": {}, "context": {}}
        frows = _rows_for_final(rows, adj)
        for mode in MODES:
            g = evaluate(rows, manifest, mode)
            f = evaluate(frows, manifest, mode)
            views[sp]["gold"][mode] = {"판정정확도": g["판정정확도"], "오귀속": _misattrib_n(g),
                                       "잘못된보류": f'{g["_counts"].get("WRONG_WITHHOLD",0)}/{sum(_resolvable(r) for r in rows)}'}
            views[sp]["final"][mode] = {"판정정확도": f["판정정확도"], "오귀속": _misattrib_n(f),
                                        "잘못된보류": f'{f["_counts"].get("WRONG_WITHHOLD",0)}/{sum(_resolvable(r) for r in frows)}'}
            views[sp]["context"][mode] = evaluate_context(rows, adj, manifest, mode)
    return views


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test", choices=["test", "dev", "all"])
    ap.add_argument("--labels", help="gold|final|context 세 기준 뷰 생성(e1_label_views.json)",
                    nargs="?", const="all")
    ap.add_argument("--out")
    args = ap.parse_args()

    if args.labels:
        manifest = load_manifest()
        views = build_label_views(manifest)
        for sp in ("test", "all"):
            print(f"\n[E1 세 기준] {sp} (n={views[sp]['n']})  판정정확도 naive/fallback/pgate")
            for lab in ("gold", "final", "context"):
                v = views[sp][lab]
                print(f"  {lab:8} " + " / ".join(str(v[m]['판정정확도']) for m in MODES)
                      + (f"  (답가능 {v['pgate']['answerable_n']}, pgate 잘못된보류 {v['pgate']['잘못된보류']}, 오귀속 {v['pgate']['오귀속']})"
                         if lab == "context" else
                         f"  (pgate 잘못된보류 {v['pgate']['잘못된보류']}, 오귀속 n/f/p {v['naive']['오귀속']}/{v['fallback']['오귀속']}/{v['pgate']['오귀속']})"))
        out = args.out or str(ROOT / "results" / "e1_label_views.json")
        Path(out).write_text(json.dumps(views, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장: {out}")
        return 0

    rows = list(csv.DictReader(GOLD.open(encoding="utf-8-sig")))
    sub = rows if args.split == "all" else [r for r in rows if r["split"] == args.split]
    reviewed = [r for r in sub if r["reviewer"].strip()]
    manifest = load_manifest()

    res = {"split": args.split, "n": len(sub), "reviewed_n": len(reviewed), "modes": {}}
    print(f"[E1 출처 검증] split={args.split} n={len(sub)} (사람검수 {len(reviewed)})")
    hdr = f"{'모드':9}{'정확도':>7}{'오귀속률':>8}{'coverage':>9}{'RESOLVED정밀':>12}{'정당보류':>8}{'잘못보류':>8}{'판정정확':>8}"
    print(hdr)
    for mode in MODES:
        m = evaluate(sub, manifest, mode)
        res["modes"][mode] = m
        print(f"{mode:9}{m['정확도']:>7}{m['오귀속률']:>8}{m['coverage']:>9}{m['RESOLVED정밀도']:>12}"
              f"{str(m['정당한보류율']):>8}{str(m['잘못된보류율']):>8}{m['판정정확도']:>8}")
    res["transition_fallback_to_pgate"] = transition_table(sub, manifest)
    res["resolvability"] = resolvability_table(sub, manifest)
    res["ext"] = ext_table(sub, manifest)
    print("\nfallback→pgate 전환(fallback이 혁신법 기본귀속한 문항):")
    for k, v in res["transition_fallback_to_pgate"].items():
        print(f"  {k}: {v['n']}")
    if args.out:
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
