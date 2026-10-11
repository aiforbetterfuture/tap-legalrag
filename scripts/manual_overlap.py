"""
scripts/manual_overlap.py — 매뉴얼 원문 겹침 검사 공용 모듈 (작업지시서 30 P0-3).

공공누리 제4유형 매뉴얼(`data/guide/*.json`)의 '본문·질문·답변' 텍스트를 모아,
공개 후보 텍스트에 **공백 제거 기준 연속 30자 이상** 겹치는 구간이 있는지 찾는다.
원문은 출력하지 않는다(위치·길이만). export(겹치는 셀 비우기)와 scan(0건 확인)이 함께 쓴다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUIDE_DIR = ROOT / "data" / "guide"
MIN_RUN = 30  # 연속 일치 임계(공백 제거 기준)

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub("", s or "")


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("text", "question", "answer", "section_path", "doc_title"):
                yield from _strings(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from _strings(x)


def load_manual_ngrams(guide_dir: Path = GUIDE_DIR, n: int = MIN_RUN) -> set[str]:
    """매뉴얼 텍스트의 정규화 n-그램 집합. 매뉴얼이 없으면 빈 집합."""
    grams: set[str] = set()
    if not guide_dir.exists():
        return grams
    for f in sorted(guide_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for s in _strings(data):
            t = _norm(s)
            for i in range(0, max(0, len(t) - n + 1)):
                grams.add(t[i:i + n])
    return grams


def overlap_runs(text: str, grams: set[str], n: int = MIN_RUN) -> int:
    """text에 매뉴얼 n-그램과 일치하는 구간이 몇 '곳' 있는지(겹치는 시작 위치 수)."""
    if not grams:
        return 0
    t = _norm(text)
    hits = 0
    i = 0
    L = len(t)
    while i <= L - n:
        if t[i:i + n] in grams:
            hits += 1
            i += n  # 한 구간을 건너뛰어 과대계수 방지
        else:
            i += 1
    return hits


def cell_overlaps(value: str, grams: set[str], n: int = MIN_RUN) -> bool:
    return overlap_runs(value, grams, n) > 0
