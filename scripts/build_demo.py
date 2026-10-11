"""
scripts/build_demo.py — 시연 HTML을 '실제 파이프라인 실행 결과'에서 생성 (작업지시서 22 §1-3).

손으로 쓴 문구를 넣지 않는다. 5개 질의를 실제로 돌려 결정·상태·근거 경로·run_id·판본을 그대로 출력한다.
사용: python scripts/build_demo.py  → docs/demo/index.html
"""
from __future__ import annotations

import datetime as dt
import html
import io
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from law_rag.gate import decide_answer  # noqa: E402
from law_rag.knowledge import Assembler, InMemoryEdgeStore  # noqa: E402
from law_rag.knowledge.loader import build_edges, load_corpus, load_manifest  # noqa: E402
from law_rag.models import Query  # noqa: E402
from law_rag.reasoning import RuleEngine  # noqa: E402
from law_rag.retrieval import Retriever  # noqa: E402
from law_rag.router import RuleRouter  # noqa: E402

OUT = ROOT / "docs" / "demo" / "index.html"
CORPUS_VERSION = "CORPUS-20260802"

CASES = [
    ("명시 조문", "학생인건비는 어떻게 지급하나요?"),
    ("낡은 조문(질문 인용)", "시행령 제64조 제2항에 따른 3책5공 적용제외 대상은?"),
    ("답 불변", "비영리기관이 연구개발비를 이관할 때 간접비 적용 방법은?"),
    ("확인 요청", "연구책임자와 연구개발기관 중 어떤 것을 변경해야 하나요?"),
    ("범위 밖", "우주 정거장 발사 허가 절차는?"),
]
BADGE = {"ANSWER": "ANSWER", "CONFIRM": "CONFIRM(확인)", "ABSTAIN": "ABSTAIN(보류)", "CORRECT": "CORRECT(전제 교정)"}


def _run():
    # §4 통일: 시연도 law_rag.decide.decide_for_query(평가·CLI와 같은 경로)를 쓴다.
    from law_rag.decide import decide_for_query
    corpus = load_corpus(ROOT / "tests" / "fixtures", split=True)
    edges = build_edges(corpus)
    idx = {c.chunk_id: c for c in corpus}
    out = []
    for label, q in CASES:
        query = RuleRouter().route(Query(text=q))
        d = decide_for_query(query, corpus, edges, idx, "B4")
        out.append({"label": label, "q": q, "decision": d["decision"], "reason": d["reason"],
                    "path": " → ".join(d["path"]), "evidence": d["evidence"] or "(없음)",
                    "relevance": d["relevance"]})
    return out


def _html(cards):
    run_id = str(uuid.uuid4())
    rows = []
    for c in cards:
        rows.append(f"""  <div class="card">
    <div class="q">{html.escape(c['label'])} — "{html.escape(c['q'])}"</div>
    <div class="badges"><span class="{c['decision']}">결정: {BADGE.get(c['decision'], c['decision'])}</span>
      <span>관련도 {c['relevance']}</span><span>최상위 근거: {html.escape(c['evidence'])}</span></div>
    <div class="path">결정 경로: {html.escape(c['path'])}  ·  사유: {html.escape(c['reason'])}</div>
  </div>""")
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><title>TAP-LegalRAG 시연(자동 생성)</title>
<style>
 body{{font-family:system-ui,"Malgun Gothic",sans-serif;max-width:860px;margin:24px auto;padding:0 16px;color:#1b1b1b;line-height:1.5}}
 .sub{{color:#666;font-size:13px}} .card{{border:1px solid #ddd;border-radius:10px;padding:14px 16px;margin:14px 0}}
 .q{{font-weight:600;margin-bottom:8px}} .badges span{{display:inline-block;font-size:12px;border-radius:12px;padding:2px 9px;margin:2px 4px 2px 0;border:1px solid #ccc}}
 .ANSWER{{background:#e8f5e9;border-color:#a5d6a7}} .CONFIRM{{background:#fff8e1;border-color:#ffe082}}
 .ABSTAIN{{background:#fce4ec;border-color:#f48fb1}} .CORRECT{{background:#e3f2fd;border-color:#90caf9}}
 .path{{font-size:13px;color:#333;background:#fafafa;border-left:3px solid #bbb;padding:6px 10px;margin-top:8px;white-space:pre-wrap}}
 footer{{color:#888;font-size:12px;margin-top:24px;border-top:1px solid #eee;padding-top:10px}}
</style></head><body>
<h1>TAP-LegalRAG — 판정 경로 시연</h1>
<p class="sub">이 페이지는 <b>scripts/build_demo.py가 실제 파이프라인(config B4)을 돌려 자동 생성</b>한다(손으로 쓴 문구 없음).
설정 B4, 판본 {CORPUS_VERSION}, run_id {run_id[:8]}. 법적 판단을 대체하지 않으며 사람 검토가 전제다.</p>
{chr(10).join(rows)}
<footer>생성: {dt.date.today()} · 데이터 출처: 과학기술정보통신부 매뉴얼 등 공개 자료·국가법령정보 · 결과·한계: docs/RESULTS.md, docs/LIMITATIONS.md</footer>
</body></html>
"""


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    cards = _run()
    OUT.write_text(_html(cards), encoding="utf-8")
    for c in cards:
        print(f"  [{c['decision']:8}] {c['label']}: {c['reason']}")
    print(f"생성: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
