"""
scripts/eval_golden_v3.py — 골든셋 v3 평가 하니스 (A-1).

REMOVE·HOLD·정답없음 제외. 지표: R@5(ANY/ALL), MRR, 근거완결성(검색만 vs 관계조립 후),
유형별·법령패밀리별. 기준일 → Query.as_of. 항 없는 주소는 그 조의 모든 청크, 별표는 분할 자식 포함.

사용: python scripts/eval_golden_v3.py [--guide]   (--guide면 법령+해설 2조건 비교)
출력: 표 + 내부 설계 노트
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from law_rag.knowledge.loader import build_edges, load_corpus  # noqa: E402
from law_rag.knowledge import Assembler, InMemoryEdgeStore  # noqa: E402
from law_rag.models import Query  # noqa: E402
from law_rag.retrieval import Retriever  # noqa: E402
from law_rag.router import RuleRouter  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
GOLDEN = ROOT / "tests" / "golden" / "golden_set_v3_draft_manual.csv"
OUT = ROOT / "docs" / "내부 설계 노트" / "eval_v3_baseline.json"
TOP_K = 5


def _addr_index(corpus):
    art_ids: dict[tuple, list] = defaultdict(list)
    para_id: dict[tuple, str] = {}
    for c in corpus:
        if c.is_split_parent:
            continue  # 분할 원본은 검색 대상 아님 → 자식만 인정
        art_ids[(c.law_id, c.article_no)].append(c.chunk_id)
        para_id[(c.law_id, c.article_no, c.paragraph_no)] = c.chunk_id
    return art_ids, para_id


def _parse_addr(addr: str):
    lid, rest = addr.split(":", 1)
    segs = rest.split(":")
    tag = segs[0]
    art = tag[1:] if tag.startswith("J") else ("별표" + tag[1:] if tag.startswith("B") else tag)
    para = segs[1][1:] if len(segs) > 1 and segs[1].startswith("H") else None
    return lid, art, para


def _addr_ids(addr: str, art_ids, para_id) -> set[str]:
    lid, art, para = _parse_addr(addr)
    if para is not None:
        pid = para_id.get((lid, art, para))
        if pid:
            return {pid}
    return set(art_ids.get((lid, art), []))  # 항 없음 → 조 전체(별표면 자식 포함)


def _split_addrs(cell: str) -> list[str]:
    cell = (cell or "").strip()
    if not cell or "없음" in cell:
        return []
    return [a.strip() for a in cell.replace(";", ",").split(",") if a.strip() and ":" in a]


def evaluate(rows, corpus, edges, *, use_guide=False):
    router, retriever = RuleRouter(), Retriever()
    idx = {c.chunk_id: c for c in corpus}
    assembler = Assembler(InMemoryEdgeStore(edges), idx)
    art_ids, para_id = _addr_index(corpus)
    import datetime as dt

    agg = defaultdict(lambda: {"n": 0, "any": 0, "all": 0, "mrr": 0.0, "comp_s": 0.0, "comp_a": 0.0})

    for r in rows:
        req = _split_addrs(r["정답_필수_주소"])
        sup = _split_addrs(r["정답_보조_주소"])
        if not req:
            continue
        as_of = None
        if (r.get("기준일") or "").strip():
            try:
                as_of = dt.date.fromisoformat(r["기준일"].strip())
            except ValueError:
                as_of = None
        q = router.route(Query(text=r["질문"], as_of=as_of))
        # 한 번 검색(상위 10)에서: R@5는 상위5, 완결성은 상위6(파이프라인 기본), MRR은 상위10.
        scored = retriever.retrieve_with_signal(q, corpus, final_k=10)[0]
        ids = [s.chunk.chunk_id for s in scored]
        top5_set = set(ids[:TOP_K])
        cand_set = set(ids[:6])  # 완결성 기준(final_k=6)

        req_sets = [_addr_ids(a, art_ids, para_id) for a in req]
        hit_any = any(s & top5_set for s in req_sets)
        hit_all = all(s & top5_set for s in req_sets) and req_sets
        # MRR@10: 필수 중 아무거나 처음 맞은 순위(상위 10까지)
        rank = next((i for i, cid in enumerate(ids[:10], 1)
                     if any(cid in s for s in req_sets)), 0)

        all_addrs = req + sup
        all_sets = [_addr_ids(a, art_ids, para_id) for a in all_addrs]
        comp_s = sum(1 for s in all_sets if s & cand_set) / len(all_sets)
        bundle = assembler.assemble(scored[:6], hops=1)
        ev = cand_set | {c.chunk_id for c in bundle.related}
        comp_a = sum(1 for s in all_sets if s & ev) / len(all_sets)

        bench_tags = [t for t in (r.get("벤치마크") or "").split(";") if t.strip()]
        for key in ["전체", r["문항유형"], *("@" + t for t in bench_tags)]:
            a = agg[key]
            a["n"] += 1
            a["any"] += int(bool(hit_any))
            a["all"] += int(bool(hit_all))
            a["mrr"] += (1.0 / rank) if rank else 0.0
            a["comp_s"] += comp_s
            a["comp_a"] += comp_a
    return agg


def _fmt(agg):
    out = {}
    for k, a in agg.items():
        n = a["n"] or 1
        out[k] = {"n": a["n"], "R@5_ANY": round(a["any"] / n, 3), "R@5_ALL": round(a["all"] / n, 3),
                  "MRR@10": round(a["mrr"] / n, 3), "완결성_검색만": round(a["comp_s"] / n, 3),
                  "완결성_조립후": round(a["comp_a"] / n, 3)}
    return out


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "MISSING"


def run_manifest(eval_spec_version: str = "v1") -> dict:
    """감사·재현용 최소 RunManifest(EVAL_SPEC v1 §6)."""
    import hashlib
    import subprocess
    import uuid
    from law_rag.retrieval import Retriever
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip() or "UNKNOWN"
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                    capture_output=True, text=True).stdout.strip())
    except Exception:
        commit, dirty = "UNKNOWN", True
    r = Retriever()
    rc = f"{type(r.keyword).__name__}|{type(r.dense).__name__}|{type(r.reranker).__name__}"
    return {
        "run_id": str(uuid.uuid4()),
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "golden_sha256": _sha256(GOLDEN),
        "corpus_manifest_sha256": _sha256(ROOT / "data" / "manifest.json"),
        "code_commit": commit, "dirty": dirty,
        "eval_spec_version": eval_spec_version,
        "retriever_config_hash": hashlib.sha256(rc.encode()).hexdigest()[:16],
        "llm": None, "embedding": None, "seed": 0,
    }


def main() -> int:
    import argparse
    if hasattr(sys.stdout, "buffer"):  # 실행 시에만(import 시 pytest 캡처 보호)
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="결과 JSON 저장 경로(미지정 시 저장 안 함 — 문서 덮어쓰기 방지)")
    args = ap.parse_args()

    rows = list(csv.DictReader(GOLDEN.open(encoding="utf-8-sig")))
    active = [r for r in rows if r["최종판정(1차)"] not in ("REMOVE", "HOLD")]
    corpus = load_corpus(FIXTURES, split=True)
    res = _fmt(evaluate(active, corpus, build_edges(corpus)))
    manifest = run_manifest()

    order = ["전체", "A", "B", "C", "D", "E", "G", "F3",
             "@CORE", "@NEGATIVE", "@TEMPORAL", "@DRIFT", "@APPLICABILITY"]
    print(f"{'집계':14}{'n':>4}{'R@5_ANY':>9}{'R@5_ALL':>9}{'MRR@10':>8}{'완결S':>8}{'완결A':>8}")
    for k in order:
        if k in res:
            v = res[k]
            print(f"{k:14}{v['n']:>4}{v['R@5_ANY']:>9}{v['R@5_ALL']:>9}{v['MRR@10']:>8}"
                  f"{v['완결성_검색만']:>8}{v['완결성_조립후']:>8}")
    print(f"\nrun_id={manifest['run_id'][:8]} commit={manifest['code_commit'][:8]} "
          f"dirty={manifest['dirty']} eval_spec={manifest['eval_spec_version']}")
    if args.out:
        Path(args.out).write_text(
            json.dumps({"manifest": manifest, "metrics": res}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"저장: {args.out}")
    else:
        print("(저장 안 함 — --out 지정 시에만 기록)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
