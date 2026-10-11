"""
scripts/diff_corpus.py — 코퍼스 스냅샷 비교(개정 전후 조문 변경 추적, P1B-4).

무엇을:
    각 청크의 (chunk_id → version_hash) 스냅샷을 저장하고, 이전 스냅샷과 비교해
    추가(added)·삭제(removed)·변경(changed) 조문 목록을 만든다.
왜:
    시행일 기준 재수집(P1B-2) 후 "무엇이 실제로 바뀌었는가"를 조문 단위로 보여줘야
    개정 반영을 신뢰할 수 있다(특히 혁신법 시행령 8/20 개정).

사용:
    python scripts/diff_corpus.py            # 현재 코퍼스 vs 최신 스냅샷 비교 → 09_CORPUS_DIFF.md
    python scripts/diff_corpus.py --save      # 현재 코퍼스를 새 스냅샷으로 저장(baseline)
스냅샷: data/snapshots/corpus_YYYYMMDD_HHMM.json (gitignore 대상 아님 — 이력 근거)
"""
from __future__ import annotations

import datetime as dt
import io
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from law_rag.knowledge.loader import load_corpus  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
SNAP_DIR = ROOT / "data" / "snapshots"
DIFF_MD = ROOT / "docs" / "내부 설계 노트" / "09_CORPUS_DIFF.md"


def current_snapshot() -> dict[str, dict]:
    corpus = load_corpus(FIXTURES, split=True)
    return {
        c.chunk_id: {
            "hash": c.version_hash, "law_id": c.law_id,
            "law": c.law_short_name or c.law_name, "article": c.article_no,
            "para": c.paragraph_no,
        }
        for c in corpus
    }


def _latest_snapshot() -> Path | None:
    if not SNAP_DIR.exists():
        return None
    snaps = sorted(SNAP_DIR.glob("corpus_*.json"))
    return snaps[-1] if snaps else None


def save_snapshot(snap: dict) -> Path:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    p = SNAP_DIR / f"corpus_{dt.datetime.now():%Y%m%d_%H%M}.json"
    p.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def diff(old: dict, new: dict):
    added = [k for k in new if k not in old]
    removed = [k for k in old if k not in new]
    changed = [k for k in new if k in old and old[k]["hash"] != new[k]["hash"]]
    return added, removed, changed


def _by_law(ids, snap):
    from collections import Counter
    return dict(Counter(snap[i]["law"] for i in ids if i in snap))


def write_report(old_path, old, new, added, removed, changed) -> None:
    DIFF_MD.parent.mkdir(parents=True, exist_ok=True)
    laws = sorted({v["law"] for v in new.values()})
    lines = [
        "# 코퍼스 변경 요약 (09_CORPUS_DIFF)", "",
        f"- 생성: {dt.datetime.now():%Y-%m-%d %H:%M}",
        f"- 비교 기준(이전 스냅샷): {old_path.name if old_path else '(없음 — 최초 baseline)'}",
        f"- 전체 청크: 이전 {len(old)} → 현재 {len(new)}", "",
        "## 법령별 변경 조문 수", "",
        "| 법령 | 추가 | 삭제 | 변경 |", "|---|---|---|---|",
    ]
    a, r, c = _by_law(added, new), _by_law(removed, old), _by_law(changed, new)
    for law in laws:
        lines.append(f"| {law} | {a.get(law,0)} | {r.get(law,0)} | {c.get(law,0)} |")
    lines += ["", f"합계: 추가 {len(added)} · 삭제 {len(removed)} · 변경 {len(changed)}", ""]
    if changed[:40]:
        lines += ["## 변경된 조문(일부)", ""]
        for k in changed[:40]:
            lines.append(f"- {new[k]['law']} {new[k]['article']}조"
                         + (f"제{new[k]['para']}항" if new[k]["para"] else "") + f"  ({k})")
    DIFF_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    save = "--save" in sys.argv
    new = current_snapshot()
    old_path = _latest_snapshot()
    old = json.loads(old_path.read_text(encoding="utf-8")) if old_path else {}

    added, removed, changed = diff(old, new)
    write_report(old_path, old, new, added, removed, changed)
    print(f"이전 {len(old)} → 현재 {len(new)} | 추가 {len(added)} 삭제 {len(removed)} 변경 {len(changed)}")
    print(f"리포트: {DIFF_MD.relative_to(ROOT)}")

    if save or old_path is None:
        p = save_snapshot(new)
        print(f"스냅샷 저장: {p.relative_to(ROOT)}" + (" (최초 baseline)" if old_path is None else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
