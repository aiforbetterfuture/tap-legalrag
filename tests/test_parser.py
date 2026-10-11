"""
tests/test_parser.py — SPEC-1 법령 XML 파서 검증.

'파싱 결과 전부가 LawChunk.model_validate를 통과하고,
 조/항/별표 개수가 원문과 일치한다.'

실행: PYTHONPATH=src pytest tests/test_parser.py -v
"""
from __future__ import annotations

from datetime import date

import pytest

from law_rag.knowledge.parser import parse_law_xml
from law_rag.models import LawChunk, LawType, TopicTag

# ── 테스트용 법률 XML ─────────────────────────────────────────
#   제12조(협약의 변경): 2항  → 2 청크
#   제20조(성과의 보고) : 1항, 조문시행일자 다름, 예외 조문 포함 → 1 청크
#   제99조            : 삭제(항 없음) → 1 청크
#   별표1             : → 1 청크
#   합계: 5 청크
SAMPLE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<법령>
  <기본정보>
    <법령명_한글>테스트법</법령명_한글>
    <법령종류>법률</법령종류>
    <공포일자>20200609</공포일자>
    <시행일자>20210101</시행일자>
    <법령ID>TST001</법령ID>
  </기본정보>
  <조문>
    <장>
      <장번호>제3장</장번호>
      <장제목>협약</장제목>
    </장>
    <조문단위>
      <조문번호>12</조문번호>
      <조문여부>본문</조문여부>
      <조문제목>협약의 변경</조문제목>
      <조문시행일자>20210101</조문시행일자>
      <항>
        <항번호>①</항번호>
        <항내용>중앙행정기관의 장과 연구개발기관의 장은 협약의 내용을 변경할 수 있다. 대통령령으로 정하는 중요한 사항은 미리 협의하여야 한다.</항내용>
      </항>
      <항>
        <항번호>②</항번호>
        <항내용>제1항에 따른 중요한 사항의 범위는 대통령령으로 정한다.</항내용>
      </항>
    </조문단위>
    <조문단위>
      <조문번호>20</조문번호>
      <조문여부>본문</조문여부>
      <조문제목>성과의 보고</조문제목>
      <조문시행일자>20240701</조문시행일자>
      <항>
        <항번호>①</항번호>
        <항내용>제12조에도 불구하고 긴급한 경우 연구개발기관의 장은 성과보고서를 사후에 제출할 수 있다. 별표1에서 정한 기준을 따른다.</항내용>
      </항>
    </조문단위>
    <조문단위>
      <조문번호>99</조문번호>
      <조문여부>삭제</조문여부>
      <조문제목>삭제</조문제목>
      <조문시행일자>20230101</조문시행일자>
    </조문단위>
  </조문>
  <별표>
    <별표단위>
      <별표번호>1</별표번호>
      <별표제목>비목별 한도</별표제목>
      <별표내용>| 비목 | 한도 |
| 인건비 | 20% |
| 재료비 | 30% |</별표내용>
    </별표단위>
  </별표>
</법령>
"""

META = {
    "law_id": "TST001",
    "law_type": "법률",
    "source_url_base": "https://example.com",
}

# ── 대통령령 XML: parent_law_ref / hierarchy 테스트용 ──────────
SIHAENGNYEONG_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<법령>
  <기본정보>
    <법령명_한글>테스트법 시행령</법령명_한글>
    <법령종류>대통령령</법령종류>
    <공포일자>20201229</공포일자>
    <시행일자>20210101</시행일자>
  </기본정보>
  <조문>
    <조문단위>
      <조문번호>15</조문번호>
      <조문여부>본문</조문여부>
      <조문제목>협약의 변경</조문제목>
      <조문시행일자>20210101</조문시행일자>
      <항>
        <항번호>①</항번호>
        <항내용>법 제12조제2항에 따라 미리 협의하여야 하는 중요한 사항은 다음 각 호와 같다. 1. 연구책임자 변경</항내용>
      </항>
    </조문단위>
  </조문>
</법령>
"""

META_SIHAENGNYEONG = {
    "law_id": "TST002",
    "law_type": "대통령령",
    "parent_law_name": "테스트법",
    "source_url_base": "https://example.com",
}


@pytest.fixture
def chunks():
    return parse_law_xml(SAMPLE_XML, META)


@pytest.fixture
def ryeong_chunks():
    return parse_law_xml(SIHAENGNYEONG_XML, META_SIHAENGNYEONG)


# ── 핵심: 모든 청크가 Pydantic 검증 통과 ─────────────────────
def test_all_chunks_validate(chunks):
    """파서 출력 전부가 LawChunk.model_validate를 통과하는가."""
    for c in chunks:
        LawChunk.model_validate(c.model_dump())


# ── 개수: 조/항/별표 개수가 원문과 일치 ──────────────────────
def test_chunk_count(chunks):
    """제12조(2항)+제20조(1항)+제99조(항없음 1청크)+별표1 = 5."""
    assert len(chunks) == 5


# ── 청크 ID 형식 ─────────────────────────────────────────────
def test_chunk_id_format(chunks):
    """chunk_id가 LAW-{law_id}-J{조}[-H{항}] / B{번호} 규칙을 따르는가."""
    ids = {c.chunk_id for c in chunks}
    assert "LAW-TST001-J12-H1" in ids   # 제12조 1항
    assert "LAW-TST001-J12-H2" in ids   # 제12조 2항
    assert "LAW-TST001-J20-H1" in ids   # 제20조 1항
    assert "LAW-TST001-J99"    in ids   # 삭제 조문(항 없음)
    assert "LAW-TST001-B1"     in ids   # 별표1


# ── 계층·구속력 (법률 = 1 / 5) ───────────────────────────────
def test_hierarchy_and_binding_법률(chunks):
    for c in chunks:
        assert c.hierarchy_level == 1
        assert c.binding_power == 5
        assert c.law_type == LawType.법률


# ── 장제목 추적 ───────────────────────────────────────────────
def test_chapter_section_tracked(chunks):
    """<장> 요소를 순회하며 chapter_section이 채워지는가."""
    j12 = next(c for c in chunks if c.chunk_id == "LAW-TST001-J12-H1")
    assert j12.chapter_section == "제3장 협약"


# ── 주제태그 비어있지 않음 ────────────────────────────────────
def test_topic_tags_nonempty(chunks):
    """모든 청크에 최소 1개의 주제태그가 있는가."""
    for c in chunks:
        assert len(c.topic_tags) >= 1, f"{c.chunk_id} topic_tags 비어 있음"


# ── 키워드 기반 태그 감지 ─────────────────────────────────────
def test_topic_협약변경_detected(chunks):
    j12 = next(c for c in chunks if c.chunk_id == "LAW-TST001-J12-H1")
    assert TopicTag.협약변경 in j12.topic_tags


def test_topic_성과관리_detected(chunks):
    j20 = next(c for c in chunks if c.chunk_id == "LAW-TST001-J20-H1")
    assert TopicTag.성과관리 in j20.topic_tags


# ── 예외 조문 추출: '제X조에도 불구하고' ─────────────────────
def test_exception_of_extraction(chunks):
    """제20조 제1항 본문의 '제12조에도 불구하고' → exception_of='제12조'."""
    j20 = next(c for c in chunks if c.chunk_id == "LAW-TST001-J20-H1")
    assert j20.exception_of == "제12조"


# ── 인용 추출: '별표1' ────────────────────────────────────────
def test_cites_appendix_extraction(chunks):
    """별표 참조가 cites에 들어오는가."""
    j20 = next(c for c in chunks if c.chunk_id == "LAW-TST001-J20-H1")
    assert "별표1" in j20.cites


# ── 삭제 조문 is_repealed=True ───────────────────────────────
def test_repealed_article(chunks):
    j99 = next(c for c in chunks if c.chunk_id == "LAW-TST001-J99")
    assert j99.is_repealed is True


# ── 조문별 시행일자 오버라이드 ────────────────────────────────
def test_article_enforce_date_overrides_law_date(chunks):
    """제20조의 조문시행일자(2024-07-01)가 법령 시행일자(2021-01-01)보다 우선하는가."""
    j20 = next(c for c in chunks if c.chunk_id == "LAW-TST001-J20-H1")
    assert j20.article_enforce_date == date(2024, 7, 1)
    assert j20.valid_from == date(2024, 7, 1)


# ── valid_from 시점 필터 동작 ────────────────────────────────
def test_temporal_filter_via_valid_from(chunks):
    """article_enforce_date 이전 시점엔 is_valid_at이 False를 반환하는가."""
    j20 = next(c for c in chunks if c.chunk_id == "LAW-TST001-J20-H1")
    assert j20.is_valid_at(date(2024, 8, 1)) is True
    assert j20.is_valid_at(date(2023, 1, 1)) is False


# ── 별표 청크 기본 필드 ──────────────────────────────────────
def test_appendix_chunk_fields(chunks):
    b1 = next(c for c in chunks if c.chunk_id == "LAW-TST001-B1")
    assert b1.article_no == "별표1"
    assert b1.article_title == "비목별 한도"
    assert b1.chapter_section == "별표"
    assert "[별표1]" in b1.body_text
    assert "|" in b1.body_text       # Markdown 표 보존


# ── 대통령령: 계층 / 구속력 ──────────────────────────────────
def test_hierarchy_대통령령(ryeong_chunks):
    for c in ryeong_chunks:
        assert c.hierarchy_level == 2
        assert c.binding_power == 4
        assert c.law_type == LawType.대통령령


# ── 대통령령: parent_law_ref 추출 ────────────────────────────
def test_parent_law_ref_extraction(ryeong_chunks):
    """'법 제12조제2항에 따라' → parent_law_ref = '테스트법 제12조제2항'."""
    j15 = next(c for c in ryeong_chunks if "J15" in c.chunk_id)
    assert j15.parent_law_ref == "테스트법 제12조제2항"


# ── 법률의 parent_law_ref는 항상 None ────────────────────────
def test_law_has_no_parent_ref(chunks):
    for c in chunks:
        assert c.parent_law_ref is None


# ── 시행일자 누락 → promulgation_date 폴백 ───────────────────
def test_enforce_date_fallback():
    """시행일자 없으면 공포일자로 폴백하고 Pydantic 검증을 통과하는가."""
    xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<법령>
  <기본정보>
    <법령명_한글>폴백테스트법</법령명_한글>
    <법령종류>고시</법령종류>
    <공포일자>20230101</공포일자>
  </기본정보>
  <조문>
    <조문단위>
      <조문번호>1</조문번호>
      <조문여부>본문</조문여부>
      <조문제목>목적</조문제목>
      <항>
        <항번호>①</항번호>
        <항내용>이 고시는 목적을 정한다.</항내용>
      </항>
    </조문단위>
  </조문>
</법령>
"""
    result = parse_law_xml(xml, {"law_id": "FALLBACK"})
    assert len(result) == 1
    c = result[0]
    LawChunk.model_validate(c.model_dump())
    # 시행일자 없음 → 공포일자(2023-01-01)로 폴백
    assert c.enforce_date == date(2023, 1, 1)
    assert c.article_enforce_date == date(2023, 1, 1)


# ── 잘못된 XML → ValueError ──────────────────────────────────
def test_invalid_xml_raises():
    with pytest.raises(ValueError, match="XML 파싱 실패"):
        parse_law_xml("<깨진xml<<", {"law_id": "X"})


# ── 고시: hierarchy=4, binding=3 ─────────────────────────────
def test_hierarchy_고시():
    xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<법령>
  <기본정보>
    <법령명_한글>사용기준</법령명_한글>
    <법령종류>고시</법령종류>
    <공포일자>20230101</공포일자>
    <시행일자>20230101</시행일자>
  </기본정보>
  <조문>
    <조문단위>
      <조문번호>1</조문번호>
      <조문여부>본문</조문여부>
      <조문제목>목적</조문제목>
      <항>
        <항번호>①</항번호>
        <항내용>이 고시는 연구비 집행 기준을 정한다.</항내용>
      </항>
    </조문단위>
  </조문>
</법령>
"""
    result = parse_law_xml(xml, {"law_id": "GOSI001", "law_type": "고시"})
    assert result[0].hierarchy_level == 4
    assert result[0].binding_power == 3


# ── 관계 품질 보강: exception_of 항 번호 패턴 ────────────────
def test_exception_of_항_pattern():
    """'제N항에도 불구하고'(조번호 없는 조내 참조) → exception_of 추출."""
    xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<법령>
  <기본정보>
    <법령명_한글>패턴테스트법</법령명_한글>
    <법령종류>법률</법령종류>
    <공포일자>20210101</공포일자>
    <시행일자>20210101</시행일자>
  </기본정보>
  <조문>
    <조문단위>
      <조문번호>11</조문번호>
      <조문여부>본문</조문여부>
      <조문제목>협약의 변경</조문제목>
      <조문시행일자>20210101</조문시행일자>
      <항>
        <항번호>①</항번호>
        <항내용>중앙행정기관의 장과 연구개발기관의 장은 협약 내용을 변경할 수 있다.</항내용>
      </항>
      <항>
        <항번호>②</항번호>
        <항내용>연구책임자 변경 시 미리 협의하여야 한다.</항내용>
      </항>
      <항>
        <항번호>③</항번호>
        <항내용>③ 제2항에도 불구하고 경미한 사항은 통보로 갈음할 수 있다.</항내용>
      </항>
    </조문단위>
  </조문>
</법령>
"""
    result = parse_law_xml(xml, {"law_id": "PAT001"})
    j11_h3 = next(c for c in result if c.paragraph_no == "3")
    # "제2항에도 불구하고" → exception_of = "제11조제2항"
    assert j11_h3.exception_of == "제11조제2항"
    # 이중 ③ 없음: body_text는 "③ 제2항에도..." (단일)
    assert j11_h3.body_text.count("③") == 1


# ── 관계 품질 보강: cites 자기참조 제외 ──────────────────────
def test_cites_no_self_reference(chunks):
    """조 헤더 '제N조(제목)'에서 자기 조번호가 cites에 들어오지 않는가."""
    for c in chunks:
        article_self = f"제{c.article_no}조"
        # para 없이 정확히 자기 조 참조 형태는 cites에 없어야
        exact_self = [r for r in c.cites if r == article_self]
        assert not exact_self, (
            f"{c.chunk_id}: 자기참조 '{article_self}'가 cites에 포함됨: {c.cites}"
        )


# ── 관계 품질 보강: exception_of 기존 패턴(조번호 포함)도 유지 ──
def test_exception_of_타조문_pattern(chunks):
    """'제N조에도 불구하고' (타 조문 참조) 기존 패턴이 계속 동작하는가."""
    j20 = next(c for c in chunks if c.chunk_id == "LAW-TST001-J20-H1")
    assert j20.exception_of == "제12조"  # 기존 assertion과 동일
