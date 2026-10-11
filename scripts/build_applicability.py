"""
scripts/build_applicability.py — 적용요건 레지스트리 (A-2).

무엇을:
    (1) applies_to: 사용기준(75386) 조 → 적용 기관유형(조문 제목의 기관명으로 판정).
        영리 15~17·60~69 / 정부출연 34~44 / 대학 45~51 / 기타비영리 52~59.
    (2) 준용: "제N조[부터 제M조까지]를 준용한다. 이 경우 'A'은 'B'로 본다"
        → {from, to[], substitute{A:B}}  (예: 54→36, 정부출연기관→기타비영리기관)
왜:
    "대학에 적용되는 간접비 기준은?"처럼 '기관유형'에 따라 답이 갈리는 질문을, 준용 의제를 따라
    기준 조문으로 모아 '답이 갈리는지'를 기계가 판단하게 한다(A-5 명확화 정책의 근거표).

출력: data/registry/applicability.json
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
OUT = ROOT / "data" / "registry" / "applicability.json"
USE_STD_LAW = "75386"  # 연구개발비 사용 기준

# 기관유형 키워드(긴 것 먼저)
_INST = ["기타비영리기관", "정부출연기관", "영리기관", "대학"]
# 준용: '준용' 직전의 조문 나열 절만 잡는다(기사 전체가 아니라). 자기조·무관 참조 잡음 방지.
_RE_JUNYONG_CLAUSE = re.compile(
    r"((?:제\d+조(?:\s*부터\s*제\d+조까지)?(?:\s*제\d+항(?:\s*부터\s*제\d+항까지)?)?[,\s및ㆍ·]*)+?)(?:를|을)?\s*준용"
)
_RE_RANGE = re.compile(r"제(\d+)조(?:\s*부터\s*제(\d+)조까지)?")
_RE_SUBST = re.compile(
    r"이\s*경우\s*[\"“']([^\"”']+)[\"”']\s*(?:을|를|은|는)\s*[\"“']([^\"”']+)[\"”']\s*(?:으)?로\s*본다"
)


def _inst_of(title: str) -> str | None:
    for k in _INST:
        if k in (title or ""):
            return k
    return None


def _art_int(a: str) -> int:
    m = re.match(r"(\d+)", a)
    return int(m.group(1)) if m else 10**9


def build(corpus) -> dict:
    # 대표 청크(조별 1개: 제목·본문 머리말 포함) 수집
    reps: dict[str, object] = {}
    for c in corpus:
        if c.law_id != USE_STD_LAW or c.article_no.startswith("별표"):
            continue
        if c.article_no not in reps or (c.paragraph_no in (None, "1")):
            reps.setdefault(c.article_no, c)
    applies: dict[str, str] = {}
    junyong: list[dict] = []
    for art, c in sorted(reps.items(), key=lambda kv: _art_int(kv[0])):
        inst = _inst_of(c.article_title)
        if inst:
            applies[art] = inst
        to: list[str] = []
        for clause_m in _RE_JUNYONG_CLAUSE.finditer(c.body_text):
            for m in _RE_RANGE.finditer(clause_m.group(1)):
                lo = int(m.group(1))
                hi = int(m.group(2)) if m.group(2) else lo
                to.extend(str(n) for n in range(lo, hi + 1) if str(n) != art)  # 자기조 제외
        if to:
            ms = _RE_SUBST.search(c.body_text)
            sub = {ms.group(1): ms.group(2)} if ms else {}
            junyong.append({"law_id": USE_STD_LAW, "from": art,
                            "to": sorted(set(to), key=int), "substitute": sub})
    return {"applies_to": {USE_STD_LAW: applies}, "준용": junyong}


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    corpus = load_corpus(FIXTURES, split=True)
    reg = build(corpus)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ap = reg["applies_to"][USE_STD_LAW]
    from collections import Counter
    print("applies_to 기관유형 분포:", dict(Counter(ap.values())))
    print(f"준용 관계 {len(reg['준용'])}건. 예:",
          [(j["from"], j["to"][:3] + (["…"] if len(j["to"]) > 3 else []),
            list(j["substitute"].items())[:1]) for j in reg["준용"][:6]])
    print(f"저장: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
