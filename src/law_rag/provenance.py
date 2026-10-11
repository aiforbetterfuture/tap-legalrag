"""
law_rag.provenance — P(출처) 검증: 인용을 '맥락'으로 해석하되, 무한정어는 기본 귀속하지 않는다.

v2 전략 E1. 세 모드를 비교한다(모드명은 결과표·문서와 일치):
  - mode="naive"   : 인용 문자열 자체에 법령명이 없으면 문맥을 보지 않고 무조건 013774(혁신법).
                     외부 법령 감지·문맥 상속 없음(원래 P0 동작 재현, 기준선).
  - mode="fallback": 문맥 상속 + 외부(코퍼스 밖) 감지는 하되, '그래도 못 찾으면' 013774로 귀속.
  - mode="pgate"   : 인용 자체 또는 맥락의 '직전 법령명'으로만 확정. 근거 없으면 보류(UNRESOLVED).
                     코퍼스 밖 법령(「고등교육법」 등)은 외부로 보고 보류한다(정당한 보류).
  (하위호환: mode="default"는 "fallback"의 별칭)

상태(CitationStatus): RESOLVED | PARTIAL | AMBIGUOUS | UNRESOLVED (데이터 계약 v1 §2).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from law_rag.knowledge.refs import _alias_nospace, _resolve_law_token, guide_family_roots

_FAMILY_ACT = "013774"  # 매뉴얼(혁신법) 도메인 기본 법률

# 인용 문자열: 제N조[(제목)][제M항][제K호]. 조와 항 사이의 괄호 제목을 건너뛴다(버그 수정).
_RE_CITE = re.compile(
    r"제\s*(\d+(?:의\d+)?)\s*조(?:\s*\([^)]*\))?(?:\s*제\s*(\d+)\s*항)?(?:\s*제\s*(\d+)\s*호)?"
)
# 맥락에서 법령 토큰(괄호명 + 별칭 + 상대 한정어). 긴 것 우선은 아래에서 처리.
_REL = r"이\s*법|같은\s*법|동법|이\s*영|같은\s*영|시행규칙|시행령|규칙|법|영"


@dataclass
class LegalRef:
    raw: str
    law_id: str | None
    article: str | None
    paragraph: str | None
    item: str | None
    status: str  # RESOLVED | PARTIAL | AMBIGUOUS | UNRESOLVED
    method: str  # explicit_name | alias | inherit | default | external | none
    is_external: bool = False
    candidates: list = field(default_factory=list)


def _law_tokens(text: str, manifest: dict):
    """맥락 속 법령 토큰을 (위치, law_id 또는 'EXT:이름', 방법)으로 수집."""
    alias = _alias_nospace(manifest)
    roots = guide_family_roots(manifest)
    out = []
    for m in re.finditer(r"[「｢『]([^」｣』]+)[」｣』]", text):  # 괄호 법령명(내·외부)
        name = m.group(1).strip()
        nid = alias.get(name.replace(" ", "")) or _resolve_law_token(name, alias, roots)
        out.append((m.start(), nid if nid else f"EXT:{name}", "explicit_name" if nid else "external"))
    for m in re.finditer(r"(?<![가-힣])(" + "|".join(sorted(alias, key=len, reverse=True)) + r")", text):
        out.append((m.start(), alias[m.group(1)], "alias"))
    for m in re.finditer(r"(?<![가-힣])(" + _REL + r")\s*제\s*\d+\s*조", text):
        tok = re.sub(r"\s+", "", m.group(1))
        out.append((m.start(), _resolve_law_token(tok, _alias_nospace(manifest), roots), "alias"))
    return sorted(out, key=lambda x: x[0])


def resolve(raw_citation: str, context: str, manifest: dict, *, mode: str = "pgate") -> LegalRef:
    if mode == "default":
        mode = "fallback"  # 하위호환 별칭
    cm = _RE_CITE.search(raw_citation)
    art = cm.group(1) if cm else None
    para = cm.group(2) if cm else None
    item = cm.group(3) if cm else None

    # 인용 문자열 자체에 붙은 법령명(모든 모드 공통으로 먼저 본다)
    self_tokens = _law_tokens(raw_citation, manifest)
    self_lid = self_tokens[-1][1] if self_tokens else None

    # ── naive: 문맥·외부 감지 없음. 인용에 코퍼스 법령명 있으면 그것, 아니면 무조건 013774 ──
    if mode == "naive":
        if self_lid and not str(self_lid).startswith("EXT:"):
            return LegalRef(raw_citation, self_lid, art, para, item, "RESOLVED", "explicit_name")
        return LegalRef(raw_citation, _FAMILY_ACT, art, para, item, "RESOLVED", "naive_default")

    # ── fallback / pgate: 인용명 → 문맥 직전 법령명 순 ──
    law_id, method, external = None, "none", False
    if self_lid is not None:
        external = str(self_lid).startswith("EXT:")
        law_id, method = (None, "external") if external else (self_lid, "explicit_name")
    else:
        pos = context.find(raw_citation.strip().split()[0]) if raw_citation.strip() else -1
        if pos < 0 and art:
            pm = re.search(rf"제\s*{art}\s*조", context)
            pos = pm.start() if pm else len(context)
        toks = [t for t in _law_tokens(context, manifest) if t[0] <= (pos if pos >= 0 else len(context))]
        if toks:
            _, lid, _ = toks[-1]
            external = str(lid).startswith("EXT:")
            law_id, method = (None, "external") if external else (lid, "inherit")

    if external:  # 코퍼스 밖 법령 → 두 모드 모두 보류(정당)
        return LegalRef(raw_citation, None, art, para, item, "UNRESOLVED", "external", is_external=True)
    if law_id is None:
        if mode == "fallback":
            return LegalRef(raw_citation, _FAMILY_ACT, art, para, item, "RESOLVED", "fallback_default")
        return LegalRef(raw_citation, None, art, para, item, "UNRESOLVED", "none")  # pgate: 보류
    return LegalRef(raw_citation, law_id, art, para, item, "RESOLVED", method)
