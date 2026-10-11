# CHANGELOG

버전 표기는 공개 릴리스 기준(v1.0.0 = 10/11). 개선되지 않은 지표도 삭제하지 않는다.

## rc5 → v1.0.0 (2026-10-11, 공개 문서·추출 규칙 보강 — 판정 로직·평가 수치 불변)

> rc5 대비 **판정 로직·임계값·평가 정의·수치 모두 불변.** 공개 문서 추가와 공개판 추출/검사 규칙 보강(C1~C11), 평가 입력 파일 추적만.

- **공개 문서**(사용자 작성): README(3분 요약·설계 결정 6가지·보안 표·구현 범위 3단), SECURITY(위협·보호 장치·한계·신고 경로), DESIGN_DECISIONS·WORKFLOW·TESTING 신설.
- **평가 입력 추적**: `provenance_gold/adjudicated/review_R1.csv`, 골든셋, `TAP_DATA_CONTRACTS_v1.md`, `build_provenance_gold.py`. 확정본 해시 불변(adjudicated `e90d4835…`). `*_draft_manual.xlsx`는 `.gitignore`.
- **공개판 추출 보강(C1~C11)**: 골든 FAQ 질문 90건 비움·`context` 열 삭제+해시·`개발 규칙 문서(비공개)`/단독 `LLM` 중립화·`.bak`/`.egg-info` 제외·`SECURITY.md` 허용목록·git 밖 `test_s0_1` skip·`PUBLIC_REDUCED.txt` 표식·CI UTF-8·시연 일치 테스트(`test_examples`)·pyproject `tap-legalrag 1.0.0`·`scan_public --denylist`.
- **테스트**: 개발판 146 passed·2 skipped(148 collected, Windows·3.13·mcp). 공개판 임시 사본 126 passed·22 skipped·0 failed(저장소 안·밖). 매뉴얼 유래 겹침 0건. 블라인드 금칙어 목록 미작성 시 NOT VERIFIED.
- 공개판 첫 커밋 해시는 push 후 이 자리에 기록: `공개판 v1.0.0 = 개발판 커밋 <hash>`.

## rc4 → rc5 (2026-10-11, 채점 기준 추가 + 공개 준비 — 판정 로직 불변)

> **성능 개선판이 아니라 '채점 기준 추가판'이다.** 판정 로직·임계값·설정은 하나도 바꾸지 않았다. 결과 차이는 전적으로 **라벨 정의(gold/final/context)** 때문이며 코드 변경이 아니다. rc1~rc4 결과는 보존.

- **사람 검수 100건 + 불일치 26건 최종판정**(설계자 1인, `provenance_adjudicated.csv`, 수정 없이 사용). 독립·외부 검증 아님.
- **E1 세 기준**(`eval_provenance.py --labels` → `results/e1_label_views.json`): gold(AI 정답표·동결)·final(최종판정)=법령 귀속 과제, context=답변 가능성 판정(2×2). test 판정정확도 — gold 0.617/0.750/0.650, final 0.483/0.617/0.750, context 0.450/0.583/0.717. **정답 정의에 따라 상대 성능이 달라짐**(어느 방식 우수 주장 아님).
- 사용자 확인 5건 반영(PG-002·003 법령 특정 불가, PG-027 C=Y, PG-050 C=N, PG-013 C=N) — `revision_log` 기록, `check_labels.py`가 정합성 검사. pending 0건.
- **공공누리 제4유형**: 매뉴얼 본문(`data/guide/`)·발췌·FAQ 질문을 공개판에서 제외(위치·해시만). 매뉴얼 유래 겹침 검사 0건. 매뉴얼 필요 테스트는 `requires_manual`로 skip(개발 139 passed / 공개 119 passed·22 skip).
- LICENSE(Apache-2.0·코드 전용)·NOTICE·DATA_SOURCES 3층. 공개용 표기 중립화(내부 코드명·모델명). CI 매트릭스(Ubuntu+Windows).
- 완화 일치 **81/100**(최종판정 엑셀 설명의 82는 규칙 확정 전 값이었음).
- E3(결정)는 바꾸지 않음(rc3/rc4 그대로). 매니페스트 해시 변화 없음.

## rc3 → rc4 (2026-10-10, 문서 정합·저장소 얼굴·에이전트 도구 — 평가 로직·수치 불변)

- **문서 정정(§A)**: PG-025는 사람(R1)이 '판단 불가'로 답해 **②(정답 정의 문제) 지지**임을 명기(규칙 오분류는 한계로 기록, 규칙 미수정). 민감도 문장을 "정답 정의에 따라 pgate가 fallback과 **동률~근소 우위(0.750~0.767)**"로 교체('결론이 뒤집힌다' 표현 제거). 설계 문항 제외 목록이 10/7 계획문서에 **사전 기록**됐다는 근거를 EVAL_SPEC §11에 추가.
- **CLI 출력(§A)**: `python -m law_rag ask`가 질문 인용 확인 + **검색 상위 3건(관련도)**를 표시(판정 로직 불변, 출력 형식만). 약한 검색이 '대표 근거'처럼 보이던 문제 완화.
- **숫자 자동 대조(§A)**: `scripts/sync_numbers.py` + `docs/NUMBERS.md` + 테스트 — README·RESULTS·HUMAN_REVIEW 수치를 결과 JSON과 대조, 다르면 실패.
- **저장소 얼굴(§B)**: README 전면 재작성(9절, mermaid, 주 결과 rc3, 관련 프로젝트 역할분담). ARCHITECTURE·PROCESS·ROADMAP·RELATED_WORK 신설, LIMITATIONS 갱신. CI(`.github/workflows/tests.yml`, Python 3.11·requirements-lock·네트워크 없음).
- **에이전트용 MCP(§C)**: `src/law_rag/mcp_server.py`(선택 의존성 [mcp], FastMCP) — `tap_decide`·`tap_resolve_citation`·`tap_check_currency` 3개. 기존 함수의 **얇은 포장만**(판정 로직 추가 없음), readOnly·네트워크 없음. `docs/AGENT_USAGE.md`에 '종단 간 성능 미평가' 명시.
- **E3 수치는 rc3와 동일**(B4 0.813/0.425, B0 0.846). 재실행 결과 파일 해시 동일. 테스트 132 passed·2 skipped.

## rc2 → rc3 (2026-10-09, 버그 2개 수정 — 새 기능 없음)

- **버그1 조 단위 존재 확인**: '제64조(제목) 제1항'에서 괄호 제목 때문에 항이 떨어져 조만 추출되고, 항 분할된 조는 'LAW-..-J64' 단독 chunk가 없어 DRIFT로 오판(M-131). → `_RE_CITE`가 괄호 제목 건너뛰고 항 추출, `temporal`은 조 단위 스캔으로 존재 확인.
- **버그2 단정 교정 금지**: 계보상 낡은 번호가 **현행에도 존재**(예: 64:2는 2024 신설 현행 항)하면 CORRECT 단정 대신 **CONFIRM + 후보 2개**. 현행에 없을 때만 CORRECT → rc3에서 CORRECT 예측 **0건**.
- **표현 정정**: rc2의 "CORRECT 생성(골든 3건 중 1건)"은 사실과 다름 — rc2 CORRECT 예측은 M-117 1건이고 정답은 ANSWER(오답), 정답 CORRECT_PREMISE 3건은 전부 미적중.
- rc1/rc2/rc3 E3 결과 파일 모두 보존.

## rc1 → rc2 (2026-10-09, 버그 수정 — 새 기능 없음)

- **gate P·T 적용 대상 수정(정확성)**: rc1은 P·T를 '검색된 근거'에 적용 → 문서의 "출처 RESOLVED"와 불일치, B4−P가 B4와 동일해지는 원인.
  rc2는 P·T를 **질문 텍스트의 인용에만** 적용(검색된 현행 조문엔 미적용). 결과: B4−P≠B4·B4−T≠B4(각 레이어 효과 분리). (rc2의 CORRECT 관련 표현은 rc3에서 사실대로 정정 — 위 참조.)
- **temporal 적용 범위**: 현행 코퍼스 조문은 항상 CURRENT_VALID. 계보(낡은 번호 교정)는 질문 인용·2차 자료에만.
- **시연 자동 생성**: `scripts/build_demo.py`가 실제 파이프라인(B4) 결과로 `docs/demo/index.html` 생성(손으로 쓴 문구 제거).
- **eval_decision**: GOLD_MAP에 없는 라벨은 기본값 대신 오류.
- **날짜 정정**: 동결 문서·RESULTS의 "10/10"을 실제 실행일 2026-10-09로.
- **사람 검수 R1 반영**: `docs/HUMAN_REVIEW.md`(엄격 12/20·관대 14/20, 잘못된 보류 17=①10+②7, 민감도 fallback 0.750/pgate 0.767). rc1 수치는 보존.
- rc1 E3 수치(`results/e3_decision.json`)는 지우지 않고 rc2(`results/e3_decision_rc2.json`)와 나란히 둔다.

## [Unreleased] — 10/9 작업 (rc1, 기능 동결 전)

### 평가
- EVAL_SPEC **v1.1**: E3의 B0(어휘 기준선)을 단순 토큰 겹침 → **실제 BM25(Okapi, k1=1.5, b=0.75)**로 고정.
  이유: 약한 기준선은 이후 단계 효과를 부풀릴 수 있어 공정 비교를 위해 표준 BM25 사용.
- EVAL_SPEC v1: MRR 지표를 **MRR@10**으로 확정(구 0.200은 MRR@5, 사양 0.217은 MRR@10). RunManifest 도입.

### P(출처)
- `provenance.resolve` 3모드: **naive**(문맥·외부감지 없이 무조건 혁신법) / **fallback**(문맥·외부감지+못찾으면 혁신법) / **pgate**(근거 없으면 보류).
- E1 결과(test60): 오귀속률 naive 0.383 → fallback 0.25 → pgate 0.067. 판정정확도 fallback 0.75 > pgate 0.65 > naive 0.617(혼재 — pgate는 오귀속↓·잘못된 보류↑ 트레이드오프).

### 음성·한계 결과(삭제 금지)
- 명확화 정책 v0는 always-NONE보다 낫지 않은 경우가 있었음(이전 분석). 본 결과와 분리 보고.
- A-3 해설 표류 탐지는 토큰 겹침이 어휘 불일치(패러프레이즈)를 못 이어 재현율 낮은 프로토타입.
