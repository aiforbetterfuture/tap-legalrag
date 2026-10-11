"""
scripts/eval_human_review.py — P 정답표 사람 독립 검수 R1 반영 (작업지시서 26 §2, 100건).

사람 검수 원본 엑셀은 읽지 않는다(개인정보). 작업보조 LLM가 넣어 둔 비교표
`data/eval/provenance_review_R1.csv`(100행 = 1차 20 + 2차 80)만 사용한다. 여기서 재현·확인하는 것:
  - 일치율(엄격/완화)을 전체·test·dev·차수별로 집계하고, 열 집계 == 재계산 교차검사
  - 2차 80건의 `R1_판단근거`별 일치(인용 안 법령명 / 앞 문장 법령명 / 근거 없음 / 업무 지식)
  - pgate '잘못된 보류' 17건을 사람 확인으로 분해: ① 사전 누락 10(사람 10/10 올바른 법령) · ② 문맥 밖 7(사람 7/7 판단 불가)
  - 민감도 분석(② 7건을 정당한 보류로 볼 때 판정 정확도)
동결 수치(rc1)·판정 로직·golden은 바꾸지 않는다. 사용: python scripts/eval_human_review.py [--out PATH]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from law_rag.knowledge.loader import load_manifest  # noqa: E402
from law_rag.provenance import resolve  # noqa: E402

REVIEW = ROOT / "data" / "eval" / "provenance_review_R1.csv"
GOLD = ROOT / "data" / "eval" / "provenance_gold.csv"

# 잘못된 보류 17건의 사람 확인 묶음(작업지시서 26 §2.2 — 사람이 17건 전부 확인).
#   ① 법령명(연구개발비 사용 기준) 사전 누락 = 고칠 수 있는 공학적 결함.
#   ② 정답이 문맥 창 밖 정보에 의존 = 사람도 판단 불가(정답 정의 문제).
CONFIRMED_GROUP1 = ["PG-014", "PG-029", "PG-073", "PG-074", "PG-075",
                    "PG-078", "PG-079", "PG-080", "PG-083", "PG-084"]
CONFIRMED_GROUP2 = ["PG-003", "PG-010", "PG-015", "PG-018", "PG-025", "PG-028", "PG-034"]

# ① 규칙(참고용 비교): 인용/문맥에 괄호 없는 '(국가연구개발사업) 연구개발비 사용 기준'.
_USAGE_STD = re.compile(r"(?<![「『｢])(?:국가연구개발사업\s*)?연구개발비\s*사용\s*기준")
_NOT_CORPUS = ("", "UNRESOLVABLE", "판단불가", "판단 불가")


def _col(r, *names):
    for n in names:
        if n in r:
            return r[n].strip()
    return ""


def _norm(s: str) -> str:
    """외부 법령명 비교용: NFKC 정규화 + 가운뎃점(･・·ㆍ) 통일."""
    s = unicodedata.normalize("NFKC", (s or "").strip())
    return re.sub(r"[･・·ㆍ]", "·", s)


def _row_match(r, gold):
    """(엄격, 완화) 재계산. 완화 = 엄격 또는 (정답이 코퍼스 밖이고 사람도 코퍼스 법령에 안 붙임)."""
    g = gold.get(r["pg_id"], {})
    gl, al = _norm(r["gold_law"]), _norm(_col(r, "R1_law(해석)", "R1_law"))
    strict = (al == gl and gl != "")
    a_notcorpus = al in _NOT_CORPUS or al.startswith("EXT:")
    lenient = strict or (g.get("gold_in_corpus", "").strip().upper() == "N" and a_notcorpus)
    return strict, lenient


def _agree_subset(rows, gold):
    """부분집합의 열 집계·재계산·교차검사."""
    cs = sum(1 for r in rows if _col(r, "엄격_일치") == "Y")
    cl = sum(1 for r in rows if _col(r, "완화_일치") == "Y")
    rs = rl = 0
    disagree = []
    for r in rows:
        s, l = _row_match(r, gold)
        rs += int(s); rl += int(l)
        if (_col(r, "엄격_일치") == "Y") != s:
            disagree.append((r["pg_id"], "strict"))
        if (_col(r, "완화_일치") == "Y") != l:
            disagree.append((r["pg_id"], "lenient"))
    return {"n": len(rows), "엄격": cs, "완화": cl,
            "재계산_엄격": rs, "재계산_완화": rl,
            "열=재계산": cs == rs and cl == rl, "불일치_문항": disagree}


def _agreement(rv, gold=None):
    """전체·test·dev·차수별 일치율. gold 생략 시 파일에서 로드(테스트 호환)."""
    if gold is None:
        gold = {r["pg_id"]: r for r in csv.DictReader(GOLD.open(encoding="utf-8-sig"))}
    def sub(pred):
        return _agree_subset([r for r in rv if pred(r)], gold)
    def split(r):
        return gold.get(r["pg_id"], {}).get("split", "?")
    out = {"전체": sub(lambda r: True),
           "test": sub(lambda r: split(r) == "test"),
           "dev": sub(lambda r: split(r) == "dev"),
           "1차": sub(lambda r: _col(r, "차수") == "1차"),
           "2차": sub(lambda r: _col(r, "차수") == "2차")}
    out["열=재계산"] = all(out[k]["열=재계산"] for k in ("전체", "test", "dev"))
    return out


def _by_judgment_basis(rv):
    """2차 80건을 R1_판단근거별로 엄격 일치 집계."""
    sec = [r for r in rv if _col(r, "차수") == "2차"]
    by = {}
    for r in sec:
        k = _col(r, "R1_판단근거") or "(미기재)"
        d = by.setdefault(k, {"n": 0, "엄격": 0})
        d["n"] += 1
        d["엄격"] += int(_col(r, "엄격_일치") == "Y")
    return by


def _by_resolvability(rv, gold):
    by = {}
    for r in rv:
        k = gold.get(r["pg_id"], {}).get("resolvability", "?") or "?"
        d = by.setdefault(k, {"n": 0, "엄격": 0})
        d["n"] += 1
        d["엄격"] += int(_col(r, "엄격_일치") == "Y")
    return by


def _split_17_by_rule(ww_ids, gold_rows):
    """17건을 규칙으로 ①(사용기준)/②(그 외)로 나눈다(참고 비교용)."""
    by_id = {r["pg_id"]: r for r in gold_rows}
    g1, g2 = [], []
    for pid in sorted(ww_ids):
        r = by_id.get(pid, {})
        text = (r.get("raw_citation", "") + " " + r.get("context", ""))
        (g1 if _USAGE_STD.search(text) else g2).append(pid)
    return g1, g2


def _wrong_withhold_ids(man):
    pg = list(csv.DictReader(GOLD.open(encoding="utf-8-sig")))
    test = [r for r in pg if r["split"] == "test"]
    ids = []
    for r in test:
        resolvable = r["gold_in_corpus"].strip().upper() == "Y" and not r["gold_law"].startswith("EXT:")
        if not resolvable:
            continue
        ref = resolve(r["raw_citation"], r.get("context", ""), man, mode="pgate")
        if not (ref.status == "RESOLVED" and ref.law_id):
            ids.append(r["pg_id"])
    return set(ids)


def _human_confirm_17(rv):
    """17건 사람 확인: ① 사람이 '올바른 법령'(엄격 일치 Y)으로 본 수, ② 사람이 '판단 불가'(UNRESOLVABLE)로 본 수."""
    by = {r["pg_id"]: r for r in rv}
    g1_ok = sum(1 for i in CONFIRMED_GROUP1 if _col(by.get(i, {}), "엄격_일치") == "Y")
    g2_na = sum(1 for i in CONFIRMED_GROUP2
                if _norm(_col(by.get(i, {}), "R1_law(해석)", "R1_law")) in _NOT_CORPUS
                or _norm(_col(by.get(i, {}), "R1_law(해석)", "R1_law")).startswith("EXT:"))
    return g1_ok, g2_na


def _sensitivity(man):
    """② 7건을 정당한 보류로 재분류했을 때 판정 정확도(민감도). test 기준, 원 정의 수치 별도 보존."""
    from eval_provenance import classify
    pg = list(csv.DictReader(GOLD.open(encoding="utf-8-sig")))
    test = [r for r in pg if r["split"] == "test"]
    out = {}
    g2 = set(CONFIRMED_GROUP2)  # 사람 확인 ②(7건)
    for mode in ("fallback", "pgate"):
        correct = just = extra = 0
        for r in test:
            o = classify(resolve(r["raw_citation"], r.get("context", ""), man, mode=mode), r)
            if o == "CORRECT":
                correct += 1
            elif o == "JUST_WITHHOLD":
                just += 1
            elif o == "WRONG_WITHHOLD" and r["pg_id"] in g2:
                extra += 1
        out[mode] = {"원정의_판정정확도": round((correct + just) / len(test), 3),
                     "민감도_판정정확도": round((correct + just + extra) / len(test), 3)}
    return out


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args()

    rv = list(csv.DictReader(REVIEW.open(encoding="utf-8-sig")))
    gold = {r["pg_id"]: r for r in csv.DictReader(GOLD.open(encoding="utf-8-sig"))}
    gold_rows = list(gold.values())
    man = load_manifest()

    agr = _agreement(rv, gold)
    if not agr["열=재계산"]:
        bad = {k: agr[k]["불일치_문항"] for k in ("전체", "test", "dev") if agr[k]["불일치_문항"]}
        print(f"[경고] 열 집계와 재계산이 다름: {bad}")
    basis = _by_judgment_basis(rv)
    resolv = _by_resolvability(rv, gold)
    mismatch = dict(Counter(r["불일치_유형"].strip() for r in rv if r["불일치_유형"].strip()))

    ww = _wrong_withhold_ids(man)
    g1r, g2r = _split_17_by_rule(ww, gold_rows)       # 규칙 산출(참고)
    g1_ok, g2_na = _human_confirm_17(rv)              # 사람 확인
    sens = _sensitivity(man)

    def fr(d):
        return {"n": d["n"], "엄격": f"{d['엄격']}/{d['n']}", "완화": f"{d['완화']}/{d['n']}"}

    res = {
        "n_reviewed": len(rv), "reviewer": "R1(사용자 1명, 정답 비공개 독립 판정, 1차 20 + 2차 80)",
        "agreement": {k: fr(agr[k]) for k in ("전체", "test", "dev", "1차", "2차")},
        "agreement_열=재계산": agr["열=재계산"],
        "by_judgment_basis_2차": {k: f"{v['엄격']}/{v['n']}" for k, v in basis.items()},
        "by_resolvability": {k: f"{v['엄격']}/{v['n']}" for k, v in resolv.items()},
        "mismatch_types": mismatch,
        "wrong_withhold": {
            "n": len(ww),
            "group1_pre_dict_miss": {"n": len(CONFIRMED_GROUP1), "ids": CONFIRMED_GROUP1,
                                     "사람_올바른법령": f"{g1_ok}/{len(CONFIRMED_GROUP1)}"},
            "group2_outside_context": {"n": len(CONFIRMED_GROUP2), "ids": CONFIRMED_GROUP2,
                                       "사람_판단불가": f"{g2_na}/{len(CONFIRMED_GROUP2)}"},
            "rule_split_for_reference": {"①": len(g1r), "②": len(g2r),
                                         "diff_vs_human": "PG-025(규칙 ①, 사람 ② 지지)"},
        },
        "sensitivity": sens,
    }
    a = agr
    print(f"[사람 검수 R1] 검수 {len(rv)}건(R1 1명, 1차 20 + 2차 80)")
    print(f"  일치율: 전체 엄격 {a['전체']['엄격']}/100·완화 {a['전체']['완화']}/100"
          f" | test {a['test']['엄격']}/60·{a['test']['완화']}/60 | dev {a['dev']['엄격']}/40·{a['dev']['완화']}/40"
          f"  (열=재계산 {agr['열=재계산']})")
    print(f"  판단근거별(2차): " + " · ".join(f"{k} {v['엄격']}/{v['n']}" for k, v in basis.items()))
    print(f"  pgate 잘못된 보류 {len(ww)}건 — 사람 확인: ① 사전누락 {g1_ok}/10 올바른 법령 · ② 문맥밖 {g2_na}/7 판단 불가")
    print(f"    (참고 규칙 분해 ①{len(g1r)}/②{len(g2r)}; 차이 PG-025 — 사람은 ② 지지)")
    print(f"  민감도(②=7 정당보류): fallback {sens['fallback']['민감도_판정정확도']} / pgate {sens['pgate']['민감도_판정정확도']}")
    if args.out:
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
