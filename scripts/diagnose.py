"""
scripts/diagnose.py — 골든셋 없이 돌리는 '자가진단(구조 무결성) 리포트'.

왜 필요한가:
    골든셋(사람이 만든 정답지)은 '실제 질문 정확도'를 재는 정밀 건강검진이다.
    이 스크립트는 '화재경보기'다. 정답지 없이도 명백히 틀린 것을 잡아낸다.
    예) 시행령 본문의 "법 제11조"가 시행령 제11조로 연결돼 있으면 정답지 없이도 오류다.

측정 항목 (P0_REPAIR_SPEC.md의 완료 기준과 1:1 대응):
    M1  상위법 참조('법/영/「법령명」 제N조') 오연결·누락
    M2  항 지정 참조('제N조제M항')의 항 정확도
    M3  미해결 참조(코퍼스 밖 법령은 '외부'로 분리 집계)
    M4  하향 조립 프로브: 혁신법 §11② → 시행령 §14 포함 여부
    M5  주제 필터 프로브: 주제 태그가 정답 후보를 잘라내는가
    M6  기준일 프로브: 과거 기준일에서 유효 청크 수, '판본 미보유' 명시 여부
    M7  맥락 없는 청크(조 제목도 context_prefix도 없는 청크)
    M8  주제 미분류 청크(정의일반 단독)
    M9  1순위 근거 순서 프로브: 규칙엔진이 검색 1위를 밀어내는가
    M10 본문 첫 문장 known-item Recall@5 (자동 생성; 제목 효과 배제로 부풀림 없음)
    T   질의당 평균 응답시간(프로브 질문)

실행 (law_rag_kit 폴더에서):
    python scripts/diagnose.py                      # 리포트 출력
    python scripts/diagnose.py --json out.json      # 결과 JSON 저장(전후 비교용)
    python scripts/diagnose.py --strict             # 하나라도 FAIL이면 종료코드 1

주의:
    M10은 '본문 첫 문장으로 그 조문을 찾는' 자동 시험이다. 실제 담당자 질문보다 쉽다.
    (구버전은 조문 제목 질의였으나 context_prefix에 제목이 들어가 부풀려져 폐기했다.)
    점수가 오르면 '고장이 줄었다'는 뜻이지 '실제 정확도가 그만큼'이라는 뜻이 아니다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from law_rag.knowledge.loader import build_edges, load_corpus  # noqa: E402
from law_rag.models import Query, ScoredChunk  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"

# 코퍼스 안 법령 ID (manifest 도입 후에는 manifest에서 읽도록 바꿔도 된다)
LAW_ID = {"혁신법": "013774", "시행령": "013933", "시행규칙": "013980", "사용기준": "75386", "성과평가법": "010090"}
# 하위 규정에서 "법" / "영"이 가리키는 법령
PARENT_OF = {"013933": "013774", "013980": "013774", "75386": "013774"}
DECREE_OF = {"013980": "013933", "75386": "013933"}  # "영 제N조" → 시행령

_RE_QUAL = re.compile(r"(?<![가-힣])(법|영)\s*제(\d+(?:의\d+)?)조(?:\s*제(\d+)항)?")
_RE_NAMED = re.compile(r"「([^」]+)」\s*제(\d+(?:의\d+)?)조(?:\s*제(\d+)항)?")
_RE_ANY = re.compile(r"제(\d+(?:의\d+)?)조(?:\s*제(\d+)항)?")

PROBE_QUESTIONS = [
    "협약의 중요 사항을 변경하려면 누구와 협의해야 하나요?",
    "연구책임자 변경은 협약변경 대상인가요?",
    "정산은 각 단계 종료 후 언제까지 해야 하나요?",
    "연구자 한 명이 동시에 수행 가능한 과제 수는?",
    "연구비 사용계획 변경에 사전 승인이 필요한 경우는?",
    "비영리기관의 간접비 고시비율은?",
    "학생인건비는 어떻게 지급하나요?",
    "연구장비를 구입하려면 심의를 받아야 하나요?",
    "제재부가금은 어떻게 산정하나요?",
    "기술료는 누가 징수하나요?",
]


def _first_sentence(body: str) -> str:
    """본문에서 머리말(제N조(제목)/[별표N])·항번호를 떼고 첫 문장만 추출(M10 질의용)."""
    t = re.sub(r"^제\d+(?:의\d+)?조\([^)]*\)\s*", "", body)
    t = re.sub(r"^\[[^\]]+\]\s*", "", t)
    t = re.sub(r"^[①-⑮]\s*", "", t).strip()
    parts = re.split(r"(?<=다[.])\s|(?<=[.])\s", t, maxsplit=1)
    return (parts[0] if parts else t).strip()


def _name_to_id(corpus) -> dict[str, str]:
    m = {}
    for c in corpus:
        for n in filter(None, [c.law_name, c.law_short_name]):
            m[n] = c.law_id
    return m


def _targets(edges_from, idx):
    out = []
    for e in edges_from:
        t = idx.get(e.to_ref)
        if t is not None:
            out.append(t)
    return out


def measure(corpus, edges, pipeline_factory):
    idx = {c.chunk_id: c for c in corpus}
    name2id = _name_to_id(corpus)
    by_from: dict[str, list] = {}
    for e in edges:
        by_from.setdefault(e.from_chunk_id, []).append(e)

    R: dict[str, dict] = {}

    # ── M1/M2: 상위법·법령명 참조 ───────────────────────────────
    mentions = covered = wrong_law = 0
    para_total = para_wrong = 0
    wrong_examples: list[str] = []
    external = 0
    # 법령(패밀리)별 집계: law_id → [mentions, wrong_law, para_total, para_wrong]
    by_law: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for c in corpus:
        # 서식·분할부모·별표(표 데이터)는 규범 문장이 아니라 엣지 추출 대상이 아니다
        # (build_edges와 측정 범위를 맞춘다). 조문 참조 품질만 측정한다.
        if c.is_form or c.is_split_parent or c.article_no.startswith("별표"):
            continue
        body = c.body_text
        tgts = _targets(by_from.get(c.chunk_id, []), idx)
        qualified_spans = []
        found = []  # (expected_law_id, art, para)
        for m in _RE_QUAL.finditer(body):
            kind, art, para = m.group(1), m.group(2), m.group(3)
            exp = PARENT_OF.get(c.law_id) if kind == "법" else DECREE_OF.get(c.law_id)
            qualified_spans.append(m.span())
            if exp and exp != c.law_id:
                found.append((exp, art, para))
        for m in _RE_NAMED.finditer(body):
            qualified_spans.append(m.span())
            lid = name2id.get(m.group(1).strip())
            if lid is None:
                external += 1
                continue
            if lid != c.law_id:
                found.append((lid, m.group(2), m.group(3)))

        def _is_qualified(pos: int) -> bool:
            return any(a <= pos < b for a, b in qualified_spans)

        unqualified_arts = {
            m.group(1) for m in _RE_ANY.finditer(body)
            if not _is_qualified(m.start()) and m.group(1) != c.article_no
        }
        for exp, art, para in found:
            mentions += 1
            by_law[c.law_id][0] += 1
            hit = [t for t in tgts if t.law_id == exp and t.article_no == art]
            if hit:
                covered += 1
            if art not in unqualified_arts and any(
                t.law_id == c.law_id and t.article_no == art for t in tgts
            ):
                wrong_law += 1
                by_law[c.law_id][1] += 1
                if len(wrong_examples) < 3:
                    wrong_examples.append(f"{c.chunk_id}: '{exp}' 제{art}조 → 자기 법령 제{art}조로 연결")
            if para and hit:
                para_total += 1
                by_law[c.law_id][2] += 1
                if not any(t.paragraph_no in (para, None) for t in hit):
                    para_wrong += 1
                    by_law[c.law_id][3] += 1
        # 같은 법령 안의 '제N조제M항' 참조도 항 정확도 측정
        for m in _RE_ANY.finditer(body):
            if _is_qualified(m.start()) or not m.group(2) or m.group(1) == c.article_no:
                continue
            art, para = m.group(1), m.group(2)
            hit = [t for t in tgts if t.law_id == c.law_id and t.article_no == art]
            if hit:
                para_total += 1
                if not any(t.paragraph_no in (para, None) for t in hit):
                    para_wrong += 1

    R["M1_parent_ref_wrong_law"] = {
        "value": round(wrong_law / mentions, 3) if mentions else 0.0,
        "detail": f"{wrong_law}/{mentions}건 오연결, 올바른 법령 연결 {covered}/{mentions}",
        "examples": wrong_examples, "target": "< 0.05", "ok": mentions > 0 and wrong_law / mentions < 0.05,
    }
    R["M1b_parent_ref_coverage"] = {
        "value": round(covered / mentions, 3) if mentions else 0.0,
        "detail": f"상위법·법령명 참조 중 올바른 법령·조로 연결된 비율 ({covered}/{mentions})",
        "target": ">= 0.90", "ok": mentions > 0 and covered / mentions >= 0.90,
    }
    R["M2_paragraph_wrong"] = {
        "value": round(para_wrong / para_total, 3) if para_total else 0.0,
        "detail": f"항 지정 참조 {para_total}건 중 다른 항으로 연결 {para_wrong}건",
        "target": "< 0.03", "ok": para_total > 0 and para_wrong / para_total < 0.03,
    }

    # ── M3: 미해결 참조 (외부 법령 EXT: 는 '정상 분리'로 집계 제외) ──
    ext_edges = [e for e in edges if e.to_ref.startswith("EXT:")]
    unresolved = [e for e in edges if e.to_ref not in idx and not e.to_ref.startswith("EXT:")]
    internal = max(len(edges) - len(ext_edges), 1)
    R["M3_unresolved_edges"] = {
        "value": len(unresolved),
        "detail": f"내부 엣지 {internal}개 중 미해결 {len(unresolved)}개 "
                  f"({Counter(e.edge_type.value for e in unresolved)}), "
                  f"외부 법령(EXT) {len(ext_edges)}개 분리, 외부 법령명 언급 {external}건",
        "target": "< 5% (외부 법령 참조는 EXT로 분리)",
        "ok": len(unresolved) / internal < 0.05,
    }

    # ── M1_M2_by_family: 법령 패밀리별 M1(오연결)·M2(항오류) 분해 ──
    name_by_id = {}
    for c in corpus:
        name_by_id.setdefault(c.law_id, c.law_short_name or c.law_name)
    fam = {name_by_id.get(lid, lid): f"오연결 {v[1]}/{v[0]}, 항오류 {v[3]}/{v[2]}"
           for lid, v in by_law.items()}
    R["M1_M2_by_family"] = {
        "value": len(fam), "detail": " | ".join(f"{k}: {d}" for k, d in fam.items()),
        "target": "참고(법령별 분해)", "ok": True,
    }

    # ── M11: 원천 무결성(fixture SHA-256 = manifest) ─────────────
    from law_rag.knowledge.loader import load_manifest  # noqa: E402
    man = load_manifest()
    mism = []
    for e in man.get("laws", []):
        fp = FIXTURES / e["fixture"]
        if not fp.exists():
            mism.append(f"{e['fixture']}(없음)")
            continue
        actual = hashlib.sha256(fp.read_bytes()).hexdigest()
        if actual != e.get("sha256"):
            mism.append(f"{e['fixture']}(지문불일치)")
    R["M11_source_integrity"] = {
        "value": f"{len(man.get('laws', []))-len(mism)}/{len(man.get('laws', []))}",
        "detail": ("모든 fixture 지문이 manifest와 일치" if not mism else f"불일치: {mism}"),
        "target": "manifest sha256 = 실제 파일", "ok": bool(man.get("laws")) and not mism,
    }

    # ── M12: 판본 정합(시행일 ≤ 수집일, 공포본 없음) ─────────────
    future = []
    for e in man.get("laws", []):
        enf = e.get("enforce_date"); fetched = e.get("fetched_at")
        if enf and fetched and str(enf) > str(fetched):
            future.append(f"{e.get('law_short_name') or e['law_name']}(시행 {enf} > 수집 {fetched})")
    R["M12_version_consistency"] = {
        "value": f"{len(man.get('laws', []))-len(future)}/{len(man.get('laws', []))}",
        "detail": ("모든 fixture 시행일 ≤ 수집일" if not future
                   else f"시행 전 공포본 의심(P1B-2 재수집 필요): {future}"),
        "target": "시행일 ≤ 수집일(공포본 없음)", "ok": bool(man.get("laws")) and not future,
    }

    # ── M13: 판본 간 참조 정합성(A-4, WARN) ─────────────────────
    # 다른 법령의 '항'을 가리키는 참조에서 "제X조제Y항에 따른 ○○"의 ○○가 대상 항 본문에 없으면 경고.
    m13_warn = []
    _re_follow = {}
    for e in edges:
        if e.edge_type.value not in ("refers_to", "delegates_to"):
            continue
        src, tgt = idx.get(e.from_chunk_id), idx.get(e.to_ref)
        if not src or not tgt or tgt.law_id == src.law_id or not tgt.paragraph_no:
            continue
        pat = _re_follow.setdefault(
            (tgt.article_no, tgt.paragraph_no),
            re.compile(rf"제{tgt.article_no}조\s*제{tgt.paragraph_no}항에\s*따[른라]\s*([가-힣]{{2,6}})"),
        )
        m = pat.search(src.body_text)
        if m:
            # 조사 제거 후 '어간'으로 비교(예: '예고를'→'예고'). 문법형 불일치 오탐 축소.
            noun = re.sub(r"(으로|로서|로써|에서|에게|를|을|이|가|은|는|의|에|로|와|과|및)$", "", m.group(1))
            if len(noun) >= 2 and noun not in tgt.body_text:
                m13_warn.append(f"{src.chunk_id}: '제{tgt.article_no}조제{tgt.paragraph_no}항에 따른 {noun}' "
                                f"→ 대상 {tgt.law_short_name or tgt.law_id} {tgt.article_no}조{tgt.paragraph_no}항 본문에 '{noun}' 없음")
    R["M13_cross_version_refs"] = {
        "value": len(m13_warn),
        "detail": (f"판본 간 참조 불일치 의심 {len(m13_warn)}건. " + (m13_warn[0] if m13_warn else "없음")),
        "target": "WARN(재수집 후 재평가). 예: 시행령 26조 → 법 13조7항 '정산'",
        "ok": True,  # 지금은 경고만(FAIL 아님)
    }

    pipe = pipeline_factory(corpus, edges)

    # ── M4: 하향 조립 ───────────────────────────────────────────
    try:
        src = idx["LAW-013774-J11-H2"]
        b = pipe.assembler.assemble([ScoredChunk(chunk=src, score=1.0)], hops=1)
        rel = [c.chunk_id for c in b.related]
        ok = any(r.startswith("LAW-013933-J14") for r in rel)
        R["M4_downward_assembly"] = {"value": ok, "detail": f"혁신법 §11② 조립 결과: {rel[:6]}",
                                     "target": "시행령 §14 포함", "ok": ok}
    except Exception as ex:  # noqa: BLE001
        R["M4_downward_assembly"] = {"value": "ERROR", "detail": repr(ex), "target": "시행령 §14 포함", "ok": False}

    # ── M5: 주제 필터 ───────────────────────────────────────────
    try:
        q = pipe.router.route(Query(text="연구책임자 변경은 협약변경 대상인가요?"))
        scored, _ = pipe.retriever.retrieve_with_signal(q, corpus, first_stage_k=30, final_k=30)
        ids = [s.chunk.chunk_id for s in scored]
        ok = "LAW-013774-J11-H2" in ids
        R["M5_topic_filter_probe"] = {"value": ok, "detail": f"주제={getattr(q.topic, 'value', None)}, 후보 {len(ids)}개, 혁신법 §11② 포함={ok}",
                                      "target": "주제 태그가 정답 후보를 잘라내지 않음", "ok": ok}
    except Exception as ex:  # noqa: BLE001
        R["M5_topic_filter_probe"] = {"value": "ERROR", "detail": repr(ex), "target": "-", "ok": False}

    # ── M6: 기준일 ──────────────────────────────────────────────
    live = [c for c in corpus if not c.is_form and not c.is_split_parent]
    counts = {str(y): sum(c.is_valid_at(dt.date(y, 1, 1)) for c in live) for y in (2022, 2023, 2024, 2025)}
    try:
        a = pipe.answer("연구책임자 변경은 협약변경 대상인가요?", as_of=dt.date(2023, 5, 1))
        text = " ".join([a.conclusion, a.body, *a.audit_log])
        explicit = "판본" in text
    except Exception as ex:  # noqa: BLE001
        explicit, text = False, repr(ex)
    R["M6_asof_coverage"] = {
        "value": counts, "detail": f"2023-05-01 기준 질의에 '판본 미보유' 명시={explicit}",
        "target": "보유 판본 밖 기준일이면 '판본 미보유'를 명시(근거 없음과 구분)", "ok": explicit,
    }

    # ── M7/M8: 맥락·주제 ────────────────────────────────────────
    no_ctx = [c for c in live if c.article_title and c.article_title not in c.body_text
              and not (c.context_prefix and c.article_title in c.context_prefix)]
    R["M7_context_less_chunks"] = {"value": len(no_ctx), "detail": "조 제목이 본문에도 context_prefix에도 없는 청크 수",
                                   "target": "0", "ok": len(no_ctx) == 0}
    untagged = [c for c in live if [t.value for t in c.topic_tags] == ["정의일반"]]
    tag_count = Counter(t.value for c in live for t in c.topic_tags)
    R["M8_untagged_chunks"] = {"value": round(len(untagged) / len(live), 3),
                               "detail": f"정의일반 단독 {len(untagged)}/{len(live)}, 태그 분포 {dict(tag_count)}",
                               "target": "< 0.10 (P1)", "ok": len(untagged) / len(live) < 0.10}

    # ── M9: 1순위 근거 순서 ──────────────────────────────────────
    try:
        q = pipe.router.route(Query(text="정산은 각 단계 종료 후 언제까지 해야 하나요?"))
        scored, _ = pipe.retriever.retrieve_with_signal(q, corpus)
        bundle = pipe.assembler.assemble(scored, hops=1)
        ranked = pipe.rule_engine.rank(bundle)
        first_primary = scored[0].chunk.chunk_id if scored else None
        first_ranked = ranked.ordered[0].chunk_id if ranked.ordered else None
        top_ids = {s.chunk.chunk_id for s in scored if scored and s.score == scored[0].score}
        ok = first_ranked is not None and first_ranked in top_ids  # 동점 1위끼리는 규칙으로 정렬 허용
        R["M9_primary_order_kept"] = {"value": ok, "detail": f"검색 1위={first_primary}, 정렬 후 1위={first_ranked}",
                                      "target": "검색 1위가 정렬 후에도 1위", "ok": ok}
    except Exception as ex:  # noqa: BLE001
        R["M9_primary_order_kept"] = {"value": "ERROR", "detail": repr(ex), "target": "-", "ok": False}

    # ── M10: 본문 첫 문장 known-item (P1-A: 제목 대신 본문으로 → context_prefix로 부풀지 않음) ──
    #   제목 질의는 context_prefix에 제목이 들어가 쉬워진다(부풀림). 본문 첫 문장으로 바꾼다.
    arts: dict[tuple[str, str], tuple[str, set]] = {}
    for c in live:
        if c.article_no.startswith("별표"):
            continue  # 별표는 표라서 '첫 문장' 개념이 약함 → 조문만
        key = (c.law_id, c.article_no)
        if key not in arts:
            s = _first_sentence(c.body_text)
            arts[key] = (s, set())
        arts[key][1].add(c.chunk_id)
    probes = {k: v for k, v in arts.items() if len(v[0]) >= 12}  # 너무 짧은 문장 제외
    hit = 0
    for sentence, ids in probes.values():
        q = pipe.router.route(Query(text=sentence))
        scored, _ = pipe.retriever.retrieve_with_signal(q, corpus, final_k=5)
        hit += any(s.chunk.chunk_id in ids for s in scored)
    r5 = round(hit / len(probes), 3) if probes else 0.0
    R["M10_body_known_item_R@5"] = {
        "value": r5,
        "detail": f"{len(probes)}개 조문 '본문 첫 문장' 질의(자동, 제목 효과 배제). "
                  f"제목 기반 구지표는 context_prefix로 부풀려져 폐기.",
        "target": ">= 0.85 (실엔진 교체 전 기준선 기록용)", "ok": r5 >= 0.85,
    }

    # ── T: 응답시간 ─────────────────────────────────────────────
    t0 = time.perf_counter()
    for qtext in PROBE_QUESTIONS:
        pipe.answer(qtext)
    avg = (time.perf_counter() - t0) / len(PROBE_QUESTIONS)
    R["T_avg_answer_sec"] = {"value": round(avg, 3), "detail": f"프로브 {len(PROBE_QUESTIONS)}문항 평균(LLM 미연결 상태)",
                             "target": "기록용(현재 단계 기준 없음)", "ok": True}
    return R


def main() -> int:
    # Windows 콘솔(CP949) 한글 깨짐 방지. import 시가 아니라 실행 시에만 stdout을 바꾼다
    # (모듈 레벨에서 바꾸면 pytest의 출력 캡처가 깨진다).
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="결과 JSON 저장 경로")
    ap.add_argument("--strict", action="store_true", help="FAIL이 있으면 종료코드 1")
    args = ap.parse_args()

    from law_rag import build_default_pipeline

    corpus = load_corpus(FIXTURES, split=True)
    edges = build_edges(corpus)
    print(f"코퍼스 {len(corpus)}청크 · 엣지 {len(edges)}개 · {dt.datetime.now():%Y-%m-%d %H:%M}\n")
    R = measure(corpus, edges, build_default_pipeline)

    print(f"{'지표':<28} {'판정':<5} {'값':<14} 설명")
    print("-" * 100)
    for k, v in R.items():
        val = v["value"] if not isinstance(v["value"], dict) else json.dumps(v["value"], ensure_ascii=False)
        print(f"{k:<28} {'PASS' if v['ok'] else 'FAIL':<5} {str(val):<14} {v['detail']}")
        print(f"{'':<28} {'':<5} {'':<14} 목표: {v['target']}")
        for ex in v.get("examples", []):
            print(f"{'':<49} 예) {ex}")
    n_fail = sum(1 for v in R.values() if not v["ok"])
    print("-" * 100)
    print(f"FAIL {n_fail}개 / 전체 {len(R)}개")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
             "chunks": len(corpus), "edges": len(edges), "metrics": R},
            ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"JSON 저장: {args.json}")
    return 1 if (args.strict and n_fail) else 0


if __name__ == "__main__":
    raise SystemExit(main())
