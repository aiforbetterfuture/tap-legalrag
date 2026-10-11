"""
law_rag.temporal — T(시점) 검증 v1: '낡은 조문 번호 인용' 탐지 (소규모 프로토타입).

주의(과대포장 금지): 과거 법령 전 판본을 처리하는 시간 인식 시스템이 아니다.
현행 조문의 <신설·개정 날짜> 표시(marker)와 수작업 검증 계보(manual)로, 매뉴얼이 인용한
'낡은 조문 번호'를 현행 위치로 교정하거나 근거가 없으면 DRIFT로 표시한다. 대표 사례만 다룬다.

상태(DriftStatus): CURRENT_VALID | HISTORIC_VALID | DRIFT_UNRESOLVED | UNKNOWN.
"""
from __future__ import annotations

import json
from pathlib import Path

_LINEAGE = Path(__file__).resolve().parents[2] / "data" / "registry" / "lineage.json"


def load_lineage() -> dict:
    if not _LINEAGE.exists():
        return {"entries": []}
    return json.loads(_LINEAGE.read_text(encoding="utf-8"))


def _key(art, para) -> str:
    return f"{art}:{para}" if para else f"{art}"


def _exists_in_corpus(corpus_index: dict, law_id: str, article: str, paragraph: str | None) -> bool:
    """현행 코퍼스에 (law, 조[, 항])이 존재하는가. 조만 주어지면 그 조의 '아무 항'이라도 있으면 True.
    (버그 수정: 항으로 분할된 조는 'LAW-..-J{조}' 단독 chunk_id가 없으므로 조 단위 스캔으로 확인)"""
    for c in corpus_index.values():
        if c.law_id != law_id or c.article_no != article:
            continue
        if paragraph is None or c.paragraph_no == paragraph:
            return True
    return False


def classify(law_id: str, article: str, paragraph: str | None,
             corpus_index: dict, lineage: dict | None = None) -> dict:
    """인용 (law_id, 조, 항)의 시점 타당성.

    반환: {status, corrected_article, corrected_paragraph, method, evidence, candidates?}
      - 계보상 '낡은 번호'이고 그 번호가 현행엔 **없음** → HISTORIC_VALID(현행 위치로 교정)
      - 계보상 '낡은 번호'인데 그 번호가 현행에도 **있음** → AMBIGUOUS_HISTORIC(단정 금지, 후보 2개)
      - 현행 코퍼스에 (조[,항]) 존재 → CURRENT_VALID
      - 어디에도 없음 → DRIFT_UNRESOLVED
    """
    lin = lineage or load_lineage()
    stale = _key(article, paragraph)
    exists_now = _exists_in_corpus(corpus_index, law_id, article, paragraph)
    for e in lin.get("entries", []):
        if e["law_id"] == law_id and e["stale"] == stale:
            ca, cp = (e["current"].split(":") + [None])[:2]
            if exists_now:
                # 낡은 번호가 현행에도 존재 → 단정 교정 금지(법 전문가 지적). 후보 2개로 확인 요청.
                return {"status": "AMBIGUOUS_HISTORIC", "corrected_article": ca, "corrected_paragraph": cp,
                        "method": e["method"], "evidence": e["evidence"],
                        "candidates": [f"현행 {article}조{('제'+paragraph+'항') if paragraph else ''}",
                                       f"종전 번호의 현행 위치 {ca}조{('제'+cp+'항') if cp else ''}"]}
            return {"status": "HISTORIC_VALID", "corrected_article": ca,
                    "corrected_paragraph": cp, "method": e["method"], "evidence": e["evidence"]}
    if exists_now:
        return {"status": "CURRENT_VALID", "corrected_article": article,
                "corrected_paragraph": paragraph, "method": "corpus", "evidence": ""}
    return {"status": "DRIFT_UNRESOLVED", "corrected_article": None,
            "corrected_paragraph": None, "method": "none", "evidence": "현행·계보 어디에도 없음"}
