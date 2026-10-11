"""
law_rag.guide — 매뉴얼 '해설' 검색 계층 (M-3/M-4).

법령 검색(Retrieval)과 '구조적으로 분리'한다. 해설은 법적 효력이 없으므로
법령 Citation으로 둔갑하면 안 된다. 그래서 별도 타입(GuideChunk)·별도 검색기를 둔다.

파이프라인은 use_guide=True일 때만 이 검색기를 호출하고, 결과는 Answer.guide_notes
('해설(법적 효력 없음)' 칸)에만 담는다.
"""
from __future__ import annotations

import json
from pathlib import Path

from law_rag.models import GuideChunk
from law_rag.retrieval import _tokenize


def load_guide_chunks(guide_dir: str | Path) -> list[GuideChunk]:
    """data/guide/*.json → GuideChunk 리스트."""
    base = Path(guide_dir)
    out: list[GuideChunk] = []
    if not base.exists():
        return out
    # 해설 청크 파일만 읽는다. 매핑/제외 목록(golden_guide_map, exclude_test 등)은 건너뜀.
    _NON_CHUNK = {"golden_guide_map.json", "exclude_test.json"}
    for fp in sorted(base.glob("*.json")):
        if fp.name in _NON_CHUNK:
            continue
        data = json.loads(fp.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            continue  # 청크 배열이 아니면 무시(방어선)
        for item in data:
            out.append(GuideChunk.model_validate(item))
    return out


def load_exclude_ids(path: str | Path | None) -> set[str]:
    """정답 유출 방지용 제외 ID 목록(한 줄에 하나). 없으면 빈 집합."""
    if not path:
        return set()
    p = Path(path)
    if not p.exists():
        return set()
    return {
        line.strip() for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


class GuideRetriever:
    """해설 검색기(참조구현: 토큰 겹침). 법령 Retriever와 같은 계약이 아니라 별도다.

    exclude_ids: 골든셋 test 문항의 출처 Q&A를 빼서 '법령만'/'법령+해설' 공정 비교를 가능케 한다(M-4).
    """

    def __init__(self, chunks: list[GuideChunk], exclude_ids: set[str] | None = None):
        ex = exclude_ids or set()
        self.chunks = [c for c in chunks if c.guide_id not in ex]

    @classmethod
    def from_dir(cls, guide_dir: str | Path, exclude_ids: set[str] | None = None) -> "GuideRetriever":
        return cls(load_guide_chunks(guide_dir), exclude_ids)

    def search(self, query: str, top_k: int = 3) -> list[GuideChunk]:
        q = set(_tokenize(query))
        scored: list[tuple[int, GuideChunk]] = []
        for c in self.chunks:
            toks = set(_tokenize(f"{c.section_path or ''} {c.text}"))
            overlap = len(q & toks)
            if overlap > 0:
                scored.append((overlap, c))
        scored.sort(key=lambda x: (-x[0], x[1].guide_id))
        return [c for _, c in scored[:top_k]]
