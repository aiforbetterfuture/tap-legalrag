"""
law_rag.clarify — 명확화 정책 결정기 v0 (A-5, Planner 자리).

Requirement-aware **Selective** Clarification의 결정론적 v0:
    '답이 갈릴 때만 묻는다'. 질문에 기관유형이 명시되면 확정(NONE), 조건/기관유형에 따라
    답이 갈리면 BRANCH(검색·조회형)/ASK(판단형), 흔한 기본값이 있으면 ASSUME.

적용요건 레지스트리(A-2)의 applies_to·준용을 근거로 '답 불변성'을 판정한다.
키워드·임계값은 상수 + # TODO(golden).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from law_rag.models import LawChunk, QueryKind

_REGISTRY = Path(__file__).resolve().parents[2] / "data" / "registry" / "applicability.json"

# 기관유형 어휘(긴 것 먼저)  # TODO(golden)
_INST_WORDS = ["기타비영리기관", "정부출연기관", "비영리기관", "영리기관", "정부출연", "비영리", "대학"]
# 선택/판단(되묻기) 패턴
_CHOICE = re.compile(r"어떤\s*것|어느\s*것|어느\s*쪽|중\s*(?:어느|어떤|무엇|어떻게)|중\s*[^.?]{0,12}(?:해야|하는지|변경)")
# 조건부(답이 갈림) 패턴
_COND = re.compile(r"다르게|달리\s*적용|경우에\s*따라|예외|조건부|할\s*수\s*있는\s*방법")


def _load_junyong() -> dict:
    if not _REGISTRY.exists():
        return {}
    reg = json.loads(_REGISTRY.read_text(encoding="utf-8"))
    return {j["from"]: j for j in reg.get("준용", [])}


def decide(query, evidence: list[LawChunk]) -> dict:
    """명확화 정책 결정. evidence=정렬된 근거(상위). 반환 dict는 Answer.clarification 형식."""
    text = query.text
    has_type = next((w for w in _INST_WORDS if w in text), None)
    is_judgment = (query.kind == QueryKind.judgment) or bool(_CHOICE.search(text))

    # 근거 중 기관유형-특정 조문의 유형 집합(답이 기관유형에 따라 갈리는지)
    types = sorted({c.applies_to for c in evidence[:5] if getattr(c, "applies_to", None)})

    # 1) 질문이 기관유형을 특정 → 확정(해당 조문·준용으로). 되묻지 않는다.
    if has_type:
        jy = _load_junyong()
        src_art = next((c.article_no for c in evidence[:5]
                        if getattr(c, "applies_to", None) and c.article_no in jy), None)
        note = "질문이 기관유형(%s)을 특정 → 확정" % has_type
        if src_art:
            note += f" (준용: {src_art}조→{','.join(jy[src_art]['to'])}조 함께 인용)"
        return {"policy": "NONE", "slot": "기관유형", "question": "", "branches": [], "note": note}

    # 2) 선택/판단형(답이 조건에 따라 갈리고 사용자가 골라야) → 되묻기
    if is_judgment:
        return {"policy": "ASK", "slot": "쟁점",
                "question": "어떤 상황(대상·단계)을 기준으로 판단할지 알려주시면 근거를 특정하겠습니다.",
                "branches": []}

    # 3) 조건부/기관유형 2개 이상 → 분기 제시(되묻지 않고 경우별로)
    if _COND.search(text) or query.kind == QueryKind.lookup_conditional or len(types) >= 2:
        return {"policy": "BRANCH", "slot": "조건",
                "question": "", "branches": types or ["조건별로 상이"]}

    # 4) 그 외 → 확정
    return {"policy": "NONE", "slot": "", "question": "", "branches": []}
