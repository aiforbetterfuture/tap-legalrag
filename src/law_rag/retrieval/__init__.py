"""
law_rag.retrieval — 검색 계층 (통합설계 4장 ②).

파이프라인: 메타필터(시점·주제) → 하이브리드(BM25+Dense) → RRF → 리랭킹.

설계 원칙:
    각 단계를 독립 함수/클래스로 둔다. 한 단계를 교체해도 나머지는 안 바뀐다.
    예: DenseSearcher를 BGE-M3에서 다른 모델로 바꿔도 Retriever는 그대로.

V1 참조구현:
    실제 임베딩/BM25 엔진 대신, 인터페이스와 흐름을 검증하는 최소 구현을 둔다.
    Cursor에게 "이 인터페이스를 만족하도록 BGE-M3 검색기를 구현하라"고 지시하면
    참조구현을 실제 엔진으로 교체할 수 있다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from datetime import date

from law_rag.models import LawChunk, Query, ScoredChunk, TopicTag

# P0-4: 주제 일치 시 주는 가산점(배수). 거름망이 아니라 가산점으로만 쓴다.
# 주제 태그는 키워드 추정치라, 거름망으로 쓰면 태그가 빠진 정답을 잘라낸다.
TOPIC_BOOST = 0.1  # TODO(golden): 골든셋으로 조정


# ─────────────────────────────────────────────────────────────
# 1) 메타 필터: 후보를 먼저 좁힌다 (시점·주제·폐지여부)
# ─────────────────────────────────────────────────────────────
def meta_filter(
    chunks: list[LawChunk],
    *,
    as_of: date | None = None,
    topic: TopicTag | None = None,
    include_repealed: bool = False,
    include_forms: bool = False,
    include_split_parents: bool = False,
) -> list[LawChunk]:
    """검색 전에 후보를 좁힌다. 시점 필터가 정산 질문 정확도의 핵심.

    - as_of가 주어지면 그 시점에 유효한 조문만 남긴다(협약연도 기준 답변).
    - topic이 주어지면 해당 주제 태그를 가진 조문만.
    - 폐지 조문은 기본 제외(이력 조회 시에만 포함).
    - 별지/서식(is_form)은 기본 제외. '무슨 서류를 내야 하나' 질의에서만
      include_forms=True로 포함한다. 서식은 본문이 매우 길고(수만 자)
      키워드가 반복돼 조문 질의의 검색 점수를 잠식하기 때문.
    - 분할된 대형 별표의 원본(is_split_parent)은 기본 제외. 자식(-S{n})으로
      검색하고, 전체 확인이 필요할 때만 include_split_parents=True로 노출.
    """
    result = []
    for c in chunks:
        if not include_repealed and c.is_repealed:
            continue
        if not include_forms and c.is_form:
            continue
        if not include_split_parents and c.is_split_parent:
            continue
        if as_of is not None and not c.is_valid_at(as_of):
            continue
        if topic is not None and topic not in c.topic_tags:
            continue
        result.append(c)
    return result


# ─────────────────────────────────────────────────────────────
# 2) 검색기 인터페이스: BM25 / Dense / 리랭커를 같은 계약으로
# ─────────────────────────────────────────────────────────────
class Searcher(ABC):
    """검색기 인터페이스. 후보 청크 중 질의와 관련된 것을 점수와 함께 반환."""

    @abstractmethod
    def search(self, query: str, candidates: list[LawChunk], top_k: int) -> list[ScoredChunk]:
        ...


class KeywordSearcher(Searcher):
    """BM25 자리의 참조구현. V1은 간단한 토큰 겹침 점수.
    실제로는 OpenSearch/pg + Nori 형태소로 교체한다.

    법령에서 왜 키워드 검색이 필요한가:
        '제32조','간접비','기술료' 같은 정확한 용어·조문번호 매칭은
        의미검색(Dense)보다 키워드검색이 강하다.
    """

    def search(self, query: str, candidates: list[LawChunk], top_k: int) -> list[ScoredChunk]:
        q_tokens = set(_tokenize(query))
        scored = []
        for c in candidates:
            text = f"{c.article_title} {c.body_text} {c.context_prefix or ''}"
            c_tokens = set(_tokenize(text))
            overlap = len(q_tokens & c_tokens)
            if overlap > 0:
                scored.append(ScoredChunk(chunk=c, score=float(overlap)))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]


class BM25Searcher(Searcher):
    """Okapi BM25 (순수 파이썬, 새 의존성 없음). E3의 B0(어휘 기준선)용 '실제 BM25'.

    KeywordSearcher(단순 토큰 겹침)와 달리 IDF·문서길이 정규화를 적용한다.
    통계(df·평균길이)는 매 호출의 candidates에서 계산한다(코퍼스 규모상 충분히 빠름)."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b

    def search(self, query: str, candidates: list[LawChunk], top_k: int) -> list[ScoredChunk]:
        import math
        q_tokens = [t for t in _tokenize(query)]
        docs = [_tokenize(f"{c.article_title} {c.body_text} {c.context_prefix or ''}") for c in candidates]
        N = len(docs) or 1
        avgdl = sum(len(d) for d in docs) / N if docs else 1.0
        df: dict[str, int] = {}
        for d in docs:
            for t in set(d):
                df[t] = df.get(t, 0) + 1
        scored: list[ScoredChunk] = []
        for c, d in zip(candidates, docs):
            if not d:
                continue
            tf = Counter(d)
            dl = len(d)
            s = 0.0
            for t in q_tokens:
                if t not in tf:
                    continue
                idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
                denom = tf[t] + self.k1 * (1 - self.b + self.b * dl / avgdl)
                s += idf * (tf[t] * (self.k1 + 1)) / denom
            if s > 0:
                scored.append(ScoredChunk(chunk=c, score=s))
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]


class DenseSearcher(Searcher):
    """임베딩 검색 자리의 참조구현. V1은 문자 단위 유사도 근사.
    실제로는 BGE-M3 등으로 context_prefix+요약을 임베딩해 교체한다.

    법령에서 왜 의미검색이 필요한가:
        '기계 샀는데 절차?' 같은 일상어 질문은 조문 용어와 안 겹쳐서
        키워드검색이 놓친다. 의미검색이 이 어휘격차를 메운다.
    """

    def search(self, query: str, candidates: list[LawChunk], top_k: int) -> list[ScoredChunk]:
        scored = []
        for c in candidates:
            text = f"{c.context_prefix or ''} {c.article_title} {c.body_text}"
            sim = _char_similarity(query, text)
            scored.append(ScoredChunk(chunk=c, score=sim))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]


# ─────────────────────────────────────────────────────────────
# 3) RRF 융합: 두 검색 결과를 순위 기반으로 합친다
# ─────────────────────────────────────────────────────────────
def reciprocal_rank_fusion(
    result_lists: list[list[ScoredChunk]], k: int = 60
) -> list[ScoredChunk]:
    """여러 검색 결과를 RRF로 융합. 점수 스케일이 달라도 순위로 합치므로 안전.
    RRF 점수 = Σ 1/(k + rank). 여러 검색기가 공통으로 상위에 둔 청크가 뜬다."""
    fused: dict[str, float] = {}
    chunk_map: dict[str, LawChunk] = {}
    for results in result_lists:
        for rank, sc in enumerate(results):
            cid = sc.chunk.chunk_id
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + rank)
            chunk_map[cid] = sc.chunk
    merged = [ScoredChunk(chunk=chunk_map[cid], score=score) for cid, score in fused.items()]
    merged.sort(key=lambda s: s.score, reverse=True)
    return merged


# ─────────────────────────────────────────────────────────────
# 4) 리랭커 인터페이스
# ─────────────────────────────────────────────────────────────
class Reranker(ABC):
    """Cross-Encoder 리랭커 인터페이스. 후보를 정밀 재정렬한다."""

    @abstractmethod
    def rerank(self, query: str, candidates: list[ScoredChunk], top_k: int) -> list[ScoredChunk]:
        ...


class PassthroughReranker(Reranker):
    """참조구현: 상위 top_k를 그대로 통과. 실제로는 bge-reranker-v2-m3로 교체.
    인터페이스를 지키므로 나중에 교체해도 Retriever는 안 바뀐다."""

    def rerank(self, query: str, candidates: list[ScoredChunk], top_k: int) -> list[ScoredChunk]:
        return candidates[:top_k]


# ─────────────────────────────────────────────────────────────
# 5) Retriever: 위 조각들을 조립한 오케스트레이터
# ─────────────────────────────────────────────────────────────
class Retriever:
    """검색 파이프라인 전체를 조립한다. 각 부품은 주입(inject)받으므로
    테스트·교체가 쉽다(의존성 주입 패턴)."""

    def __init__(
        self,
        keyword: Searcher | None = None,
        dense: Searcher | None = None,
        reranker: Reranker | None = None,
    ):
        self.keyword = keyword or KeywordSearcher()
        self.dense = dense or DenseSearcher()
        self.reranker = reranker or PassthroughReranker()

    def retrieve(
        self,
        query: Query,
        corpus: list[LawChunk],
        *,
        first_stage_k: int = 30,
        final_k: int = 6,
    ) -> list[ScoredChunk]:
        result, _ = self.retrieve_with_signal(
            query, corpus, first_stage_k=first_stage_k, final_k=final_k
        )
        return result

    def retrieve_with_signal(
        self,
        query: Query,
        corpus: list[LawChunk],
        *,
        first_stage_k: int = 30,
        final_k: int = 6,
    ) -> tuple[list[ScoredChunk], float]:
        """검색 결과 + '관련성 신호'를 함께 반환한다.

        관련성 신호(relevance)는 키워드 검색이 실제로 겹친 정도다.
        참조구현 Dense는 모든 청크에 점수를 주므로 RRF 순위만으로는
        무관한 질문을 걸러낼 수 없다. 키워드 히트를 신호로 쓴다.
        실제 엔진(리랭커 점수)으로 교체 시 그 점수를 신호로 반환하면 된다.
        """
        # P0-4: 주제는 meta_filter에 넘기지 않는다(거름망 금지). as_of·폐지·서식만 거른다.
        candidates = meta_filter(corpus, as_of=query.as_of)
        if not candidates:
            return [], 0.0
        kw = self.keyword.search(query.text, candidates, first_stage_k)
        dn = self.dense.search(query.text, candidates, first_stage_k)
        # 관련성 신호: 질의와 최상위 후보 본문의 문자 2-gram 겹침(0~1).
        # 참조구현용 근사. 실제 엔진에서는 리랭커 점수를 신호로 반환한다.
        relevance = 0.0
        if kw or dn:
            best = (kw or dn)[0].chunk
            best_text = f"{best.article_title} {best.body_text} {best.context_prefix or ''}"
            relevance = _bigram_overlap(query.text, best_text)
        fused = reciprocal_rank_fusion([kw, dn])
        # P0-4: 주제 일치 청크에 가산점(안정 정렬 유지). relevance 신호는 바꾸지 않는다.
        if query.topic is not None:
            fused = [
                ScoredChunk(
                    chunk=sc.chunk,
                    score=sc.score * (1 + TOPIC_BOOST)
                    if query.topic in sc.chunk.topic_tags else sc.score,
                )
                for sc in fused
            ]
            fused.sort(key=lambda s: s.score, reverse=True)
        ranked = self.reranker.rerank(query.text, fused, final_k)
        return ranked, relevance


# ─────────────────────────────────────────────────────────────
# 6) CoverageGuard: 보유 판본 범위 점검 (P0-5)
# ─────────────────────────────────────────────────────────────
class CoverageGuard:
    """기준일(as_of)이 보유 판본 범위 안인지 점검한다.

    왜 필요한가:
        코퍼스는 현행 판본 하나뿐이라, 과거 기준일을 주면 거의 모든 조문이
        '아직 유효하지 않음'으로 걸러져 "근거 없음"이라는 '틀린 결론'이 나온다.
        사실은 '그 시점 판본을 안 가진 것'이다. 이 둘을 구분해 정직하게 답한다.
        (진짜 해결 = 과거 판본 수집·valid_to·부칙 경과조치는 P1-②)

    범위 = 법령별 min(valid_from). 검색 대상이 아닌 서식·분할부모는 제외한다.
    """

    def __init__(self, corpus: list[LawChunk]):
        self.starts: dict[str, date] = {}
        self.names: dict[str, str] = {}
        for c in corpus:
            if c.is_form or c.is_split_parent:
                continue
            if c.law_id not in self.starts or c.valid_from < self.starts[c.law_id]:
                self.starts[c.law_id] = c.valid_from
            self.names.setdefault(c.law_id, c.law_short_name or c.law_name)

    def check(self, as_of: date | None) -> tuple[str, list[str]]:
        """('ok'|'all_missing'|'partial', 범위 밖 법령명 목록) 반환."""
        if as_of is None or not self.starts:
            return ("ok", [])
        out = [self.names[lid] for lid, start in self.starts.items() if as_of < start]
        if not out:
            return ("ok", [])
        return ("all_missing" if len(out) == len(self.starts) else "partial", out)

    def coverage_lines(self) -> list[str]:
        """보유 판본 안내용: '법령명: YYYY-MM-DD 이후' (시행일 오름차순)."""
        return [
            f"{self.names[lid]}: {start} 이후 보유"
            for lid, start in sorted(self.starts.items(), key=lambda x: x[1])
        ]


# ─────────────────────────────────────────────────────────────
# 내부 유틸 (참조구현 전용. 실제 엔진 교체 시 제거됨)
# ─────────────────────────────────────────────────────────────
def _tokenize(text: str) -> list[str]:
    import re
    return [t for t in re.split(r"[\s,.\(\)\[\]{}·]+", text) if len(t) >= 2]


def _char_similarity(a: str, b: str) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _bigrams(text: str) -> set[str]:
    import re
    t = re.sub(r"\s+", "", text)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _bigram_overlap(query: str, doc: str) -> float:
    """질의의 문자 2-gram 중 문서에 등장하는 비율(0~1).
    무관한 질문은 2-gram이 거의 안 겹쳐 낮은 값이 나온다."""
    qb = _bigrams(query)
    if not qb:
        return 0.0
    db = _bigrams(doc)
    return len(qb & db) / len(qb)
