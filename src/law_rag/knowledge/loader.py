"""
law_rag.knowledge.loader — 샘플/실데이터 로더 & 엣지 빌더.

역할:
    JSON 파일에서 LawChunk 리스트를 읽고, 각 청크의 parent_law_ref/exception_of/cites
    필드에서 관계 엣지를 자동 생성한다.

엣지 빌더가 왜 여기 있나:
    관계 그래프의 연료(edges)를 데이터에서 자동 추출하는 것이 Knowledge Layer의 일.
    V1은 필드 기반(parent_law_ref 등)으로 만들고, SPEC-2에서 cites(refers_to)를 추가했다.

SPEC-2 보강(이번 작업):
    - cites → refers_to 엣지 생성 추가
    - _resolve_ref 확장: 법내 참조(법령명 없는 "제N조"), 별표 참조도 해석 가능
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from law_rag.knowledge.refs import appendix_no, parse_ref_string, scan_refs
from law_rag.models import Edge, EdgeType, LawChunk, LawType


def load_chunks(path: str | Path) -> list[LawChunk]:
    """JSON 파일 → LawChunk 리스트. Pydantic이 스키마 위반을 자동 검증한다."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [LawChunk.model_validate(item) for item in data]


# ── 실데이터 코퍼스 정의 — 단일 출처는 data/manifest.json (P1B-1) ──
#   CORPUS_SPEC = [(fixture 파일명, 파서종류 law|admrul, meta dict), ...]
#   meta: law_id, law_name, law_short_name, law_type, parent_law_name, alias_map
MANIFEST_PATH = Path(__file__).resolve().parents[3] / "data" / "manifest.json"

# manifest가 없을 때의 안전 폴백(현행 5종). manifest가 정상이면 이 값은 덮어쓰인다.
_FALLBACK_SPEC: list[tuple[str, str, dict]] = [
    ("real_law.xml", "law", {"law_id": "013774", "law_name": "국가연구개발혁신법", "law_type": "법률"}),
    ("sihaengnyeong.xml", "law", {"law_id": "013933", "law_name": "국가연구개발혁신법 시행령",
     "law_short_name": "혁신법 시행령", "law_type": "대통령령", "parent_law_name": "국가연구개발혁신법"}),
    ("sihaenggyu.xml", "law", {"law_id": "013980", "law_name": "국가연구개발혁신법 시행규칙",
     "law_short_name": "혁신법 시행규칙", "law_type": "부령", "parent_law_name": "국가연구개발혁신법"}),
    ("yongbi_gosi.xml", "admrul", {"law_id": "75386", "law_name": "국가연구개발사업 연구개발비 사용 기준",
     "law_short_name": "사용기준", "law_type": "고시", "parent_law_name": "국가연구개발혁신법"}),
    ("seongkwa_law.xml", "law", {"law_id": "010090",
     "law_name": "국가연구개발사업 등의 성과평가 및 성과관리에 관한 법률",
     "law_short_name": "성과평가법", "law_type": "법률"}),
]


def load_manifest(path: str | Path | None = None) -> dict:
    """data/manifest.json을 읽어 반환. 없으면 빈 dict."""
    p = Path(path) if path else MANIFEST_PATH
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _spec_from_manifest(doc: dict) -> list[tuple[str, str, dict]]:
    """manifest → CORPUS_SPEC 형식. api_target 'admrul'→'admrul', 그 외→'law'."""
    spec: list[tuple[str, str, dict]] = []
    for e in doc.get("laws", []):
        kind = "admrul" if e.get("api_target") == "admrul" else "law"
        meta = {
            "law_id": e["law_id"], "law_name": e["law_name"],
            "law_short_name": e.get("law_short_name"), "law_type": e.get("law_type"),
            "parent_law_name": e.get("parent_law_name"),
            "alias_map": e.get("alias_map", {}),
        }
        spec.append((e["fixture"], kind, meta))
    return spec


_manifest = load_manifest()
CORPUS_SPEC: list[tuple[str, str, dict]] = (
    _spec_from_manifest(_manifest) if _manifest.get("laws") else _FALLBACK_SPEC
)


def load_corpus(
    fixtures_dir: str | Path,
    *,
    split: bool = True,
    only: list[str] | None = None,
) -> list[LawChunk]:
    """tests/fixtures의 실법령 XML 전체를 파싱해 코퍼스로 합친다.

    - 법률/시행령/시행규칙 → parse_law_xml, 고시 → parse_admrul_xml.
    - split=True면 대형 별표를 split_corpus로 분할(부모 유지 + 자식 생성).
    - only: 특정 파일명만 로드(테스트용). None이면 전체.
    존재하지 않는 fixture는 건너뛴다(부분 코퍼스 허용).
    """
    from law_rag.knowledge.parser import parse_admrul_xml, parse_law_xml
    from law_rag.knowledge.splitter import split_corpus

    base = Path(fixtures_dir)
    chunks: list[LawChunk] = []
    for fname, kind, meta in CORPUS_SPEC:
        if only is not None and fname not in only:
            continue
        fpath = base / fname
        if not fpath.exists():
            continue
        xml = fpath.read_text(encoding="utf-8")
        parser = parse_admrul_xml if kind == "admrul" else parse_law_xml
        chunks.extend(parser(xml, meta))

    result = split_corpus(chunks) if split else chunks
    _apply_registry(result)  # A-2: applies_to 채우기(레지스트리 있으면)
    return result


_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "data" / "registry" / "applicability.json"


def _apply_registry(chunks: list[LawChunk]) -> None:
    """data/registry/applicability.json의 applies_to를 청크에 채운다(A-2). 없으면 통과."""
    if not _REGISTRY_PATH.exists():
        return
    reg = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    ap = reg.get("applies_to", {})
    for c in chunks:
        inst = ap.get(c.law_id, {}).get(c.article_no)
        if inst:
            c.applies_to = inst


class _Indexes:
    """참조 해석에 쓰는 코퍼스 인덱스 + 법령 관계맵 (P0-1/P0-2).

    관계맵은 하드코딩하지 않고 코퍼스에서 유도한다:
      - name2id   : 법령명/약칭 → law_id
      - parent_of : law_id → '법'(상위 법률, hierarchy=1)의 law_id
      - decree_of : law_id → '영'(같은 패밀리의 시행령=대통령령)의 law_id
    조/항 인덱스:
      - by_para : (law_id, 조, 항) → chunk_id   (항 정밀 해석)
      - by_art  : (law_id, 조) → 대표 chunk_id  (조의 첫 청크=제1항/단일, 조 제목 포함)
      - appx    : (law_id, '별표N') → 대표 chunk_id (분할 별표면 첫 자식 -S1)
    """

    def __init__(self, chunks: list[LawChunk]):
        self.ids = {c.chunk_id for c in chunks}
        self.name2id: dict[str, str] = {}
        id2type: dict[str, LawType] = {}
        id2level: dict[str, int] = {}
        for c in chunks:
            id2type.setdefault(c.law_id, c.law_type)
            id2level.setdefault(c.law_id, c.hierarchy_level)
            for n in filter(None, [c.law_name, c.law_short_name]):
                self.name2id.setdefault(n, c.law_id)

        # 조/항 인덱스
        self.by_para: dict[tuple[str, str, str | None], str] = {}
        self.by_art: dict[tuple[str, str], str] = {}
        appx_cand: dict[tuple[str, str], list[str]] = defaultdict(list)
        for c in chunks:
            if c.is_form:
                continue  # 서식은 해석 대상에서 제외
            self.by_para.setdefault((c.law_id, c.article_no, c.paragraph_no), c.chunk_id)
            if c.article_no.startswith("별표"):
                if not c.is_split_parent:
                    appx_cand[(c.law_id, c.article_no)].append(c.chunk_id)
            else:
                self.by_art.setdefault((c.law_id, c.article_no), c.chunk_id)
        # 분할 별표는 첫 자식(-S1)이 사전식 최소 → min. 비분할은 -B 본체.
        self.appx: dict[tuple[str, str], str] = {k: min(v) for k, v in appx_cand.items()}

        # 관계맵 유도
        self.parent_of, self.decree_of = _derive_relations(
            chunks, self.name2id, id2type, id2level
        )

    def lookup(self, law_id: str, art: str, para: str | None) -> str | None:
        """(법령, 조, 항) → chunk_id. 항 정밀 → 조 대표 순으로 폴백."""
        if para is not None:
            cid = self.by_para.get((law_id, art, para))
            if cid:
                return cid
        return self.by_art.get((law_id, art))


def _derive_relations(chunks, name2id, id2type, id2level):
    """코퍼스에서 '법'(parent_of)·'영'(decree_of) 관계를 유도한다.

    1순위: CORPUS_SPEC 메타의 parent_law_name (실코퍼스에서 신뢰도 높음).
    2순위: parent_law_ref 선두 법령명 (CORPUS_SPEC 밖 데이터, 예: 샘플).
    '법'은 항상 법률(hierarchy=1)이 되도록 사슬(고시→시행령→법률)을 타고 올라간다.
    """
    spec_parent: dict[str, str] = {}
    for _, _, meta in CORPUS_SPEC:
        lid, pname = meta.get("law_id"), meta.get("parent_law_name")
        if lid and pname and pname in name2id:
            spec_parent[lid] = name2id[pname]

    ref_votes: dict[str, Counter] = defaultdict(Counter)
    for c in chunks:
        if not c.parent_law_ref:
            continue
        m = re.match(r"\s*(.+?)\s*제\d", c.parent_law_ref)
        if m:
            pid = name2id.get(m.group(1).strip())
            if pid and pid != c.law_id:
                ref_votes[c.law_id][pid] += 1

    def act_of(lid, seen=None):
        """lid의 '법'(법률 조상)을 찾는다. 직접 부모가 법률이 아니면 더 올라간다."""
        seen = seen or set()
        if lid in seen:
            return None
        seen.add(lid)
        p = spec_parent.get(lid)
        if p is None and ref_votes.get(lid):
            p = ref_votes[lid].most_common(1)[0][0]
        if p is None:
            return None
        return p if id2level.get(p) == 1 else act_of(p, seen)

    parent_of: dict[str, str] = {}
    for lid in id2type:
        if id2level.get(lid, 9) > 1:
            act = act_of(lid)
            if act:
                parent_of[lid] = act

    # 패밀리(법률)별 시행령(대통령령) 식별
    decree_by_act: dict[str, str] = {}
    for lid, typ in id2type.items():
        if typ == LawType.대통령령 and parent_of.get(lid):
            decree_by_act[parent_of[lid]] = lid
    decree_of: dict[str, str] = {}
    for lid, typ in id2type.items():
        if typ == LawType.대통령령:
            decree_of[lid] = lid  # '이 영' = 자기
        act = parent_of.get(lid)
        if act and act in decree_by_act:
            decree_of[lid] = decree_by_act[act]
    return parent_of, decree_of


def build_edges(chunks: list[LawChunk]) -> list[Edge]:
    """청크들의 관계 필드에서 엣지를 생성한다.

    - parent_law_ref → delegates_to (위임: 하위규정이 상위법을 가리킴)
    - exception_of   → exception_to (예외)
    - cites          → refers_to   (인용)

    해석은 _Indexes가 유도한 관계맵으로 한정어(법/영/「법령명」)를 올바른 법령으로,
    항 지정 참조를 항 단위로 연결한다(P0-1/P0-2). 코퍼스 밖 법령은 'EXT:…'로 저장해
    미해결과 구분한다. 동일 (from,to,type) 엣지는 중복 제거한다.
    """
    idx = _Indexes(chunks)
    edges: list[Edge] = []
    seen: set[tuple[str, str, str]] = set()

    def add(from_id: str, to_ref: str, etype: EdgeType) -> None:
        if to_ref == from_id:  # self-loop 방어선
            return
        key = (from_id, to_ref, etype.value)
        if key in seen:
            return
        seen.add(key)
        edges.append(Edge(from_chunk_id=from_id, to_ref=to_ref, edge_type=etype))

    for c in chunks:
        # 서식·분할 별표 원본은 엣지 출발점에서 제외(검색 대상 아님).
        if c.is_form or c.is_split_parent:
            continue
        # 별표(표 데이터): 본문은 표라서 잡음 → '제목'의 "(제N조 관련)" 근거조문만 연결(P1-A).
        #   delegates_to = 별표(세부 기준)가 그 근거조문의 위임을 받아 존재함.
        if c.article_no.startswith("별표"):
            for r in scan_refs(c.article_title):
                tgt = _resolve_ref(r.canonical(), c.law_id, idx)
                if tgt and not tgt.startswith("EXT:"):
                    add(c.chunk_id, tgt, EdgeType.delegates_to)
            continue
        if c.parent_law_ref:
            add(c.chunk_id, _resolve_ref(c.parent_law_ref, c.law_id, idx) or c.parent_law_ref,
                EdgeType.delegates_to)
        if c.exception_of:
            add(c.chunk_id, _resolve_ref(c.exception_of, c.law_id, idx) or c.exception_of,
                EdgeType.exception_to)
        for cite_ref in c.cites:
            add(c.chunk_id, _resolve_ref(cite_ref, c.law_id, idx) or cite_ref,
                EdgeType.refers_to)

    return edges


def _resolve_ref(ref: str, source_law_id: str, idx: _Indexes) -> str | None:
    """참조 문자열 → 코퍼스 내 chunk_id.

    해석 규칙:
      - 「법령명」/평문 법령명: 코퍼스에 있으면 그 법령, 없으면 'EXT:…'(외부 법령).
      - '법'       → parent_of(상위 법률). 법률 자신이면 자기.
      - '영/령'    → decree_of(시행령). 시행령 자신이면 자기.
      - '이 법/같은 법' → '법'과 동일 규칙, '이 영/같은 영' → '영'과 동일 규칙.
      - 한정어 없음 → 자기 법령(법내 참조).
      - 항 지정 시 항 단위, 아니면 조 대표 청크. 별표는 (분할 시) 첫 자식.
    """
    app = appendix_no(ref)
    if app and "조" not in ref:
        law_id = source_law_id
        for nm, lid in idx.name2id.items():
            if nm in ref:
                law_id = lid
                break
        return idx.appx.get((law_id, app))

    r = parse_ref_string(ref)
    if r is None:
        return None

    if r.name:
        lid = idx.name2id.get(r.name)
        if lid is None:
            return f"EXT:「{r.name}」 " + (f"제{r.art}조제{r.para}항" if r.para else f"제{r.art}조")
        return idx.lookup(lid, r.art, r.para)

    q = r.qual
    if q in ("법", "이법", "같은법"):
        law_id = idx.parent_of.get(source_law_id, source_law_id)
    elif q in ("영", "령", "이영", "같은영"):
        law_id = idx.decree_of.get(source_law_id, source_law_id)
    elif q == "규칙":
        return None  # 시행규칙 매핑은 유도 안 함(드묾) → 미해결
    else:
        law_id = source_law_id  # 한정어 없음 → 자기 법령

    return idx.lookup(law_id, r.art, r.para)
