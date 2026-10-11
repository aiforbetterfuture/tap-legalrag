"""
scripts/check_guide_drift.py — 해설 인용 표류 탐지기 (A-3).

매뉴얼이 '낡은 조문 번호'를 인용하는 경우(개정으로 항이 신설·밀림)를 잡는다.
2단계로 오탐을 줄인다:
  1단계(구조 후보): 인용된 조에 '신설' 표시 항이 있어 번호가 밀렸을 가능성.
  2단계(내용 확인): 인용 문장(±150자) 토큰 겹침을 '인용 항' vs '이웃 항(±1)'과 비교.
                    이웃 항이 인용 항보다 겹침이 DRIFT_MARGIN 이상 크면 → 내용이 이동함(DRIFT).

출력: 내부 설계 노트 + GuideChunk.drift_warnings 채워 data/guide/*.json 갱신.
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from law_rag.knowledge.loader import load_corpus  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
GUIDE_DIR = ROOT / "data" / "guide"
OUT_MD = ROOT / "docs" / "내부 설계 노트" / "11_GUIDE_DRIFT.md"

DRIFT_MARGIN = 0.15          # 이웃 항이 이만큼 더 겹치면 표류로 본다  # TODO(golden)
CONTEXT = 150                # 인용 문장 앞뒤 문맥 길이
_RE_TOK = re.compile(r"[가-힣]{2,}")
_DOC_CODES = ("BON", "B1", "B2", "B3", "B4")


def _toks(s: str) -> set[str]:
    return set(_RE_TOK.findall(s or ""))


def _overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a) if a else 0.0


def _article_paras(corpus, law_id: str, art: str) -> dict[str, object]:
    """(law_id, art)의 항번호 → 청크. 항 없는 조는 {'0': 청크}."""
    out = {}
    for c in corpus:
        if c.law_id == law_id and c.article_no == art and not c.is_split_parent:
            out[c.paragraph_no or "0"] = c
    return out


def _cite_context(text: str, art: str, para: str) -> str:
    """해설 본문에서 '제{art}조제{para}항' 언급 주변 ±CONTEXT 문맥."""
    pats = [rf"제\s*{art}\s*조\s*제\s*{para}\s*항", rf"제\s*{art}\s*조"]
    for p in pats:
        m = re.search(p, text)
        if m:
            return text[max(0, m.start() - CONTEXT): m.end() + CONTEXT]
    return text[:CONTEXT * 2]


def detect(corpus, guide_chunks: list[dict]) -> list[tuple[str, str]]:
    """(guide_id, warning) 목록. 각 guide의 drift_warnings도 채운다(in place)."""
    warns: list[tuple[str, str]] = []
    for g in guide_chunks:
        g["drift_warnings"] = []
        for addr in g.get("cited_refs", []):
            if addr.startswith("UNRESOLVED") or ":H" not in addr:
                continue
            lid, tag, htag = addr.split(":")
            art, para = tag[1:], htag[1:]
            paras = _article_paras(corpus, lid, art)
            if para not in paras:
                continue
            # 1단계: 이 조에 '신설' 표시가 있는가(번호 밀림 가능성)
            if not any("신설" in c.body_text for c in paras.values()):
                continue
            # 2단계: 인용 문맥 vs 인용 항 / 이웃 항 겹침 비교
            ctx = _toks(_cite_context(g["text"], art, para))
            if not ctx:
                continue
            cited_ov = _overlap(ctx, _toks(paras[para].body_text))
            best_para, best_ov = para, cited_ov
            for d in (1, -1, 2):
                nb = str(int(para) + d) if para.isdigit() else None
                if nb and nb in paras:
                    ov = _overlap(ctx, _toks(paras[nb].body_text))
                    if ov > best_ov:
                        best_para, best_ov = nb, ov
            if best_para != para and best_ov - cited_ov >= DRIFT_MARGIN:
                w = f"매뉴얼 인용 '{_lawname(corpus, lid)} 제{art}조제{para}항'은 현행 제{best_para}항으로 보임"
                g["drift_warnings"].append(w)
                warns.append((g["guide_id"], w))
    return warns


def _lawname(corpus, lid: str) -> str:
    for c in corpus:
        if c.law_id == lid:
            return c.law_short_name or c.law_name
    return lid


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    corpus = load_corpus(FIXTURES, split=True)
    warns = []
    for code in _DOC_CODES:
        fp = GUIDE_DIR / f"{code}.json"
        if not fp.exists():
            continue
        chunks = json.loads(fp.read_text(encoding="utf-8"))
        warns += detect(corpus, chunks)
        fp.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# 해설 인용 표류 탐지 (11_GUIDE_DRIFT)", "",
             f"- DRIFT_MARGIN={DRIFT_MARGIN}, 2단계(구조+내용). 탐지 {len(warns)}건", ""]
    for gid, w in warns:
        lines.append(f"- {gid}: {w}")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"표류 탐지 {len(warns)}건 → {OUT_MD.relative_to(ROOT)}")
    for gid, w in warns[:12]:
        print(" ", gid, w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
