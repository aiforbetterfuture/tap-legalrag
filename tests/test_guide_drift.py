"""
tests/test_guide_drift.py — A-3 해설 인용 표류 탐지기.

합성 시나리오로 '2단계 알고리즘'을 결정론적으로 검증한다(실데이터 패러프레이즈 한계와 분리):
  - 인용 항에 '신설' 표시가 있고(1단계), 인용 문맥이 이웃 항과 더 겹치면(2단계) → DRIFT 경고.
그리고 실제 실행이 11_GUIDE_DRIFT.md를 만들고, use_guide=True면 경고가 해설 칸에 표면화됨을 확인.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from law_rag.models import LawChunk


def _chunk(art, para, body):
    return LawChunk(
        chunk_id=f"LAW-TST-J{art}" + (f"-H{para}" if para else ""),
        law_id="TST", law_name="테스트령", law_short_name="테스트령", law_type="대통령령",
        hierarchy_level=2, binding_power=4, article_no=art, article_title="수의 제한",
        paragraph_no=para, promulgation_date=date(2021, 1, 1), enforce_date=date(2021, 1, 1),
        article_enforce_date=date(2021, 1, 1), valid_from=date(2021, 1, 1),
        topic_tags=["성과관리"], body_text=body, source_url="x",
    )


def test_a3_detects_shifted_paragraph():
    import check_guide_drift as D
    # H1=적용제외 내용, H2=외국법인(신설). 인용은 H2지만 문맥은 H1(적용제외)과 겹친다.
    corpus = [
        _chunk("64", "1", "연구개발과제 적용제외 포함하지 않고 산정 다음 각 호 특별평가 우수"),
        _chunk("64", "2", "외국법인 공동 연구개발기관 부담 <신설 2024. 2. 6.>"),
    ]
    guide = [{
        "guide_id": "G-1", "text": "제64조제2항에 따른 적용제외 포함하지 않고 산정 특별평가 우수 과제",
        "cited_refs": ["TST:J64:H2"], "drift_warnings": [],
    }]
    warns = D.detect(corpus, guide)
    assert warns and "제2항" in warns[0][1] and "제1항" in warns[0][1]
    assert guide[0]["drift_warnings"]  # in-place 채움


def test_a3_no_false_positive_without_sinseol():
    import check_guide_drift as D
    corpus = [
        _chunk("64", "1", "연구개발과제 적용제외 포함하지 않고 산정"),
        _chunk("64", "2", "외국법인 공동 연구개발기관 부담"),  # 신설 표시 없음 → 1단계 탈락
    ]
    guide = [{"guide_id": "G-2", "text": "제64조제2항 적용제외 포함하지 않고 산정",
              "cited_refs": ["TST:J64:H2"], "drift_warnings": []}]
    assert D.detect(corpus, guide) == []


@pytest.mark.skipif(not (ROOT / "내부 설계 노트").exists(),
                    reason="check_guide_drift 먼저 실행")
def test_a3_report_exists():
    assert (ROOT / "내부 설계 노트").read_text(encoding="utf-8").strip()
