"""
law_rag.knowledge — 지식 계층: 관계 그래프 저장(EdgeStore) + 조립(Assembler).

책임 범위:
    - EdgeStore: 관계 그래프(위임·인용·예외) 저장·조회. V1은 인메모리,
      이후 SQLite/Postgres/Neo4j로 교체(인터페이스 유지).
    - Assembler: 검색된 원자 청크에서 그래프를 1홉 따라가 근거 번들 구성.
    - loader.py: 법령 데이터 로딩 + 엣지 빌더.
    (스키마는 models/, 메타데이터는 LawChunk 필드로 이미 분리되어 있으므로
     별도 폴더로 쪼개지 않는다 — 빈 폴더를 만드는 과잉을 피한다.)

왜 '검색 후 조립'인가 (통합설계 기둥 1):
    저장은 조·항으로 원자적. 사용자가 필요한 '법+시행령+고시' 묶음은
    여기서 실시간으로 만든다. 개정에 자동으로 따라오고 근거가 정확하다.

왜 1홉으로 제한하는가:
    무한 확장하면 관련 없는 조문까지 딸려와 노이즈가 되고 느려진다.
    1홉(직접 연결된 것만)이 정확도·속도의 균형점. 필요 시 hops 인자로 조정.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from law_rag.models import Edge, EdgeType, EvidenceBundle, LawChunk, ScoredChunk


# 1순위 근거 하나당 딸려올 수 있는 관련 조문 상한. 넘으면 노이즈·지연이 늘어난다.
DEFAULT_MAX_RELATED_PER_PRIMARY = 3  # TODO(golden): 골든셋으로 조정


class EdgeStore(ABC):
    """관계 그래프 저장소 인터페이스. V1은 인메모리, 이후 SQLite/Postgres/Neo4j로 교체.
    통합설계 5장: 처음엔 edges 테이블(관계형)로 충분하다."""

    @abstractmethod
    def outgoing(self, chunk_id: str, edge_types: list[EdgeType]) -> list[Edge]:
        """chunk_id에서 나가는 엣지들(지정한 타입만)."""
        ...

    @abstractmethod
    def incoming(self, chunk_id: str, edge_types: list[EdgeType]) -> list[Edge]:
        """chunk_id로 들어오는 엣지들(지정한 타입만). 양방향 조립(P0-3)의 핵심:
        상위법 조문에서 그것을 구체화한 하위 규정(하위→상위 방향 엣지)을 찾는다."""
        ...


class InMemoryEdgeStore(EdgeStore):
    """V1 참조구현: 엣지 리스트를 메모리에 들고 조회. 테스트·프로토타입용.

    실제 교체 시 이 클래스만 SQL 구현으로 바꾸면 되고,
    Assembler는 EdgeStore 인터페이스만 알므로 안 바뀐다."""

    def __init__(self, edges: list[Edge]):
        self._by_from: dict[str, list[Edge]] = {}
        self._by_to: dict[str, list[Edge]] = {}
        for e in edges:
            self._by_from.setdefault(e.from_chunk_id, []).append(e)
            self._by_to.setdefault(e.to_ref, []).append(e)

    def outgoing(self, chunk_id: str, edge_types: list[EdgeType]) -> list[Edge]:
        return [e for e in self._by_from.get(chunk_id, []) if e.edge_type in edge_types]

    def incoming(self, chunk_id: str, edge_types: list[EdgeType]) -> list[Edge]:
        return [e for e in self._by_to.get(chunk_id, []) if e.edge_type in edge_types]


class Assembler:
    """검색 결과 → 근거 번들. 관계 그래프를 '양방향 1홉' 확장한다(P0-3).

    왜 양방향인가:
        하위규정→상위법 엣지만 따라가면(기존), 실무 답의 핵심인
        '법 조문 → 그것을 구체화한 시행령·고시'(상위→하위) 방향이 막힌다.
        그래서 outgoing(상위 근거)과 incoming(하위 구체화/예외)을 함께 본다.

    확장 상한(노이즈 방지):
        1순위 근거 하나당 우선순위대로 최대 N개만 붙인다.
        우선순위 ① 이 조문에 대한 예외(incoming exception_to)
                ② 이 조문을 구체화한 하위 규정(incoming delegates_to/refers_to, 더 하위 위계)
                ③ 이 조문의 상위 근거(outgoing delegates_to)
                ④ 기타 인용(outgoing refers_to)
        같은 우선순위 안에서는 (위계, chunk_id) 순으로 결정론적 정렬.
    """

    def __init__(self, edge_store: EdgeStore, corpus_index: dict[str, LawChunk]):
        """corpus_index: chunk_id → LawChunk 조회용 딕셔너리."""
        self.edges = edge_store
        self.index = corpus_index

    def assemble(
        self,
        primary: list[ScoredChunk],
        hops: int = 1,
        max_related_per_primary: int = DEFAULT_MAX_RELATED_PER_PRIMARY,
    ) -> EvidenceBundle:
        """hops는 호환용(현재 1홉 우선순위 확장 고정). related는 chunk_id로 유일화."""
        primary_ids = {sc.chunk.chunk_id for sc in primary}
        related: dict[str, LawChunk] = {}
        links: list[tuple[str, str, str]] = []

        for sc in primary:
            src = sc.chunk
            picked = 0
            for _prio, _lvl, tgt, rel in sorted(
                self._candidates(src), key=lambda x: (x[0], x[1], x[2].chunk_id)
            ):
                if picked >= max_related_per_primary:
                    break
                tid = tgt.chunk_id
                if tid in primary_ids or tgt.is_form or tgt.is_split_parent:
                    continue
                related.setdefault(tid, tgt)
                links.append((src.chunk_id, tid, rel))
                picked += 1

        return EvidenceBundle(
            primary=primary, related=list(related.values()), links=links
        )

    def _candidates(self, src: LawChunk) -> list[tuple[int, int, LawChunk, str]]:
        """src에 붙일 후보들을 (우선순위, 위계, 청크, 관계명)으로 모은다."""
        out: list[tuple[int, int, LawChunk, str]] = []

        # ① 이 조문에 대한 예외 (incoming exception_to)
        for e in self.edges.incoming(src.chunk_id, [EdgeType.exception_to]):
            t = self.index.get(e.from_chunk_id)
            if t:
                out.append((1, t.hierarchy_level, t, "예외(exception_to)"))

        # ② 이 조문을 구체화한 하위 규정 (incoming 위임/인용, 출발이 더 하위 위계)
        for e in self.edges.incoming(src.chunk_id, [EdgeType.delegates_to, EdgeType.refers_to]):
            t = self.index.get(e.from_chunk_id)
            if t and t.hierarchy_level > src.hierarchy_level:
                out.append((2, t.hierarchy_level, t, f"하위규정({e.edge_type.value})"))

        # ③ 이 조문의 상위 근거 (outgoing delegates_to)
        for e in self.edges.outgoing(src.chunk_id, [EdgeType.delegates_to]):
            t = self._resolve(e.to_ref)
            if t:
                out.append((3, t.hierarchy_level, t, "상위근거(delegates_to)"))

        # ④ 기타 인용 (outgoing refers_to)
        for e in self.edges.outgoing(src.chunk_id, [EdgeType.refers_to]):
            t = self._resolve(e.to_ref)
            if t:
                out.append((4, t.hierarchy_level, t, "인용(refers_to)"))

        return out

    def _resolve(self, to_ref: str) -> LawChunk | None:
        """엣지 도착 참조(to_ref)를 실제 청크로 해석. chunk_id만 매칭(엣지 빌더가 미리 해석)."""
        return self.index.get(to_ref)
