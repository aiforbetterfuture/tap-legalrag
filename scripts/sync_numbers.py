"""
scripts/sync_numbers.py — 문서 숫자 자동 대조 (작업지시서 24 §A-5).

결과 JSON(단일 출처)에서 핵심 수치를 읽어 `docs/NUMBERS.md`를 만들고,
RESULTS·HUMAN_REVIEW·README 등 문서에 그 수치가 그대로 들어 있는지 검사한다. 다르면 실패.
사용: python scripts/sync_numbers.py [--write]  (--write면 NUMBERS.md 갱신)
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
DOCS = ROOT / "docs"


def canonical() -> dict:
    """결과 파일에서 핵심 수치를 뽑는다(단일 출처)."""
    e3rc1 = json.loads((RESULTS / "e3_decision.json").read_text(encoding="utf-8"))["configs"]
    e3rc3 = json.loads((RESULTS / "e3_decision_rc3.json").read_text(encoding="utf-8"))["configs"]
    e1 = json.loads((RESULTS / "e1_provenance_test_v2.json").read_text(encoding="utf-8"))["modes"]
    hr = json.loads((RESULTS / "e1_human_review_R1.json").read_text(encoding="utf-8"))
    lv = json.loads((RESULTS / "e1_label_views.json").read_text(encoding="utf-8"))["test"]
    return {
        "E3 rc3 B0 결정정확도": e3rc3["B0"]["결정정확도"],
        "E3 rc3 B4 결정정확도": e3rc3["B4"]["결정정확도"],
        "E3 rc3 B4 macro_F1": e3rc3["B4"]["macro_F1"],
        "E3 rc3 B4 답변불가ANSWER": e3rc3["B4"]["답변불가_ANSWER선택"],
        "E3 rc1 B4 결정정확도": e3rc1["B4"]["결정정확도"],
        "E1 pgate 오귀속률": e1["pgate"]["오귀속률"],
        "E1 fallback 오귀속률": e1["fallback"]["오귀속률"],
        "E1 pgate RESOLVED정밀도": e1["pgate"]["RESOLVED정밀도"],
        "검수 엄격 전체": hr["agreement"]["전체"]["엄격"],
        "검수 완화 전체": hr["agreement"]["전체"]["완화"],
        "검수 엄격 test": hr["agreement"]["test"]["엄격"],
        "검수 완화 test": hr["agreement"]["test"]["완화"],
        "잘못된 보류 n": hr["wrong_withhold"]["n"],
        "E1 gold fallback test": lv["gold"]["fallback"]["판정정확도"],
        "E1 final pgate test": lv["final"]["pgate"]["판정정확도"],
        "E1 context pgate test": lv["context"]["pgate"]["판정정확도"],
    }


# (문서, 반드시 들어 있어야 하는 canonical 키 목록)
CHECKS = [
    ("README.md", ["E3 rc3 B4 결정정확도", "E3 rc3 B4 macro_F1", "E3 rc3 B0 결정정확도",
                   "E1 pgate 오귀속률", "검수 엄격 test", "검수 완화 test",
                   "E1 final pgate test", "E1 context pgate test"]),
    ("docs/RESULTS.md", ["E3 rc3 B4 결정정확도", "E3 rc3 B4 macro_F1", "E3 rc3 B0 결정정확도",
                         "E1 pgate 오귀속률", "검수 엄격 전체", "검수 완화 전체",
                         "E1 final pgate test", "E1 context pgate test"]),
    ("docs/HUMAN_REVIEW.md", ["검수 엄격 전체", "검수 완화 전체", "검수 엄격 test",
                             "검수 완화 test", "잘못된 보류 n"]),
]


def _as_str(v) -> str:
    return str(v)


def check():
    c = canonical()
    missing = []
    for rel, keys in CHECKS:
        p = ROOT / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        for k in keys:
            if _as_str(c[k]) not in text:
                missing.append((rel, k, c[k]))
    return c, missing


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    write = "--write" in sys.argv
    c, missing = check()
    if write:
        lines = ["# NUMBERS — 결과 파일에서 뽑은 공식 수치 (단일 출처)", "",
                 "> `scripts/sync_numbers.py`가 자동 생성·검사. 문서 수치가 아래와 다르면 테스트 실패.", "",
                 "| 항목 | 값 |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in c.items()]
        (DOCS / "NUMBERS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"작성: docs/NUMBERS.md ({len(c)}개 수치)")
    for k, v in c.items():
        print(f"  {k}: {v}")
    if missing:
        print("\n[불일치] 문서에서 누락/다름:")
        for rel, k, v in missing:
            print(f"  {rel}: '{k}' = {v} 없음")
        return 1
    print("\n문서 수치 대조 통과.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
