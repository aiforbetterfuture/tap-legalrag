"""
scripts/build_golden_guide_map.py — 골든 ↔ 해설(BON) 질문 매핑 (R4).

왜:
    골든셋 test 문항이 '매뉴얼의 어느 Q&A에서 왔는지' 알아야, 그 해설을 검색에서 빼고
    '법령만 vs 법령+해설'을 공정하게 비교할 수 있다(A-1). 번호가 아니라 '질문 문장'으로 매칭한다.

입력(읽기만): tests/golden/golden_set_v3_draft_manual.csv(UTF-8 BOM), data/guide/BON.json
출력: data/guide/golden_guide_map.json  (M-id → {guide_id, ratio})
      data/guide/exclude_test.json      (test 분할 문항의 guide_id 목록)
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden" / "golden_set_v3_draft_manual.csv"
BON = ROOT / "data" / "guide" / "BON.json"
OUT_MAP = ROOT / "data" / "guide" / "golden_guide_map.json"
OUT_EXCL = ROOT / "data" / "guide" / "exclude_test.json"

MATCH_THRESHOLD = 0.9
_RE_NORM = re.compile(r"[\s「」『』()（）\[\]·,.?？!·:：;\"'…\-]+")


def _norm(s: str) -> str:
    """질문 정규화: 공백·괄호·구두점 제거. '예시: …' 부가설명도 약화."""
    s = (s or "").strip()
    s = re.sub(r"\(예시\s*[:：][^)]*\)", "", s)
    return _RE_NORM.sub("", s)


def main() -> int:
    rows = list(csv.DictReader(GOLDEN.open(encoding="utf-8-sig")))
    bon = json.loads(BON.read_text(encoding="utf-8"))
    bon_norm = [(c["guide_id"], _norm(c.get("question") or "")) for c in bon]

    mapping: dict[str, dict] = {}
    fails: list[str] = []
    m_rows = [r for r in rows if r["id"].startswith("M-") and r["최종판정(1차)"] != "REMOVE"]
    for r in m_rows:
        qn = _norm(r["질문"])
        best_gid, best = None, 0.0
        for gid, bn in bon_norm:
            if not bn:
                continue
            ratio = SequenceMatcher(None, qn, bn).ratio()
            if ratio > best:
                best, best_gid = ratio, gid
        if best >= MATCH_THRESHOLD:
            mapping[r["id"]] = {"guide_id": best_gid, "ratio": round(best, 3)}
        else:
            fails.append(f"{r['id']}(best {best:.2f}, {r['질문'][:30]})")

    # 중복 매칭 검사
    from collections import Counter
    gid_count = Counter(v["guide_id"] for v in mapping.values())
    dups = {g: n for g, n in gid_count.items() if n > 1}

    # test 분할 문항의 guide_id
    test_ids = {r["id"] for r in rows if r.get("분할") == "test"}
    exclude = sorted({mapping[mid]["guide_id"] for mid in mapping if mid in test_ids})

    OUT_MAP.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_EXCL.write_text(json.dumps(exclude, ensure_ascii=False, indent=2), encoding="utf-8")

    rate = len(mapping) / len(m_rows) if m_rows else 0.0
    print(f"M-* {len(m_rows)}개 중 매칭 {len(mapping)} ({rate*100:.1f}%), 실패 {len(fails)}, 중복 {len(dups)}")
    print(f"test 분할 제외 guide_id {len(exclude)}개 → {OUT_EXCL.name}")
    if dups:
        print("중복 매칭:", dups)
    if fails:
        print("실패 목록:")
        for f in fails:
            print("  -", f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
