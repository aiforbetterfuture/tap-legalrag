"""
tests/conftest.py — 공개판(공공누리 제4유형: 매뉴얼 텍스트 제외) 대응 (작업지시서 30 P0-3).

개발 저장소에는 매뉴얼 유래 텍스트(gold CSV의 context 등)가 있어 모든 평가 재현 테스트가 돈다.
공개판(reduced)에서는 그 텍스트가 비워지므로, 매뉴얼 원문이 있어야만 재현되는 테스트는
`@requires_manual`로 표시해 **skip**한다(사유·skip 수는 CI 로그에 남는다). 테스트를 약화하지 않는다.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _manual_available() -> bool:
    """매뉴얼 원문(공공누리 제4유형)이 이 환경에 있는가.

    공개판(reduced export)은 `data/guide/`(매뉴얼 본문)를 통째로 제외하고 gold `context`를 비우며,
    표식 파일 `data/PUBLIC_REDUCED.txt`를 남긴다(C7). 그 표식이 있으면 공개판으로 간주해 skip.
    """
    if (ROOT / "data" / "PUBLIC_REDUCED.txt").exists():  # C7: 공개판 표식
        return False
    guide = ROOT / "data" / "guide"
    if not guide.exists() or not any(guide.glob("*.json")):
        return False
    # 비어 있지 않은(본문 텍스트가 있는) 청크 파일이 하나라도 있어야 함.
    import json
    for f in guide.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, list) and any(x.get("text") for x in data if isinstance(x, dict)):
            return True
    return False


MANUAL_AVAILABLE = _manual_available()

requires_manual = pytest.mark.skipif(
    not MANUAL_AVAILABLE,
    reason="requires_manual: 매뉴얼 원문(공공누리 제4유형)이 공개판에서 제외됨 — 결과 JSON·코드로 검토",
)
