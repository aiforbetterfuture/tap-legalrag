# TAP 데이터 계약 v1 (2026-10-06, 작업보조 LLM 초안 → P2-0에서 AI 코딩 에이전트가 모델에 반영)

목적: P2-1(출처)·P2-2(시점)·P2-3(적용)·P2-6(보안)·감사가 **같은 객체를 나눠 쓰도록** 구조를 먼저 고정한다.
지금 계산할 수 없는 값은 `"UNKNOWN"` 또는 `None`으로 두고, 필드는 미리 만든다(나중에 뜯어고치지 않기 위해).
규칙: 개발 규칙 문서(비공개) 1번(필드 추가만, 삭제·이름변경 금지)을 따른다. 기존 `LawChunk.applies_to: str`는 유지하고 새 필드로 확장한다.

## 1. 공통 열거형

```
CitationStatus   = RESOLVED | PARTIAL | AMBIGUOUS | UNRESOLVED
TemporalStatus   = CURRENT | HISTORICAL | FUTURE | NOT_YET_EFFECTIVE | TRANSITIONAL | UNKNOWN
DriftStatus      = CURRENT_VALID | HISTORIC_VALID | DRIFT | UNKNOWN
ApplicabilityStatus = VERIFIED | ASSUMED | BRANCHED | MISSING | NOT_APPLICABLE | UNKNOWN
LegalDirectness  = DIRECT | COMBINED | INTERPRETIVE | NO_LEGAL_BASIS | UNKNOWN
ClarifyPolicy    = NONE | ASSUME | BRANCH | ASK
LegalRisk        = HIGH | NORMAL
AnswerDecision   = ANSWER | ANSWER_WITH_NOTICE | VERIFY | BRANCH | ASK | ABSTAIN | OUT_OF_SCOPE | CORRECT_PREMISE
DataTier         = L0 | L1 | L2 | L3
Mode             = PUBLIC | INTERNAL
```

## 2. P — 법령 출처: `LegalRef`

| 필드 | 타입 | 설명 |
|---|---|---|
| raw | str | 원문 인용 문자열("같은 법 시행령 제14조") |
| span | (int,int)? | 원문 위치 |
| law_id | str? | 확정 시 |
| law_name | str? | 정식 명칭 |
| article / article_branch | str? / str? | 조, 가지번호("의2") |
| paragraph / item / subitem | str? | 항·호·목 |
| annex | str? | 별표 번호 |
| version_id | str? | 판본 ID(법제처 MST 또는 공포번호+시행일) |
| status | CitationStatus | |
| candidates | list[LegalRef] | AMBIGUOUS일 때 후보 |
| method | str | alias / inherit / content / explicit_bracket |
| temporal_status | TemporalStatus | P2-2 전까지 UNKNOWN |
| drift_status | DriftStatus | P2-2 전까지 UNKNOWN |
| chunk_id | str? | 해석된 코퍼스 청크(LAW-…) |

정규 키: `{law_id}:J{조}[의{가지}][:H{항}][:I{호}]`, 별표 `{law_id}:B{n}` (골든 주소 체계의 상위호환).

## 3. T — 판본: `LawVersion`, `ArticleLineage`, 질의 날짜

`LawVersion`: law_id · version_id · promulgation_no · promulgated_on · effective_on · superseded_on? · amendment_type(제정/일부개정/전부개정/타법개정) · raw_path · raw_sha256 · normalized_sha256 · fetched_at · source(eflaw/admrul/lsHistory).

`ArticleLineage`(판본 간 조문 계보): law_id · from_version · to_version · from_key · to_key? · change = UNCHANGED | AMENDED | RENUMBERED | INSERTED | DELETED | MOVED.
→ DRIFT 판정의 근거. "매뉴얼 발간일 기준 판본에서 from_key였던 것이 현행 to_key" 이면 HISTORIC_VALID, 어느 판본에서도 내용이 맞지 않으면 DRIFT.

`Query`에 추가: `event_date: date?` · `event_date_source = EXPLICIT | INFERRED | MISSING` (기존 `as_of`는 질의기준일로 의미 고정).

## 4. A — 적용요건: `ApplicabilityCondition`

| 필드 | 설명 |
|---|---|
| chunk_key | 정규 키 |
| condition_type | v1은 3종만: institution_type · project_type(기본사업/수탁사업) · institution_size(중소/중견/대기업) |
| values | 해당 값 목록 |
| source | title / body / junyong |
| junyong_from / substitute | 준용 원 조문, 의제 치환("정부출연기관"→"대학") |

`LawChunk.applicability: list[ApplicabilityCondition] = []` 추가. 기존 `applies_to`는 호환용으로 유지(institution_type 첫 값).

## 5. 답변: `EvidenceClaim`, `LegalStatus`, `AnswerDecision`

`EvidenceClaim`: claim_text · supporting_refs[LegalRef] · supported(bool) — 골든 요지 `[주소]` 매핑과 같은 구조.

`Answer`에 추가(기존 confidence 유지):
```
legal_status = {retrieval_confidence, legal_directness, temporal_status, applicability_status, citation_status}
claims: list[EvidenceClaim]
sufficiency: float | None
risk: LegalRisk | None
decision: AnswerDecision | None
run_id: str | None
```
결정 규칙(헌장 3장): sufficiency < θ → ABSTAIN. 그 외 정책×위험도 표. θ는 상수 + `# TODO(golden)`.

## 6. 감사·재현: `RunManifest` (모든 평가·진단 산출물에 첨부)

run_id · timestamp · code_commit · dirty_tree(bool) · eval_spec_version · golden_path · golden_sha256 · golden_version(열 '골든버전') · corpus_manifest_sha256 · law_version_ids{law_id: version_id} · guide_sha256 · retriever_config(dict + sha256) · llm_model(None) · embedding_model(None) · seed · ci_method("paired_bootstrap_5000") · python/패키지 lock 해시.

## 7. 보안: `DataTier`, `EgressPolicy`

- 모든 문서·청크·질의에 `tier: DataTier`. 법령=L0, 매뉴얼=L1 자동. L2/L3는 수집 시 명시 없으면 **수집 거부**.
- `EgressPolicy`: 목적지(external_llm, local_llm, log, git, eval_output) → 허용 등급 집합. 기본값 거부.
  PUBLIC 모드: 모든 목적지 L0·L1만. INTERNAL 모드: external_llm은 L0·L1만, local_llm·log(마스킹)은 L2까지, L3는 어디에도 안 됨.
- 카나리아: `_restricted/`에 가짜 파일 2개(고유 문자열 `CANARY-TAP-<무작위>`). pii_scan과 별도 `canary_scan`이 저장소·색인·평가 출력에서 이 문자열을 찾으면 실패.
