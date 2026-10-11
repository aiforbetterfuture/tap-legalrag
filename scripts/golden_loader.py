"""
scripts/golden_loader.py — 골든셋 CSV 로더.

골든셋 CSV 열(헤더 한글):
    id, 질문, 기준시점, 기대_질의유형, 기대_주제,
    정답_근거_chunk_id, 정답_요지, 기대_신뢰도, 관계_필요, 비고

- 정답_근거_chunk_id: 쉼표로 여러 개. '(없음)'이면 진성 음성표본(근거 없음이 정답).
- 기준시점: 'YYYY-MM-DD' 또는 '현행'(빈칸/현행 → None).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DEFAULT_PATH = Path(__file__).parent.parent / "tests" / "golden" / "golden_set.csv"

# '(없음)' 계열 — 근거 없음이 정답인 행
_NONE_TOKENS = {"", "(없음)", "없음", "none", "N/A", "-"}


@dataclass
class GoldenRow:
    id: str
    question: str
    as_of: date | None
    expected_kind: str
    expected_topic: str
    gold_chunk_ids: list[str] = field(default_factory=list)
    gold_summary: str = ""
    expected_confidence: str = ""
    needs_relation: str = ""
    note: str = ""

    @property
    def is_negative(self) -> bool:
        """근거 없음이 정답인 행(환각 차단선 검증용)."""
        return len(self.gold_chunk_ids) == 0


def _parse_asof(raw: str) -> date | None:
    raw = (raw or "").strip()
    if raw in ("", "현행", "current"):
        return None
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return _strptime(raw, fmt)
        except ValueError:
            continue
    # 연도만 주어진 경우 그 해 1월 1일
    if raw.isdigit() and len(raw) == 4:
        return date(int(raw), 1, 1)
    return None


def _strptime(s: str, fmt: str) -> date:
    from datetime import datetime
    return datetime.strptime(s, fmt).date()


def _parse_ids(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if raw in _NONE_TOKENS:
        return []
    return [x.strip() for x in raw.split(",") if x.strip() and x.strip() not in _NONE_TOKENS]


def load_golden(path: str | Path = DEFAULT_PATH) -> list[GoldenRow]:
    """골든셋 CSV → GoldenRow 리스트. 파일 없으면 FileNotFoundError."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"골든셋 CSV가 없습니다: {p}\n"
            "엑셀에서 '다른 이름으로 저장 → CSV UTF-8'로 저장해 넣어주세요.\n"
            "열: id, 질문, 기준시점, 기대_질의유형, 기대_주제, "
            "정답_근거_chunk_id, 정답_요지, 기대_신뢰도, 관계_필요, 비고"
        )
    rows: list[GoldenRow] = []
    # utf-8-sig: 엑셀 CSV의 BOM 처리
    with p.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            g = {k.strip(): (v or "").strip() for k, v in r.items() if k}
            rid = g.get("id", "")
            if not rid:
                continue
            rows.append(GoldenRow(
                id=rid,
                question=g.get("질문", ""),
                as_of=_parse_asof(g.get("기준시점", "")),
                expected_kind=g.get("기대_질의유형", ""),
                expected_topic=g.get("기대_주제", ""),
                gold_chunk_ids=_parse_ids(g.get("정답_근거_chunk_id", "")),
                gold_summary=g.get("정답_요지", ""),
                expected_confidence=g.get("기대_신뢰도", ""),
                needs_relation=g.get("관계_필요", ""),
                note=g.get("비고", ""),
            ))
    return rows
