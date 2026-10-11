"""
law_rag.gate — 근거 게이트 결정 함수 (v2 전략 2.3 / 작업지시서 22 §1).

입력(질의·정렬 근거·관련도·설정)에서 AnswerDecision을 낸다. 설정의 on/off만으로 B0~B4를 구성한다.

P·T는 **질문 텍스트에 담긴 인용**("시행령 제64조 제2항에 따르면…")에만 적용한다.
검색으로 찾아온 현행 조문에는 적용하지 않는다(현행 조문은 항상 CURRENT_VALID).
  - P: 질문 인용을 pgate로 해석 → 확정 안 되면 CONFIRM, 외부 법령이면 ABSTAIN.
  - T: 질문 인용이 계보상 '낡은 번호'면 CORRECT_PREMISE(교정 위치 제시), 현행·계보에도 없으면 CONFIRM.
  - 질문에 인용이 없으면 P·T는 통과(path에 n/a).
관련도 임계값은 게이트 조건이 아니라 '범위 밖' 판정 전용(고정값).

평가 클래스: ANSWER | CONFIRM | ABSTAIN | CORRECT.
"""
from __future__ import annotations

import re

from law_rag.clarify import decide as clarify_decide
from law_rag.provenance import _RE_CITE, resolve as resolve_citation
from law_rag.temporal import classify as temporal_classify

OOS_RELEVANCE = 0.15  # 범위 밖 판정 전용 고정 임계값(골든 이전 고정). 게이트 조건 아님.

_manifest_cache = None


def _manifest():
    global _manifest_cache
    if _manifest_cache is None:
        from law_rag.knowledge.loader import load_manifest
        _manifest_cache = load_manifest()
    return _manifest_cache


def _question_citation(text: str):
    """질문 텍스트의 첫 조문 인용 문자열을 반환(없으면 None). 앞의 법령명 토큰까지 포함해 넘긴다."""
    m = _RE_CITE.search(text)
    if not m:
        return None
    start = m.start()
    # 바로 앞 30자에 법령명/한정어가 있으면 함께(해석기가 self-token으로 인식)
    head = text[max(0, start - 30):start]
    hm = re.search(r"[「『][^」』]+[」』]\s*$|(?:국가연구개발혁신법|혁신법|시행령|시행규칙|사용\s*기준|성과평가법|영|규칙)\s*$", head)
    s = (start - (len(head) - hm.start())) if hm else start
    return text[s:m.end()]


def decide_answer(query, ranked, relevance, config, corpus_index, lineage=None) -> dict:
    """설정별 결정. config: {P,T,A,gate,force_answer:bool}. 반환 {decision, reason, path}."""
    path = []
    chunks = ranked.ordered if ranked else []

    if config.get("force_answer"):
        return {"decision": "ANSWER", "reason": "always-ANSWER(게이트 없음)", "path": ["force"]}

    # B0: 레이어·게이트 모두 꺼짐 → 근거 있으면 ANSWER
    if not any(config.get(k) for k in ("P", "T", "A", "gate")):
        return {"decision": "ANSWER" if chunks else "ABSTAIN", "reason": "B0 무게이트", "path": ["b0"]}

    # 범위 밖 판정(게이트 켤 때만, 별도 고정 임계값)
    if config.get("gate") and (not chunks or relevance < OOS_RELEVANCE):
        return {"decision": "ABSTAIN", "reason": f"범위밖/근거부족(rel={relevance:.2f})", "path": ["oos"]}

    # 질문 속 인용(있을 때만 P·T 적용)
    cite = _question_citation(query.text)
    ref = resolve_citation(cite, query.text, _manifest(), mode="pgate") if cite else None

    # P: 질문 인용의 출처 확정
    if config.get("P"):
        if ref is None:
            path.append("P=n/a")
        elif ref.is_external:
            return {"decision": "ABSTAIN", "reason": "질문 인용이 외부(코퍼스 밖) 법령", "path": path + ["P=external"]}
        elif ref.status != "RESOLVED":
            return {"decision": "CONFIRM", "reason": "질문 인용 출처 미확정", "path": path + ["P=unresolved"]}
        else:
            path.append(f"P={ref.law_id}:{ref.article}")

    # T: 질문 인용의 시점(계보상 낡은 번호면 전제 교정)
    if config.get("T"):
        if ref is None or ref.law_id is None:
            path.append("T=n/a")
        else:
            t = temporal_classify(ref.law_id, ref.article, ref.paragraph, corpus_index, lineage)
            path.append(f"T={t['status']}")
            if t["status"] == "HISTORIC_VALID":  # 낡은 번호가 현행엔 없음 → 안전하게 교정
                return {"decision": "CORRECT",
                        "reason": f"질문 인용 '낡은 번호' → 현행 {t['corrected_article']}조{t['corrected_paragraph'] or ''}항 교정",
                        "path": path}
            if t["status"] == "AMBIGUOUS_HISTORIC":  # 낡은 번호가 현행에도 존재 → 단정 금지, 후보 2개 확인
                return {"decision": "CONFIRM",
                        "reason": "인용 번호가 현행에도 있고 계보상 밀린 번호이기도 함 — " + " / ".join(t["candidates"]),
                        "path": path}
            if t["status"] == "DRIFT_UNRESOLVED":
                return {"decision": "CONFIRM", "reason": "질문 인용이 현행·계보에 없음", "path": path}

    # A: 적용 답불변 — 갈리면 CONFIRM
    if config.get("A"):
        pol = clarify_decide(query, chunks).get("policy")
        path.append(f"A={pol}")
        if pol in ("ASK", "BRANCH"):
            return {"decision": "CONFIRM", "reason": f"적용 분기({pol})", "path": path}

    return {"decision": "ANSWER" if chunks else "ABSTAIN", "reason": "게이트 통과", "path": path or ["pass"]}
