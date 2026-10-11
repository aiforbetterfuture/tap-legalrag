"""
law_rag.knowledge.splitter — 대형 별표 청크를 검색 가능한 크기로 분할한다.

왜 분할하나:
    간접비고시비율(2만자)·처분기준·사용용도 같은 별표는 표 전체가 한 청크다.
    임베딩은 청크 하나를 벡터 하나로 압축하므로, 수백 행짜리 표를 한 벡터로 만들면
    "모든 것에 조금씩 관련되고 무엇에도 정확히 맞지 않는" 흐릿한 벡터가 된다.
    → 행/대분류 단위로 쪼개 각 조각이 선명한 벡터를 갖게 한다.

인용 무결성이 최우선 (법령 RAG의 핵심):
    표를 쪼개면 "○○대학 | 17.5" 같은 맥락 없는 조각이 생긴다. 이 숫자가
    단독 인용되면 무엇의 비율인지, 어느 별표인지 알 수 없어 오답보다 위험하다.
    → 모든 자식은 (1) 머리말(별표제목+근거조문+표헤더)을 반복해 갖고,
              (2) parent_chunk_id로 부모(전체 별표)를 가리킨다.

구조:
    - 부모(원본 별표)는 코퍼스에 남기되 is_split_parent=True로 표시 → 기본 검색 제외.
    - 자식은 {부모ID}-S{n}, parent_chunk_id=부모ID.
    - 1차: 대분류(│N. 제목│ 또는 줄머리 'N. 제목') 경계로 분할.
    - 2차(재귀): 자식이 상한(기본 1,500자)을 넘으면 행(│...│) 경계에서 재분할.
"""
from __future__ import annotations

import hashlib
import re

from law_rag.models import LawChunk

# 분할 대상 임계(자식 목표 상한). 이 값 초과 시 재귀 분할.
DEFAULT_MAX_CHARS = 1500
# 이 길이 미만인 별표는 분할하지 않는다(작은 표는 통짜가 낫다).
SPLIT_TRIGGER_CHARS = 5000

# 대분류 헤더: 줄머리 또는 박스(│) 뒤의 'N. 한글제목(괄호조건 포함)'
#   예) "1. 직접비", "2. 간접비(제19조제1항 각 호의 연구개발기관)",
#       "1. 과학기술분야 정부출연연구기관 등"
# 괄호까지 포함해 대분류 제목을 온전히 잡는다(2·3번이 조건만 다른 별개 항목).
_RE_MAJOR = re.compile(
    r"(?:^|\n)[\s│]{0,6}(\d{1,2})\.\s*([^\n│]+?)\s*(?:│|\n)"
)


def split_appendix(chunk: LawChunk, max_chars: int = DEFAULT_MAX_CHARS) -> list[LawChunk]:
    """대형 별표 하나를 [부모(is_split_parent=True)] + [자식들]로 분할.

    분할이 불필요하면(짧으면) [chunk] 원본 그대로 반환한다.
    """
    body = chunk.body_text
    if len(body) < SPLIT_TRIGGER_CHARS:
        return [chunk]

    # 머리말: 별표 제목 라인(첫 줄 '[별표N] ...') + 표 헤더 행 후보
    heading = _extract_heading(chunk)

    # 1차: 대분류 경계로 자른 (제목, 텍스트) 조각
    sections = _split_by_major(body)
    if not sections:
        # 대분류를 못 찾으면 전체를 하나의 섹션으로 두고 2차(행) 분할에 맡긴다
        sections = [("", body)]

    # 본문 예산 = 상한 − 머리말/부분표시 오버헤드.
    # 자식 최종 크기 = heading + part + text 이므로 text 예산을 그만큼 줄인다.
    # part 표시("— 부분 12/18: 2. 간접비(...)")를 넉넉히 잡아 여유분 확보.
    overhead = len(heading) + 60
    body_budget = max(400, max_chars - overhead)

    # 각 섹션을 예산까지 재귀(행 경계) 분할
    pieces: list[tuple[str, str]] = []
    for sec_title, sec_text in sections:
        for sub in _split_by_rows(sec_text, body_budget):
            pieces.append((sec_title, sub))

    # 부모: 원본 유지 + is_split_parent=True
    parent = chunk.model_copy(update={"is_split_parent": True})
    result: list[LawChunk] = [parent]

    total = len(pieces)
    for i, (sec_title, text) in enumerate(pieces, start=1):
        child_body = _compose_child_body(heading, sec_title, i, total, text)
        child = chunk.model_copy(update={
            "chunk_id": f"{chunk.chunk_id}-S{i}",
            "body_text": child_body,
            "parent_chunk_id": chunk.chunk_id,
            "is_split_parent": False,
            # 자식은 body_text가 달라지므로 지문도 다시 계산(P1B-4)
            "version_hash": hashlib.sha256(child_body.encode("utf-8")).hexdigest(),
        })
        result.append(child)
    return result


def split_corpus(chunks: list[LawChunk], max_chars: int = DEFAULT_MAX_CHARS) -> list[LawChunk]:
    """코퍼스 전체에 split_appendix를 적용해 평탄화한 새 리스트를 반환."""
    out: list[LawChunk] = []
    for c in chunks:
        # 서식(is_form)은 분할 대상 아님 — 검색에서 이미 제외됨
        if c.is_form or "-B" not in c.chunk_id:
            out.append(c)
            continue
        out.extend(split_appendix(c, max_chars))
    return out


# ─────────────────────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────────────────────
def _extract_heading(chunk: LawChunk) -> str:
    """자식마다 반복할 머리말. 별표 제목 + 표 헤더 행(있으면)."""
    lines = chunk.body_text.split("\n")
    # 첫 줄이 '[별표N] ...' 머리말
    title_line = lines[0].strip() if lines else chunk.article_title
    # 표 헤더 후보: '구분│기관명│...' 처럼 │가 2개 이상이고 숫자 데이터가 아닌 첫 행
    header_row = ""
    for L in lines[1:40]:
        if L.count("│") >= 2 and not re.search(r"\d+\.\d+", L):
            # 열 이름처럼 보이는 한글 헤더
            if re.search(r"[가-힣]{2,}", L):
                header_row = L.strip()
                break
    return f"{title_line}\n{header_row}".strip() if header_row else title_line


def _split_by_major(body: str) -> list[tuple[str, str]]:
    """대분류(│N. 제목│) 경계로 (제목, 텍스트) 리스트 생성."""
    matches = list(_RE_MAJOR.finditer(body))
    if len(matches) < 2:
        return []  # 대분류가 1개 이하면 1차 분할 무의미
    sections: list[tuple[str, str]] = []
    for idx, m in enumerate(matches):
        start = m.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        num, title = m.group(1), m.group(2).strip()
        sec_title = f"{num}. {title}"
        sections.append((sec_title, body[start:end]))
    return sections


def _split_by_rows(text: str, max_chars: int) -> list[str]:
    """텍스트를 행(│...│) 경계 존중하며 max_chars 이하 조각으로 분할.

    글자수는 목표일 뿐, 실제 절단은 항상 줄바꿈 경계에서만 일어난다.
    한 줄이 상한보다 길어도 그 줄은 쪼개지 않는다(숫자·행 중간 절단 방지)."""
    if len(text) <= max_chars:
        return [text]
    lines = text.split("\n")
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for L in lines:
        add_len = len(L) + 1
        if buf and buf_len + add_len > max_chars:
            chunks.append("\n".join(buf))
            buf, buf_len = [], 0
        buf.append(L)
        buf_len += add_len
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def _compose_child_body(heading: str, sec_title: str, i: int, total: int, text: str) -> str:
    """머리말 + 부분표시 + 본문. 맥락 없는 조각의 단독 인용을 막는 장치."""
    part = f"— 부분 {i}/{total}"
    if sec_title:
        part += f": {sec_title}"
    # 본문 첫 줄이 이미 대분류 헤더면 머리말과 중복되지만, 인용 무결성 위해 머리말은 항상 유지
    return f"{heading}\n{part}\n{text.strip()}"
