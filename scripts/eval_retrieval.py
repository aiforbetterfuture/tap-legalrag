"""
scripts/eval_retrieval.py — 검색 성능 채점 하니스 (베이스라인 측정).

하는 일:
    1) 전체 코퍼스 로드(법률+시행령+시행규칙+고시) → split_corpus 적용
    2) 골든셋 정답 chunk_id가 실제 코퍼스에 존재하는지 사전 검증
    3) 각 문항 검색 실행(as_of 반영) → Recall@1, Recall@5, MRR 산출
    4) 자식(-S{n}) 적중 시 부모 chunk_id도 정답 인정
    5) 진성 음성표본('(없음)') → 최고 관련도 < 임계면 정답(환각 차단선)
    6) 문항별 표 + 주제/유형별 집계 출력

실행:
    PYTHONPATH=src python scripts/eval_retrieval.py
    PYTHONPATH=src python scripts/eval_retrieval.py --k 5 --neg-threshold 0.15

설치 필요 라이브러리 없음(기존 KeywordSearcher 베이스라인).
"""
from __future__ import annotations

import argparse
import io
import sys
from collections import defaultdict
from pathlib import Path

# UTF-8 콘솔(Windows CP949 방지)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from law_rag.knowledge.loader import load_corpus            # noqa: E402
from law_rag.models import LawChunk, Query                  # noqa: E402
from law_rag.retrieval import Retriever                     # noqa: E402
from law_rag.router import RuleRouter                       # noqa: E402
from golden_loader import GoldenRow, load_golden            # noqa: E402

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
TOP_K = 5
NEG_THRESHOLD = 0.15   # 최고 관련도가 이 값 미만이면 '근거 없음'으로 판정


def _parent_map(corpus: list[LawChunk]) -> dict[str, str]:
    """자식 chunk_id → 부모 chunk_id (부모 인정용)."""
    return {c.chunk_id: c.parent_chunk_id for c in corpus if c.parent_chunk_id}


def _hit_rank(
    ranked_ids: list[str], gold_ids: set[str], parent_of: dict[str, str]
) -> int | None:
    """검색 결과에서 정답(자식이면 부모도)이 처음 등장한 순위(1-based). 없으면 None."""
    for i, cid in enumerate(ranked_ids, start=1):
        if cid in gold_ids:
            return i
        # 자식이 맞았는데 정답이 부모로 등록된 경우
        par = parent_of.get(cid)
        if par and par in gold_ids:
            return i
        # 정답이 자식인데 부모가 검색된 경우(역방향)도 인정
    return None


def verify_gold_ids(rows: list[GoldenRow], corpus: list[LawChunk]) -> list[tuple[str, str]]:
    """골든셋 정답 chunk_id가 코퍼스에 존재하는지 검증. (row_id, 누락id) 목록 반환."""
    ids = {c.chunk_id for c in corpus}
    missing: list[tuple[str, str]] = []
    for r in rows:
        for gid in r.gold_chunk_ids:
            if gid not in ids:
                missing.append((r.id, gid))
    return missing


def evaluate(
    rows: list[GoldenRow],
    corpus: list[LawChunk],
    *,
    top_k: int = TOP_K,
    neg_threshold: float = NEG_THRESHOLD,
):
    router = RuleRouter()
    retriever = Retriever()
    parent_of = _parent_map(corpus)

    pos_rows = [r for r in rows if not r.is_negative]
    neg_rows = [r for r in rows if r.is_negative]

    recall1 = recall_k = mrr_sum = 0
    per_item = []
    by_topic = defaultdict(lambda: [0, 0])  # topic → [hit@k, total]

    for r in pos_rows:
        q = router.route(Query(text=r.question, as_of=r.as_of))
        scored, _ = retriever.retrieve_with_signal(q, corpus, final_k=top_k)
        ranked_ids = [s.chunk.chunk_id for s in scored]
        rank = _hit_rank(ranked_ids, set(r.gold_chunk_ids), parent_of)

        hit1 = rank == 1
        hitk = rank is not None and rank <= top_k
        recall1 += int(hit1)
        recall_k += int(hitk)
        mrr_sum += (1.0 / rank) if rank else 0.0
        by_topic[r.expected_topic][0] += int(hitk)
        by_topic[r.expected_topic][1] += 1
        per_item.append((r.id, r.expected_topic, ",".join(r.gold_chunk_ids),
                         rank if rank else "-", "✓" if hitk else "✗"))

    # 진성 음성표본: 최고 관련도 < 임계면 정답
    neg_correct = 0
    for r in neg_rows:
        q = router.route(Query(text=r.question, as_of=r.as_of))
        _, relevance = retriever.retrieve_with_signal(q, corpus, final_k=top_k)
        ok = relevance < neg_threshold
        neg_correct += int(ok)
        per_item.append((r.id, r.expected_topic + "(neg)", "(없음)",
                         f"rel={relevance:.3f}", "✓" if ok else "✗"))

    n_pos = len(pos_rows) or 1
    return {
        "n_pos": len(pos_rows), "n_neg": len(neg_rows),
        "recall@1": recall1 / n_pos,
        "recall@k": recall_k / n_pos,
        "mrr": mrr_sum / n_pos,
        "neg_acc": (neg_correct / len(neg_rows)) if neg_rows else None,
        "per_item": per_item,
        "by_topic": dict(by_topic),
        "top_k": top_k,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=TOP_K)
    ap.add_argument("--neg-threshold", type=float, default=NEG_THRESHOLD)
    args = ap.parse_args()

    print("코퍼스 로드 중...")
    corpus = load_corpus(FIXTURES, split=True)
    print(f"  코퍼스: {len(corpus)}청크 (split 적용)")

    try:
        rows = load_golden()
    except FileNotFoundError as e:
        print(f"\n[골든셋 없음]\n{e}")
        return
    print(f"  골든셋: {len(rows)}문항\n")

    # 사전 검증
    missing = verify_gold_ids(rows, corpus)
    if missing:
        print("⚠ 코퍼스에 없는 정답 chunk_id (오타·미탑재 의심):")
        for rid, gid in missing:
            print(f"    [{rid}] {gid}")
        print()
    else:
        print("✓ 모든 정답 chunk_id가 코퍼스에 존재\n")

    res = evaluate(rows, corpus, top_k=args.k, neg_threshold=args.neg_threshold)

    print("=" * 62)
    print(f"베이스라인 검색 성능 (KeywordSearcher, k={res['top_k']})")
    print("=" * 62)
    print(f"  Recall@1 : {res['recall@1']:.3f}  ({res['n_pos']}문항)")
    print(f"  Recall@{res['top_k']} : {res['recall@k']:.3f}")
    print(f"  MRR      : {res['mrr']:.3f}")
    if res["neg_acc"] is not None:
        print(f"  음성표본 정확도(환각차단): {res['neg_acc']:.3f}  ({res['n_neg']}문항)")

    print("\n── 주제별 Recall@k ──")
    for topic, (hit, tot) in sorted(res["by_topic"].items()):
        print(f"  {topic:10} {hit}/{tot}  ({hit/tot:.2f})")

    print("\n── 문항별 결과 ──")
    print(f"  {'id':10} {'주제':14} {'순위':>8}  적중  정답")
    for rid, topic, gold, rank, hit in res["per_item"]:
        print(f"  {rid:10} {topic:14} {str(rank):>8}   {hit}   {gold}")


if __name__ == "__main__":
    main()
