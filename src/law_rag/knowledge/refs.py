"""
law_rag.knowledge.refs — 조문 참조 스캔·해석 공용 모듈 (P0-1 / P0-2).

왜 한곳에 모으나:
    parser.py는 본문에서 참조를 뽑고, loader.py는 그 참조를 (법령·조·항)으로
    해석해 엣지로 만든다. 두 쪽이 서로 다른 규칙을 쓰면 "법 제11조"가 한쪽에선
    상위법, 다른쪽에선 자기 법령이 되는 불일치가 생긴다(P0-1의 실제 원인).
    참조 문법과 해석 규칙을 이 파일 하나로 통일한다.

핵심 원칙:
    - 추출 단계(scan_refs): 본문에 쓰인 '한정어'(법/영/이 법/「법령명」)를 살려서
      정규화 문자열로 남긴다. 숫자만 수확하지 않는다.
    - 해석 단계(resolve_ref): 한정어의 '의미'(누가 '법'인가)는 코퍼스에서 유도한
      관계맵(parent_of/decree_of)으로 판단한다. 하드코딩하지 않는다.
    - 항까지 지정된 참조는 항 단위로 해석한다(제2항 → H2, 제1항 아님).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── 한정어 어휘 (본문에 쓰인 그대로 보존) ──────────────────────
#   '법' 앞에 한글이 붙은 경우(예: '방법', '혁신법')는 한정어가 아니다 → (?<![가-힣])
_QUAL = r"이\s*법|같은\s*법|이\s*영|같은\s*영|법|영|령|규칙"

# 본문 스캔용: 「법령명」(선택) + 한정어(선택) + 제N조[제M항]
_RE_SCAN = re.compile(
    r"(?:「(?P<name>[^」]+)」\s*)?"
    r"(?:(?<![가-힣])(?P<qual>" + _QUAL + r")\s*)?"
    r"제(?P<art>\d+(?:의\d+)?)조"
    r"(?:\s*제(?P<para>\d+)항)?"
)

# 저장된 참조 문자열 파싱용(앵커): 「법령명」 또는 평문 법령명 + 한정어 + 제N조[제M항]
#   평문 법령명은 비탐욕으로 최소 매칭하되, 한정어가 아니면 이름으로 흡수한다.
_RE_PARSE = re.compile(
    r"^\s*(?:「(?P<bname>[^」]+)」|(?P<name>[^「」]*?))\s*"
    r"(?:(?<![가-힣])(?P<qual>" + _QUAL + r")\s*)?"
    r"제(?P<art>\d+(?:의\d+)?)조"
    r"(?:\s*제(?P<para>\d+)항)?"
)

_RE_APPENDIX = re.compile(r"별표\s*(\d+(?:의\d+)?)")
# 열거 상속: 두 참조 사이가 구분자(및/또는/·/,/공백)뿐이면 앞의 한정어를 물려받는다.
_RE_SEP_ONLY = re.compile(r"^[\s및또는·,]*$")


@dataclass
class Ref:
    """하나의 조문 참조. 본문에 쓰인 한정어를 보존한다."""
    name: str | None   # 「법령명」 또는 평문 법령명
    qual: str | None   # 법 / 영 / 이 법 / 같은 영 ... (공백 정규화)
    art: str           # '11', '21의2'
    para: str | None   # '2' 또는 None

    def canonical(self) -> str:
        """정규화 문자열. 한정어를 앞에 붙여 해석 단계가 의미를 복원할 수 있게 한다."""
        if self.name:
            head = f"「{self.name}」 "
        elif self.qual:
            head = f"{self.qual} "
        else:
            head = ""
        tail = f"제{self.art}조" + (f"제{self.para}항" if self.para else "")
        return head + tail


def _norm_qual(q: str | None) -> str | None:
    return re.sub(r"\s+", "", q) if q else None


def scan_refs(body: str) -> list[Ref]:
    """본문에서 조문 참조를 순서대로 추출(한정어 보존 + 열거 상속)."""
    refs: list[Ref] = []
    prev_end = -1
    prev_name: str | None = None
    prev_qual: str | None = None
    for m in _RE_SCAN.finditer(body):
        name = (m.group("name") or "").strip() or None
        qual = _norm_qual(m.group("qual"))
        art, para = m.group("art"), m.group("para")
        # 열거 상속: "법 제11조 및 제12조"의 뒤쪽은 앞의 '법'을 물려받는다.
        if name is None and qual is None and prev_end >= 0:
            if _RE_SEP_ONLY.match(body[prev_end:m.start()]):
                name, qual = prev_name, prev_qual
        refs.append(Ref(name=name, qual=qual, art=art, para=para))
        prev_end, prev_name, prev_qual = m.end(), name, qual
    return refs


def parse_ref_string(ref: str) -> Ref | None:
    """저장된 참조 문자열 1개 → Ref. 조문 참조가 아니면 None(별표 등)."""
    m = _RE_PARSE.match(ref)
    if not m:
        return None
    name = m.group("bname") or (m.group("name") or "").strip() or None
    return Ref(name=name, qual=_norm_qual(m.group("qual")),
               art=m.group("art"), para=m.group("para"))


def appendix_no(ref: str) -> str | None:
    """참조 문자열에서 '별표N' 번호 추출. 없으면 None."""
    m = _RE_APPENDIX.search(ref)
    return f"별표{m.group(1)}" if m else None


# ═══════════════════════════════════════════════════════════════
# R1: 해설(매뉴얼) 인용 추출 — 법령명 별칭을 살려 한정어 복원
#   매뉴얼은 "시행령 제9조", "사용기준 제73조", "같은 법 시행령 제14조"처럼
#   '법령명'으로 한정한다. 이를 살려 올바른 법령으로 해석한다(013774 기본값 금지).
#   한정어 없는 "제N조"는 같은 문장의 직전 법령명을 이어받고, 없으면 UNRESOLVED.
# ═══════════════════════════════════════════════════════════════
_GUIDE_RX_CACHE: dict[tuple, re.Pattern] = {}
# 문장 경계: 마침표/줄바꿈/'다.' 종결
_RE_SENT = re.compile(r"(?<=[.!?。])\s+|\n+")


def guide_family_roots(manifest: dict) -> dict:
    """혁신법 패밀리의 법률/시행령/시행규칙 law_id (상대 한정어 '법'/'영'/'규칙' 해석용)."""
    roots = {"ACT": None, "DECREE": None, "RULE": None}
    for e in manifest.get("laws", []):
        if e["law_name"] == "국가연구개발혁신법":
            roots["ACT"] = e["law_id"]
    for e in manifest.get("laws", []):
        if e.get("parent_law_name") == "국가연구개발혁신법":
            if e.get("law_type") == "대통령령":
                roots["DECREE"] = e["law_id"]
            elif e.get("law_type") == "부령":
                roots["RULE"] = e["law_id"]
    return roots


def _alias_nospace(manifest: dict) -> dict:
    out = {}
    for e in manifest.get("laws", []):
        for a in e.get("aliases", []):
            out[a.replace(" ", "")] = e["law_id"]
    return out


def _guide_regex(alias_keys: tuple) -> re.Pattern:
    if alias_keys in _GUIDE_RX_CACHE:
        return _GUIDE_RX_CACHE[alias_keys]
    # 긴 별칭 먼저(최장 일치) + 상대 한정어. 공백은 \s*로.
    rel = ["같은 법 시행규칙", "같은 법 시행령", "동법 시행규칙", "동법 시행령",
           "이 법", "같은 법", "동법", "이 영", "같은 영", "시행령", "시행규칙",
           "법", "영", "규칙"]
    toks = sorted(set(alias_keys) | set(rel), key=len, reverse=True)
    alt = "|".join(re.escape(t).replace(r"\ ", r"\s*") for t in toks)
    rx = re.compile(
        r"(?:「(?P<bname>[^」]+)」\s*)?"
        r"(?:(?<![가-힣])(?P<alias>" + alt + r")\s*)?"
        r"제(?P<art>\d+(?:의\d+)?)조(?:\s*제(?P<para>\d+)항)?"
    )
    _GUIDE_RX_CACHE[alias_keys] = rx
    return rx


def _resolve_law_token(tok: str, alias2id: dict, roots: dict) -> str | None:
    """법령명/한정어 토큰 → law_id. 상대 한정어는 혁신법 패밀리 뿌리로."""
    if not tok:
        return None
    n = tok.replace(" ", "")
    if n in alias2id:
        return alias2id[n]
    for p in ("같은법", "동법", "혁신법", "국가연구개발혁신법", "이법"):
        if n.startswith(p) and len(n) > len(p):
            n = n[len(p):]
            break
    if "시행령" in n or n in ("영", "이영", "같은영"):
        return roots.get("DECREE")
    if "시행규칙" in n or n == "규칙":
        return roots.get("RULE")
    if n in ("법", "이법", "같은법", "동법"):
        return roots.get("ACT")
    return alias2id.get(n)


def extract_guide_addresses(text: str, manifest: dict) -> list[str]:
    """해설 본문 → 인용 주소 목록. 해석되면 'LAWID:J{조}[:H{항}]', 아니면 'UNRESOLVED:제N조…'.

    문장 단위로 보며, 한정어 없는 '제N조'는 같은 문장의 직전 법령명을 이어받는다.
    """
    alias2id = _alias_nospace(manifest)
    roots = guide_family_roots(manifest)
    # regex는 원본 별칭(공백 포함)으로 만든다 — 공백은 \s*로 치환되어 "연구개발비 사용 기준"도 매칭.
    orig = tuple(sorted({a for e in manifest.get("laws", []) for a in e.get("aliases", [])}))
    rx = _guide_regex(orig)
    out: list[str] = []
    for sent in _RE_SENT.split(text):
        current: str | None = None
        for m in rx.finditer(sent):
            name = m.group("bname") or m.group("alias")
            lid = _resolve_law_token(name, alias2id, roots) if name else None
            if lid:
                current = lid
            use = lid or current
            art, para = m.group("art"), m.group("para")
            if use:
                addr = f"{use}:J{art}" + (f":H{para}" if para else "")
            else:
                addr = "UNRESOLVED:제%s조%s" % (art, f"제{para}항" if para else "")
            if addr not in out:
                out.append(addr)
    return out
