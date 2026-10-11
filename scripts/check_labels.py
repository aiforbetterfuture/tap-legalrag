"""
scripts/check_labels.py — 라벨·데이터셋 정합성 검사 (작업지시서 30 P0-1).

세 CSV(gold·review·adjudicated)가 서로 어긋나지 않는지, 사람 확인 5건이 확정본에 반영됐는지,
pending(미확정) 항목이 평가에 섞이지 않는지 검사한다. 값은 바꾸지 않는다(검사·보고만).
사용: python scripts/check_labels.py  (종료코드: 실패 1)
"""
from __future__ import annotations

import csv
import io
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
E = ROOT / "data" / "eval"
GOLD, REVIEW, ADJ = E / "provenance_gold.csv", E / "provenance_review_R1.csv", E / "provenance_adjudicated.csv"

# 사용자 확인 5건(2026-10-11 01:02) — 확정본에 이 값이 들어 있어야 한다.
CONFIRMED = {
    "PG-002": {"final_law": "UNRESOLVABLE", "context_resolvable": "N"},
    "PG-003": {"final_law": "UNRESOLVABLE", "context_resolvable": "N"},
    "PG-013": {"context_resolvable": "N"},
    "PG-027": {"context_resolvable": "Y"},
    "PG-050": {"context_resolvable": "N"},
}
PENDING_TOKENS = {"pending", "PENDING", "미확정", "TBD", "확정대기"}


def _load(p):
    return list(csv.DictReader(p.open(encoding="utf-8-sig")))


def check():
    issues, notes = [], []
    gold, review, adj = _load(GOLD), _load(REVIEW), _load(ADJ)

    # 1) 행 수·중복·조인
    for name, rows in (("gold", gold), ("review", review), ("adjudicated", adj)):
        if len(rows) != 100:
            issues.append(f"{name} 행수 {len(rows)}≠100")
        dup = [k for k, v in Counter(r["pg_id"] for r in rows).items() if v > 1]
        if dup:
            issues.append(f"{name} 중복 pg_id {dup}")
    gids, rids, aids = ({r["pg_id"] for r in x} for x in (gold, review, adj))
    if not (gids == rids == aids):
        issues.append(f"조인 실패: gold∖adj={sorted(gids-aids)} adj∖gold={sorted(aids-gids)}")

    # 2) 허용 값·빈 값
    for r in adj:
        if not r["final_law"].strip():
            issues.append(f"{r['pg_id']} final_law 빈값")
        if r["context_resolvable"].strip().upper() not in ("Y", "N"):
            issues.append(f"{r['pg_id']} context_resolvable 허용 외: {r['context_resolvable']!r}")
        for col in ("final_law", "context_resolvable", "source"):
            if r[col].strip() in PENDING_TOKENS:
                issues.append(f"{r['pg_id']} {col}=pending — 평가 제외 대상")

    # 3) source 분포 = 일치 74 / R1유지 16 / 정답표채택 8 / 제3안 2
    dist = dict(Counter(r["source"].strip() for r in adj))
    expect = {"일치": 74, "R1유지": 16, "정답표채택": 8, "제3안": 2}
    if dist != expect:
        issues.append(f"source 분포 {dist} ≠ {expect}")
    notes.append(f"source 분포: {dist}")

    # 4) source=일치 26건 아님(=불일치 26) = review 엄격_일치=N 26건과 맞물리는지
    adj_by = {r["pg_id"]: r for r in adj}
    rev_strict_n = sum(1 for r in review if r["엄격_일치"].strip() == "N")
    not_match = sum(1 for r in adj if r["source"].strip() != "일치")
    notes.append(f"adjudicated source≠일치 {not_match}건 · review 엄격_일치=N {rev_strict_n}건")
    if not_match != 26 or rev_strict_n != 26:
        issues.append(f"불일치 건수 불일치: adj≠일치 {not_match}, review N {rev_strict_n} (각 26 기대)")

    # 5) context_resolvable=N 분포
    cn = sum(1 for r in adj if r["context_resolvable"].strip().upper() == "N")
    notes.append(f"context_resolvable=N {cn}건 (기대 21)")
    if cn != 21:
        issues.append(f"context_resolvable=N {cn}≠21")

    # 6) 사용자 확인 5건 반영 + revision_log 기록
    for pid, want in CONFIRMED.items():
        r = adj_by.get(pid)
        if not r:
            issues.append(f"확정본에 {pid} 없음"); continue
        for col, val in want.items():
            if r[col].strip() != val:
                issues.append(f"{pid} {col}={r[col].strip()!r} ≠ 확인값 {val!r}")
        if "2026-10-11" not in r.get("revision_log", ""):
            issues.append(f"{pid} revision_log에 확인 날짜 없음")
    return issues, notes


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    issues, notes = check()
    for n in notes:
        print(f"  · {n}")
    if issues:
        print("\n[라벨 정합성 실패]")
        for i in issues:
            print(f"  ✗ {i}")
        return 1
    print("\n라벨·데이터셋 정합성 통과(세 CSV 100행·조인·분포·확인 5건·pending 0).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
