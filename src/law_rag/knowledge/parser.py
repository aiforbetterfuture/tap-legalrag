"""
law_rag.knowledge.parser — 국가법령정보 Open API XML → LawChunk 변환기.

SPEC-1:
    parse_law_xml(xml: str, meta: dict) -> list[LawChunk]
    - <조문단위>를 순회하며 항 단위 청크 생성 (항 없으면 조 단위 1청크)
    - <별표단위>를 Markdown 표 포맷으로 보존해 B-ID 청크 생성
    - hierarchy_level / binding_power 자동 매핑 (law_type 기반)
    - parent_law_ref / exception_of 본문 정규식 추출
    - topic_tags 키워드 자동 감지 (미분류 → 정의일반)
    - context_prefix: None  (V1; LLM 연결 후 채울 것)
    - 시행일자 누락 → enforce_date 폴백
    - 표 파싱 실패 → 원문 텍스트 보존 + 경고 로그

실제 법제처 XML 구조 노트 (테스트 XML과의 차이):
    - 법령종류: <법종구분>법률</법종구분>  (구형: <법령종류>)
    - 장/절 경계: <조문단위 조문여부="전문"> + <조문내용>제N장 ...</조문내용>
      (테스트 XML의 <장>/<절> 태그는 계속 지원)
    - 항번호 없는 <항>: 호목 컨테이너일 뿐 — 단락 분리 안 함, 조문내용 사용
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date
from xml.etree import ElementTree as ET

from law_rag.knowledge.refs import scan_refs
from law_rag.models import LawChunk, LawType, TopicTag

log = logging.getLogger(__name__)

# ── 법령종류 → hierarchy / binding 매핑 ──────────────────────
_HIERARCHY: dict[str, int] = {
    "법률": 1, "대통령령": 2,
    "총리령": 3, "부령": 3,
    "고시": 4, "훈령": 4, "예규": 4,
    "기관내규": 5,
}
_BINDING: dict[int, int] = {1: 5, 2: 4, 3: 3, 4: 3, 5: 2}

# ── 주제태그 키워드 ────────────────────────────────────────────
_TOPIC_KW: list[tuple[list[str], TopicTag]] = [
    (["협약변경", "협약 변경", "변경협약", "협약의 변경"], TopicTag.협약변경),
    (["연구비", "연구개발비", "집행", "비목", "전용", "사용기준", "계상"], TopicTag.연구비집행),
    (["정산", "정산서", "잔액"], TopicTag.정산),
    (["장비", "기기", "심의위원회", "장비심의"], TopicTag.장비심의),
    (["성과", "보고서", "평가", "성과보고"], TopicTag.성과관리),
    (["제재", "환수", "참여제한"], TopicTag.제재환수),
    (["기술료", "기술이전", "기술실시"], TopicTag.기술료),
    (["보안", "비밀", "보호"], TopicTag.보안),
]

# ── 원형숫자 → 아라비아숫자 ──────────────────────────────────
_CIRCLE: dict[str, str] = {
    "①": "1", "②": "2", "③": "3", "④": "4", "⑤": "5",
    "⑥": "6", "⑦": "7", "⑧": "8", "⑨": "9", "⑩": "10",
    "⑪": "11", "⑫": "12", "⑬": "13", "⑭": "14", "⑮": "15",
}

# ── 정규식 패턴 ───────────────────────────────────────────────
# 예외 패턴 A: "제N조[제M항]에도 불구하고"  (타 조문 참조)
_RE_EXCEPTION = re.compile(
    r"제(\d+(?:의\d+)?)조(?:제(\d+)항)?(?:에도\s*불구하고|의\s*규정에도\s*불구하고)"
)
# 예외 패턴 B: "제N항에도 불구하고"  (같은 조 내 항 참조 — 법령에서 가장 흔한 형태)
_RE_EXCEPTION_PARA = re.compile(
    r"제(\d+)항(?:에도\s*불구하고|의\s*규정에도\s*불구하고)"
)
# 상위법 인용: "법 제X조제Y항", "이 법 제X조", "시행령 제X조"
_RE_PARENT = re.compile(
    r"(?:같은\s*)?(?:법|이\s*법|시행령|시행규칙|규칙)\s*제(\d+(?:의\d+)?)조(?:제(\d+)항)?"
)
_RE_CITE = re.compile(r"제(\d+(?:의\d+)?)조(?:제(\d+)항)?")
_RE_APPENDIX_CITE = re.compile(r"별표\s*(\d+)")
_RE_DATE8 = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_RE_DATE_DASH = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
# 전문 조문내용에서 장/절 판별
_RE_장 = re.compile(r"^제\d+장")
_RE_절 = re.compile(r"^제\d+절")


# ─────────────────────────────────────────────────────────────
def parse_law_xml(xml: str, meta: dict) -> list[LawChunk]:
    """
    국가법령정보 Open API XML → LawChunk 리스트.

    Args:
        xml:  법령 XML 문자열.
        meta: 보조 메타데이터.
              필수: law_id (str)
              선택: law_name, law_short_name, law_type (str),
                    promulgation_date (str|date), enforce_date (str|date),
                    source_url_base (str), parent_law_name (str)

    Returns:
        list[LawChunk] — 모든 항목이 Pydantic model_validate를 통과한 상태.

    Raises:
        ValueError: XML이 파싱 불가능한 경우.
    """
    try:
        # 인코딩 선언이 있는 str은 bytes로 변환해야 ET가 처리함
        data = xml.encode("utf-8") if isinstance(xml, str) else xml
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"XML 파싱 실패: {exc}") from exc

    # <LawService><법령>...</법령></LawService> 래퍼 처리
    law_root = root.find(".//법령") if root.tag != "법령" else root
    if law_root is None:
        law_root = root

    # ── 기본정보 ─────────────────────────────────────────────
    law_id = str(meta.get("law_id") or _t(law_root, ".//법령ID") or "UNKNOWN")
    law_name = str(meta.get("law_name") or _t(law_root, ".//법령명_한글") or "")
    law_short_name: str | None = (
        meta.get("law_short_name") or _t(law_root, ".//법령명약칭") or None
    )
    raw_type = (
        meta.get("law_type")
        or _t(law_root, ".//법종구분")          # 법제처 실XML 태그
        or _t(law_root, ".//법령종류_코드")
        or _t(law_root, ".//법령종류")
        or "고시"
    )
    try:
        law_type = LawType(str(raw_type))
    except ValueError:
        log.warning("알 수 없는 법령종류 '%s' → 고시로 처리", raw_type)
        law_type = LawType.고시

    hierarchy_level = _HIERARCHY.get(law_type.value, 4)
    binding_power = _BINDING[hierarchy_level]

    prom_date = (
        _parse_date(meta.get("promulgation_date") or _t(law_root, ".//공포일자"))
        or date.today()
    )
    enf_date = (
        _parse_date(meta.get("enforce_date") or _t(law_root, ".//시행일자"))
        or prom_date
    )
    source_base: str = meta.get("source_url_base") or "https://www.law.go.kr/법령"
    parent_law_name: str | None = meta.get("parent_law_name") or None

    # ── 조문 수집 ─────────────────────────────────────────────
    chunks: list[LawChunk] = []
    조문_elem = law_root.find("조문")

    if 조문_elem is not None:
        current_장: str | None = None   # 현재 장 레이블
        current_절: str | None = None   # 현재 절 레이블 (장 바뀌면 초기화)

        for child in 조문_elem:
            # ── 방식 A: 테스트 XML처럼 <장>/<절> 태그가 직접 있는 경우 ──
            if child.tag == "장":
                current_장 = _chapter_label(child)
                current_절 = None  # 새 장 진입 시 절 초기화
                chapter = current_장
                for art in child:
                    if art.tag == "조문단위":
                        chunks.extend(
                            _parse_article(
                                art, law_id, law_name, law_short_name,
                                law_type, hierarchy_level, binding_power,
                                prom_date, enf_date, chapter,
                                source_base, parent_law_name,
                            )
                        )
            elif child.tag == "절":
                current_절 = _chapter_label(child)
                chapter = " ".join(p for p in [current_장, current_절] if p) or None
                for art in child:
                    if art.tag == "조문단위":
                        chunks.extend(
                            _parse_article(
                                art, law_id, law_name, law_short_name,
                                law_type, hierarchy_level, binding_power,
                                prom_date, enf_date, chapter,
                                source_base, parent_law_name,
                            )
                        )

            # ── 방식 B: 법제처 실XML처럼 <조문단위 조문여부="전문">인 경우 ──
            elif child.tag == "조문단위":
                여부 = (child.findtext("조문여부") or "").strip()
                if 여부 == "전문":
                    label = (child.findtext("조문내용") or "").strip()
                    if _RE_장.match(label):
                        current_장 = label
                        current_절 = None  # 새 장 진입 시 절 초기화
                    elif _RE_절.match(label):
                        current_절 = label
                    # 전문 항목은 청크로 만들지 않음
                else:
                    chapter = (
                        " ".join(p for p in [current_장, current_절] if p) or None
                    )
                    chunks.extend(
                        _parse_article(
                            child, law_id, law_name, law_short_name,
                            law_type, hierarchy_level, binding_power,
                            prom_date, enf_date, chapter,
                            source_base, parent_law_name,
                        )
                    )

    # ── 별표 수집 ─────────────────────────────────────────────
    별표_elem = law_root.find("별표")
    if 별표_elem is not None:
        for child in 별표_elem:
            if child.tag == "별표단위":
                c = _parse_appendix(
                    child, law_id, law_name, law_short_name,
                    law_type, hierarchy_level, binding_power,
                    prom_date, enf_date, source_base,
                )
                if c:
                    chunks.append(c)

    return chunks


# ── 조문 파싱 ─────────────────────────────────────────────────
def _parse_article(
    elem: ET.Element,
    law_id: str,
    law_name: str,
    law_short_name: str | None,
    law_type: LawType,
    hierarchy_level: int,
    binding_power: int,
    prom_date: date,
    enf_date: date,
    chapter: str | None,
    source_base: str,
    parent_law_name: str | None,
) -> list[LawChunk]:
    """<조문단위> → 항 단위 LawChunk 리스트 (항 없으면 1개)."""
    article_no = (_t(elem, "조문번호") or "").strip()
    article_title = (_t(elem, "조문제목") or "").strip()
    조문여부 = (_t(elem, "조문여부") or "본문").strip()

    # 가지조문(제21조의2 등) 처리:
    # 조문번호 필드는 '21'이지만 조문내용 첫머리가 '제21조의2(...)'인 경우
    # → article_no를 '21의2'로 교체해 chunk_id 충돌을 막는다.
    # 한국법 표기: 제N조의M  (의M이 '조' 뒤에 붙음)
    _header = (_t(elem, "조문내용") or "").strip()
    _m = re.match(r"제(\d+)조(의\d+)?", _header)
    if _m:
        _art = _m.group(1) + (_m.group(2) or "")
        if _art != article_no:
            article_no = _art
    is_repealed = 조문여부 in ("삭제", "폐지")

    # 조문별 시행일자 (없으면 법령 시행일자로 폴백)
    article_enforce_date = (
        _parse_date(_t(elem, "조문시행일자")) or enf_date
    )
    source_url = f"{source_base}/{law_name}/제{article_no}조"
    parent_chunk_id = f"LAW-{law_id}-J{article_no}"

    # 항번호(①②...)가 있는 항만 실제 단락으로 인식한다.
    # 항번호 없는 <항>은 호·목 목록의 컨테이너일 뿐이므로 단락 분리하지 않는다.
    all_항 = elem.findall("항")
    named_paras = [p for p in all_항 if p.findtext("항번호")]

    if not named_paras:
        # 항번호 없음(또는 항 자체 없음) → 조문내용으로 단일 청크
        body = (_t(elem, "조문내용") or "").strip()
        if not body:
            body = f"제{article_no}조({article_title})"
        return [
            _make_chunk(
                chunk_id=f"LAW-{law_id}-J{article_no}",
                law_id=law_id, law_name=law_name, law_short_name=law_short_name,
                law_type=law_type, hierarchy_level=hierarchy_level,
                binding_power=binding_power, article_no=article_no,
                article_title=article_title, paragraph_no=None,
                chapter_section=chapter,
                promulgation_date=prom_date, enforce_date=enf_date,
                article_enforce_date=article_enforce_date,
                is_repealed=is_repealed,
                body_text=body, source_url=source_url,
                parent_chunk_id=None, parent_law_name=parent_law_name,
            )
        ]

    # 항번호(①②...) 있는 항들 → 항 단위로 분리
    result: list[LawChunk] = []
    for idx, 항 in enumerate(named_paras):
        항번호_raw = (_t(항, "항번호") or "").strip()
        # 법제처 실XML은 항내용 앞에 이미 ①②를 포함한다 → 이중 삽입 방지
        항내용 = _strip_leading_circle((_t(항, "항내용") or "").strip())
        para_no = _circle_to_num(항번호_raw)

        # 첫 항만 조 제목 포함 (검색 시 문맥 제공)
        if idx == 0:
            body_text = f"제{article_no}조({article_title}) {항번호_raw} {항내용}".strip()
        else:
            body_text = f"{항번호_raw} {항내용}".strip()

        result.append(
            _make_chunk(
                chunk_id=f"LAW-{law_id}-J{article_no}-H{para_no}",
                law_id=law_id, law_name=law_name, law_short_name=law_short_name,
                law_type=law_type, hierarchy_level=hierarchy_level,
                binding_power=binding_power, article_no=article_no,
                article_title=article_title, paragraph_no=para_no,
                chapter_section=chapter,
                promulgation_date=prom_date, enforce_date=enf_date,
                article_enforce_date=article_enforce_date,
                is_repealed=is_repealed,
                body_text=body_text, source_url=source_url,
                parent_chunk_id=parent_chunk_id, parent_law_name=parent_law_name,
            )
        )
    return result


def _norm_appendix_no(raw: str) -> str:
    """별표번호 '0007' → '7', '0001' → '1'. 이미 정수면 그대로."""
    raw = (raw or "").strip()
    return str(int(raw)) if raw.isdigit() else raw


def _appendix_ids(별표구분: str, 번호: str, 가지: str, law_id: str):
    """별표구분·번호·가지번호로 (chunk_id, article_no, is_form) 계산.

    - 별표구분 in (별지, 서식) → is_form=True, 접두 '-F'
      (서식은 같은 번호가 중복되므로 가지번호를 붙여 유일화: -F1, -F1의2)
    - 그 외(별표) → is_form=False, 접두 '-B'
    """
    is_form = 별표구분 in ("별지", "서식")
    n = _norm_appendix_no(번호)
    g = (가지 or "").strip()
    # 가지번호 '00'/'0'/'' 는 가지 없음으로 간주
    has_gaji = g not in ("", "00", "0")
    suffix = f"{n}의{int(g)}" if has_gaji else n

    if is_form:
        label = "별지" if 별표구분 == "별지" else "서식"
        return f"LAW-{law_id}-F{suffix}", f"{label}{suffix}", True
    return f"LAW-{law_id}-B{suffix}", f"별표{suffix}", False


# ── 별표 파싱 ─────────────────────────────────────────────────
def _parse_appendix(
    elem: ET.Element,
    law_id: str,
    law_name: str,
    law_short_name: str | None,
    law_type: LawType,
    hierarchy_level: int,
    binding_power: int,
    prom_date: date,
    enf_date: date,
    source_base: str,
) -> LawChunk | None:
    """<별표단위> → B-ID(실질 별표) 또는 F-ID(별지/서식) LawChunk.

    별표구분 태그(별표/별지/서식)로 is_form을 결정한다.
    별지·서식은 같은 번호가 중복되므로 별표가지번호를 chunk_id에 포함해 유일화한다.
    """
    별표번호 = (_t(elem, "별표번호") or "").strip()
    별표구분 = (_t(elem, "별표구분") or "별표").strip()
    별표가지번호 = (_t(elem, "별표가지번호") or "").strip()
    별표제목 = (_t(elem, "별표제목") or "").strip()
    raw_content = (_t(elem, "별표내용") or "").strip()
    별표_enf = _parse_date(_t(elem, "별표시행일자")) or enf_date

    if not 별표번호:
        log.warning("별표번호 없음 — 건너뜀")
        return None

    chunk_id, article_no, is_form = _appendix_ids(
        별표구분, 별표번호, 별표가지번호, law_id
    )

    try:
        body_text = _table_to_markdown(raw_content, article_no.lstrip("별표별지서식"), 별표제목)
    except Exception as exc:
        log.warning("%s 표 파싱 실패 → 원문 보존: %s", article_no, exc)
        body_text = f"[{article_no}] {별표제목}\n{raw_content}"

    return _make_chunk(
        chunk_id=chunk_id,
        law_id=law_id, law_name=law_name, law_short_name=law_short_name,
        law_type=law_type, hierarchy_level=hierarchy_level,
        binding_power=binding_power, article_no=article_no,
        article_title=별표제목, paragraph_no=None,
        chapter_section="별표" if not is_form else "서식",
        promulgation_date=prom_date, enforce_date=enf_date,
        article_enforce_date=별표_enf,
        is_repealed=False,
        body_text=body_text, source_url=f"{source_base}/{law_name}/{article_no}",
        parent_chunk_id=None, parent_law_name=None,
        is_form=is_form,
    )


# ── 청크 생성 공통 팩토리 ─────────────────────────────────────
def _make_chunk(
    *,
    chunk_id: str,
    law_id: str,
    law_name: str,
    law_short_name: str | None,
    law_type: LawType,
    hierarchy_level: int,
    binding_power: int,
    article_no: str,
    article_title: str,
    paragraph_no: str | None,
    chapter_section: str | None,
    promulgation_date: date,
    enforce_date: date,
    article_enforce_date: date,
    is_repealed: bool,
    body_text: str,
    source_url: str,
    parent_chunk_id: str | None,
    parent_law_name: str | None,
    is_form: bool = False,
    is_split_parent: bool = False,
) -> LawChunk:
    return LawChunk(
        chunk_id=chunk_id,
        law_id=law_id,
        law_name=law_name,
        law_short_name=law_short_name,
        law_type=law_type,
        hierarchy_level=hierarchy_level,
        binding_power=binding_power,
        article_no=article_no,
        article_title=article_title,
        paragraph_no=paragraph_no,
        chapter_section=chapter_section,
        promulgation_date=promulgation_date,
        enforce_date=enforce_date,
        article_enforce_date=article_enforce_date,
        is_repealed=is_repealed,
        valid_from=article_enforce_date,
        valid_to=None,
        topic_tags=_detect_topics(body_text, article_title),
        parent_law_ref=_extract_parent_ref(body_text, parent_law_name)
        if hierarchy_level > 1 else None,
        cites=_extract_cites(body_text, article_no),
        exception_of=_extract_exception(body_text, article_no),
        context_prefix=_build_context_prefix(
            law_short_name or law_name, chapter_section,
            article_no, article_title, paragraph_no,
        ),
        body_text=body_text,
        parent_chunk_id=parent_chunk_id,
        source_url=source_url,
        # P1B-4: 조문 원문(body_text) 지문. 개정 전후 '변경된 조문'을 정확히 집어낸다.
        version_hash=hashlib.sha256(body_text.encode("utf-8")).hexdigest(),
        is_form=is_form,
        is_split_parent=is_split_parent,
    )


# ── 문맥 문장(context_prefix) 규칙 생성 (P1-①) ────────────────
def _build_context_prefix(
    law_label: str,
    chapter_section: str | None,
    article_no: str,
    article_title: str,
    paragraph_no: str | None,
) -> str:
    """검색용 문맥 문장. LLM 없이 규칙으로 '법령 > 장 > 조(제목) 제N항' 경로를 만든다.

    왜 필요한가:
        항 2 이후 청크는 본문에 조 제목이 없어 "무슨 조문인지" 흐릿하다.
        검색 임베딩 앞에 이 경로를 붙이면 조 제목·장 맥락이 벡터에 실린다.
        원문(body_text)은 절대 건드리지 않는다(결정적 단서 보존).
        예) "[혁신법 시행령 > 제3장 > 제14조(중요한 사항의 범위) 제1항]"
    """
    # article_no는 별표면 이미 '별표6' 형태 → '별표' 접두 중복 금지(표기버그 수정)
    art = article_no if article_no.startswith("별표") else f"제{article_no}조"
    head = f"{art}({article_title})" if article_title else art
    parts = [law_label]
    # 별표의 chapter_section('별표')은 head('별표6...')와 겹치므로 생략
    if chapter_section and not (article_no.startswith("별표") and chapter_section == "별표"):
        parts.append(chapter_section)
    parts.append(head + (f" 제{paragraph_no}항" if paragraph_no else ""))
    return "[" + " > ".join(parts) + "]"


# ── 헬퍼 ─────────────────────────────────────────────────────
def _t(elem: ET.Element, path: str) -> str | None:
    """XPath 텍스트 추출. None-safe."""
    node = elem.find(path)
    if node is None or not node.text:
        return None
    return node.text.strip() or None


def _parse_date(raw: object) -> date | None:
    """'20210101' 또는 '2021-01-01' 형식 → date. 실패 시 None."""
    if isinstance(raw, date):
        return raw
    s = str(raw).strip() if raw else ""
    m = _RE_DATE8.match(s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = _RE_DATE_DASH.match(s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def _circle_to_num(s: str) -> str:
    """'①' → '1'. 일반 숫자 문자열은 그대로 반환."""
    if s in _CIRCLE:
        return _CIRCLE[s]
    if re.fullmatch(r"\d+", s):
        return s
    for k, v in _CIRCLE.items():
        if s.startswith(k):
            return v
    return s or "1"


def _chapter_label(elem: ET.Element) -> str:
    """<장>/<절> 요소 → '제3장 연구개발과제의 협약' 레이블."""
    tag = elem.tag
    번호 = _t(elem, f"{tag}번호") or ""
    제목 = _t(elem, f"{tag}제목") or ""
    label = f"{번호} {제목}".strip()
    return label or 번호 or 제목


def _detect_topics(body: str, title: str) -> list[TopicTag]:
    """body + title에서 주제태그 감지. 미분류 → [정의일반]."""
    combined = f"{title} {body}"
    found: list[TopicTag] = []
    for keywords, tag in _TOPIC_KW:
        if any(kw in combined for kw in keywords):
            if tag not in found:
                found.append(tag)
    return found if found else [TopicTag.정의일반]


def _strip_leading_circle(s: str) -> str:
    """항내용 앞의 원형숫자 '① ' 제거.
    법제처 실XML은 항내용 안에 번호가 이미 들어있어 파서가 다시 붙이면 이중 삽입된다."""
    for k in _CIRCLE:
        if s.startswith(k):
            return s[len(k):].lstrip()
    return s


def _extract_exception(body: str, article_no: str = "") -> str | None:
    """예외 기준 조문 추출. 두 패턴 지원:
      A) '제N조[제M항]에도 불구하고' — 타 조문 참조
      B) '제N항에도 불구하고'       — 같은 조 내 항 참조 (article_no 필요)
    """
    # A) 타 조문 참조
    m = _RE_EXCEPTION.search(body)
    if m:
        art, para = m.group(1), m.group(2)
        return f"제{art}조제{para}항" if para else f"제{art}조"
    # B) 같은 조 내 항 참조
    m2 = _RE_EXCEPTION_PARA.search(body)
    if m2 and article_no:
        return f"제{article_no}조제{m2.group(1)}항"
    return None


def _extract_cites(body: str, article_no: str = "") -> list[str]:
    """본문에서 조문·별표 인용 참조 추출 (한정어 보존, 중복 제거, 자기참조 제외).

    P0-1: 한정어(법/영/「법령명」)를 살려 정규화 문자열로 반환한다.
        예) "법 제11조제2항"  → '법 제11조제2항'  (상위법)
            "영 제5조"        → '영 제5조'        (시행령)
            "제13조제4항"     → '제13조제4항'     (한정어 없음 → 자기 법령)
        해석(누가 '법'인가)은 loader._resolve_ref가 관계맵으로 처리한다.

    자기참조 제외: 조 머리말 '제N조(제목)...'에서 자기 조번호(한정어·항 없음)만 걸러낸다.
        '제N조제M항'(조내 교차 항 참조)은 유지한다.
    """
    cites: list[str] = []
    for r in scan_refs(body):
        if r.name is None and r.qual is None and r.para is None and r.art == article_no:
            continue  # 자기 조 단독 참조(머리말 잡음) 제외
        c = r.canonical()
        if c not in cites:
            cites.append(c)
    for m in _RE_APPENDIX_CITE.finditer(body):
        ref = f"별표{m.group(1)}"
        if ref not in cites:
            cites.append(ref)
    return cites


def _extract_parent_ref(body: str, parent_law_name: str | None) -> str | None:
    """본문에서 상위법 위임 조문 참조 추출.
    parent_law_name이 있으면 '법률명 제X조제Y항' 형식으로 확장."""
    m = _RE_PARENT.search(body)
    if not m:
        return None
    art, para = m.group(1), m.group(2)
    suffix = f"제{art}조제{para}항" if para else f"제{art}조"
    return f"{parent_law_name} {suffix}" if parent_law_name else suffix


def _table_to_markdown(content: str, 번호: str, 제목: str) -> str:
    """별표 내용을 '[별표N] 제목\\n내용' 형식으로 정규화.
    이미 Markdown 표(|) 포함이면 그대로 헤더만 앞에 붙인다."""
    header = f"[별표{번호}] {제목}"
    return f"{header}\n{content}" if content else header


# ═══════════════════════════════════════════════════════════════
# 고시(행정규칙) XML 파서 — target=admrul 응답 전용
# 루트 태그: <AdmRulService>  (법령 XML의 <법령>과 다름)
# 조문 구조: <조문내용> CDATA 평문 텍스트 (항 분리 없이 조 단위 1청크)
# 장/절 경계: 조문내용 텍스트 앞부분 "제N장 …" / "제N절 …" 패턴으로 감지
# ═══════════════════════════════════════════════════════════════

_RE_ADMRUL_ART   = re.compile(r"^제(\d+(?:의\d+)?)조\(([^)]+)\)")
_RE_ADMRUL_CHAP  = re.compile(r"^(제\d+장\s+\S.*?)$", re.MULTILINE)
_RE_ADMRUL_SEC   = re.compile(r"^(제\d+절\s+\S.*?)$", re.MULTILINE)
_RE_ADMRUL_BT_NO = re.compile(r"\[별표\s*(\d+(?:의\d+)?)\]")


def parse_admrul_xml(xml: str, meta: dict) -> list[LawChunk]:
    """
    법제처 행정규칙 API(target=admrul) XML → LawChunk 리스트.

    구조 차이 요약:
        - 루트: <AdmRulService> (법령 XML의 <법령>과 다름)
        - 기본정보: <행정규칙기본정보>
        - 조문: <조문내용> CDATA 평문 텍스트 (141개 등)
          · 제목/본문이 하나의 텍스트 덩어리 — 조 단위로 1청크 생성
        - 별표: <별표단위> (별표번호 "0001" 형식)

    Args:
        xml:  admrul API XML 문자열.
        meta: 필수 = law_id. 선택 = law_name, law_short_name, law_type,
              source_url_base, parent_law_name, enforce_date.
    """
    try:
        data = xml.encode("utf-8") if isinstance(xml, str) else xml
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"고시 XML 파싱 실패: {exc}") from exc

    # ── 기본정보 ──────────────────────────────────────────────
    info = root.find("행정규칙기본정보")
    law_id = str(meta.get("law_id") or
                  (info.findtext("행정규칙ID") if info is not None else None) or "UNKNOWN")
    law_name = str(meta.get("law_name") or
                   (info.findtext("행정규칙명") if info is not None else None) or "")
    law_short_name: str | None = meta.get("law_short_name") or None
    raw_type = meta.get("law_type") or \
               (info.findtext("행정규칙종류") if info is not None else None) or "고시"
    try:
        law_type = LawType(str(raw_type))
    except ValueError:
        log.warning("알 수 없는 행정규칙 종류 '%s' → 고시로 처리", raw_type)
        law_type = LawType.고시

    hierarchy_level = _HIERARCHY.get(law_type.value, 4)
    binding_power   = _BINDING[hierarchy_level]

    raw_enf = meta.get("enforce_date") or \
              (info.findtext("시행일자") if info is not None else None) or ""
    raw_prom = meta.get("promulgation_date") or \
               (info.findtext("발령일자") if info is not None else None) or raw_enf
    prom_date = _parse_date(raw_prom) or date.today()
    enf_date  = _parse_date(raw_enf)  or prom_date

    source_base = meta.get("source_url_base") or "https://www.law.go.kr/행정규칙"
    parent_law_name: str | None = meta.get("parent_law_name") or None

    # ── 조문내용 순회 ─────────────────────────────────────────
    chunks: list[LawChunk] = []
    current_장: str | None = None
    current_절: str | None = None

    for elem in root.findall("조문내용"):
        txt = (elem.text or "").strip()
        if not txt:
            continue

        # 장/절 헤더 감지 — 청크 미생성, 컨텍스트만 업데이트
        m_chap = _RE_장.match(txt)
        m_sec  = _RE_절.match(txt)
        if m_chap:
            current_장 = txt.split("\n")[0].strip()
            current_절 = None
            continue
        if m_sec:
            current_절 = txt.split("\n")[0].strip()
            continue

        # 조문 감지
        m_art = _RE_ADMRUL_ART.match(txt)
        if not m_art:
            continue  # 편·절 제목이거나 미식별 → 스킵

        article_no    = m_art.group(1)
        article_title = m_art.group(2)
        chapter = " ".join(p for p in [current_장, current_절] if p) or None
        source_url = f"{source_base}/{law_name}/제{article_no}조"

        chunks.append(_make_chunk(
            chunk_id=f"LAW-{law_id}-J{article_no}",
            law_id=law_id, law_name=law_name, law_short_name=law_short_name,
            law_type=law_type, hierarchy_level=hierarchy_level,
            binding_power=binding_power, article_no=article_no,
            article_title=article_title, paragraph_no=None,
            chapter_section=chapter,
            promulgation_date=prom_date, enforce_date=enf_date,
            article_enforce_date=enf_date,
            is_repealed=False,
            body_text=txt, source_url=source_url,
            parent_chunk_id=None, parent_law_name=parent_law_name,
        ))

    # ── 별표 수집 ─────────────────────────────────────────────
    # 고시는 <별표단위>에 별표(실질)와 별지(서식)가 섞여 있고 번호가 중복된다.
    # 별표구분·별표가지번호로 유일화하고 is_form을 결정한다.
    별표_root = root.find("별표")
    if 별표_root is not None:
        for bt in 별표_root.findall("별표단위"):
            raw_no  = (bt.findtext("별표번호") or "").strip()
            구분    = (bt.findtext("별표구분") or "별표").strip()
            가지    = (bt.findtext("별표가지번호") or "").strip()
            제목    = (bt.findtext("별표제목") or "").strip()
            raw_cnt = (bt.findtext("별표내용") or "").strip()

            if not raw_no or "삭제" in 제목:
                continue

            chunk_id, article_no, is_form = _appendix_ids(구분, raw_no, 가지, law_id)

            try:
                body_text = _table_to_markdown(raw_cnt, article_no.lstrip("별표별지서식"), 제목)
            except Exception as exc:
                log.warning("고시 %s 파싱 실패 → 원문 보존: %s", article_no, exc)
                body_text = f"[{article_no}] {제목}\n{raw_cnt}"

            chunks.append(_make_chunk(
                chunk_id=chunk_id,
                law_id=law_id, law_name=law_name, law_short_name=law_short_name,
                law_type=law_type, hierarchy_level=hierarchy_level,
                binding_power=binding_power, article_no=article_no,
                article_title=제목, paragraph_no=None,
                chapter_section="별표" if not is_form else "서식",
                promulgation_date=prom_date, enforce_date=enf_date,
                article_enforce_date=enf_date,
                is_repealed=False,
                body_text=body_text,
                source_url=f"{source_base}/{law_name}/{article_no}",
                parent_chunk_id=None, parent_law_name=None,
                is_form=is_form,
            ))

    return chunks
