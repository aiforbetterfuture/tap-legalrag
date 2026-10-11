"""
scripts/parse_manuals.py — 혁신법 매뉴얼 PDF → GuideChunk JSON (M-2).

'해설' 코퍼스(법적 효력 없는 보조 근거)를 만든다. 법령 코퍼스와 섞지 않는다.
읽기 전용으로 ../rnd_raw_manual의 본권·별권1~4를 파싱(부록 서식·의료법·보건의료기본법 제외).
Q&A("Q1." 형식)를 청크로 추출하고 장/절 경로·쪽수·인용 조문(cited_refs)을 채운다.

사용: python scripts/parse_manuals.py
출력: data/guide/{코드}.json  (+ 원본 PDF 지문은 로그로 출력 → manifest에 수기 반영 가능)
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
from bisect import bisect_right
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from law_rag.knowledge.loader import load_manifest  # noqa: E402
from law_rag.knowledge.refs import extract_guide_addresses  # noqa: E402

_MANIFEST = load_manifest()

MANUAL_DIR = ROOT.parent / "rnd_raw_manual"
OUT_DIR = ROOT / "data" / "guide"
EDITION = "2026.07"

# (파일명, 코드, 문서명). 부록 서식·의료법·보건의료기본법은 제외.
DOCS = [
    ("[본권] 26년도 7월 국가연구개발혁신법 매뉴얼 (1).pdf", "BON", "국가연구개발혁신법 매뉴얼"),
    ("[별권1] 26년도 7월 학생인건비통합관리 제도 매뉴얼.pdf", "B1", "학생인건비통합관리 제도 매뉴얼"),
    ("[별권2] 26년도 7월 국가연구개발사업 기술료제도 메뉴얼.pdf", "B2", "국가연구개발사업 기술료제도 매뉴얼"),
    ("[별권3] 26년도 7월 국가연구개발사업 제재처분 가이드라인.pdf", "B3", "국가연구개발사업 제재처분 가이드라인"),
    ("[별권4] 26년도 7월 연구시설·장비비 통합관리제 운영·관리 매뉴얼.pdf", "B4", "연구시설·장비비 통합관리제 운영·관리 매뉴얼"),
]

_RE_Q = re.compile(r"(?m)^\s*Q\s*(\d+)\s*[\.:]")
_RE_CHAP = re.compile(r"(?m)^\s*(제\d+장)\s+(\S[^\n]{0,40})")
_RE_SEC = re.compile(r"(?m)^\s*(제\d+절)\s+(\S[^\n]{0,40})")
_RE_SUB = re.compile(r"(?m)^\s*([가-하])\.\s+\S")          # 가. 나. … 소제목
_RE_QNA = re.compile(r"(?m)^\s*Q\s*&\s*A")                 # 'Q&A' 소제목
_TOC = re.compile(r"[·.]{4,}")  # 목차(점선 리더) 줄 제외

# R2: 추출 텍스트에서만 제거할 머리글/바닥글·잡기호
_RE_PAGENUM = re.compile(r"^\s*[-/\\]?\s*\d{1,4}\s*[-/\\]?\s*$")   # 쪽번호 줄
_RE_RUNHDR = re.compile(r"^\s*[「『][^」』]*[」』]\s*매뉴얼\s*$")   # 러닝 헤더
_RE_LEADER = re.compile(r"[·.]{3,}|￭+|￭+")                   # 점선 리더·￭


def _clean_text(t: str) -> str:
    """추출 텍스트에서 쪽번호·러닝헤더 줄 제거 + ￭/점선 리더 제거(머리글 혼입 0)."""
    lines = []
    for ln in t.split("\n"):
        if _RE_PAGENUM.match(ln) or _RE_RUNHDR.match(ln):
            continue
        ln = _RE_LEADER.sub("", ln).rstrip()
        lines.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _clean_path(p: str) -> str:
    return re.sub(r"\s{2,}", " ", _RE_LEADER.sub("", p)).strip(" >").strip()


def _split_qa(text: str) -> tuple[str, str]:
    """QA 텍스트를 질문/답변으로 분리. 'Q\\d+.' 접두 제거 후 첫 '?'까지 질문."""
    body = re.sub(r"^\s*Q\s*\d+\s*[\.:]\s*", "", text)
    m = re.search(r"\?", body)
    if m:
        return body[:m.end()].strip(), body[m.end():].strip()
    first = body.split("\n", 1)
    return first[0].strip(), (first[1].strip() if len(first) > 1 else "")


def _page_texts(path: Path) -> list[str]:
    import pdfplumber
    with pdfplumber.open(str(path)) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


def _offsets(pages: list[str]) -> tuple[str, list[int]]:
    full_parts, offs, cur = [], [], 0
    for t in pages:
        offs.append(cur)
        full_parts.append(t)
        cur += len(t) + 1  # '\n'
    return "\n".join(pages), offs


def _page_of(pos: int, offs: list[int]) -> int:
    return bisect_right(offs, pos)  # 1-based


def _headers(full: str, rx: re.Pattern) -> list[tuple[int, str]]:
    out = []
    for m in rx.finditer(full):
        line = full[m.start():full.find("\n", m.start()) if "\n" in full[m.start():] else len(full)]
        if _TOC.search(line):
            continue  # 목차 줄 제외
        out.append((m.start(), f"{m.group(1)} {m.group(2).strip()}"))
    return out


def _latest(pos: int, headers: list[tuple[int, str]]) -> str | None:
    label = None
    for hp, h in headers:
        if hp <= pos:
            label = h
        else:
            break
    return label


def parse_doc(path: Path, code: str, title: str) -> list[dict]:
    pages = _page_texts(path)
    full, offs = _offsets(pages)
    chaps, secs = _headers(full, _RE_CHAP), _headers(full, _RE_SEC)

    qs = list(_RE_Q.finditer(full))
    # R2: 경계 후보 = 다음 Q · 다음 장/절 제목 · 다음 소제목(가.~하.) · 'Q&A'
    bounds = sorted(set(
        [q.start() for q in qs]
        + [m.start() for m in _RE_CHAP.finditer(full)]
        + [m.start() for m in _RE_SEC.finditer(full)]
        + [m.start() for m in _RE_SUB.finditer(full)]
        + [m.start() for m in _RE_QNA.finditer(full)]
    ))
    chunks: list[dict] = []
    for m in qs:
        start = m.start()
        # start보다 큰 가장 가까운 경계로 끝을 자른다(다음 섹션/소제목 침범 방지)
        end = next((b for b in bounds if b > start), len(full))
        raw = full[start:end]
        text = _clean_text(raw)
        if len(text) < 10:
            continue
        chap, sec = _latest(start, chaps), _latest(start, secs)
        section_path = _clean_path(" > ".join(x for x in [chap, sec] if x)) or title
        q_part, a_part = _split_qa(text)
        cited = extract_guide_addresses(text, _MANIFEST)  # R1
        gid = f"MAN-{code}-{len(chunks)+1:04d}"
        chunks.append(dict(
            guide_id=gid, doc_title=title, edition=EDITION, section_path=section_path,
            kind="QA", text=text, question=q_part, answer=a_part,
            page_start=_page_of(start, offs), page_end=_page_of(max(start, end - 1), offs),
            cited_refs=cited, sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            legal_effect=False,
        ))
    return chunks


def main() -> int:
    if not MANUAL_DIR.exists():
        print(f"매뉴얼 폴더 없음: {MANUAL_DIR}")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for fname, code, title in DOCS:
        p = MANUAL_DIR / fname
        if not p.exists():
            print(f"  (건너뜀, 없음) {fname}")
            continue
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        chunks = parse_doc(p, code, title)
        (OUT_DIR / f"{code}.json").write_text(
            json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
        total += len(chunks)
        print(f"  {code} {title}: QA {len(chunks)}개  | PDF sha256={sha[:16]}…")
    print(f"총 QA 청크: {total}  → {OUT_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
