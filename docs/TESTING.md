# TESTING — 무엇을 테스트하고, 공개판에서 무엇을 건너뛰나

이 저장소는 **두 환경**에서 테스트합니다. 숫자가 다른 이유와, 공개판에서 검증하지 못하는 범위를 숨기지 않기 위해 이 문서를 둡니다.

| 대상 | 해설서 원문 | 실행 환경 | 기준 커밋 | 명령 | 결과 |
|---|---|---|---|---|---|
| 개발판 | 있음 (비공개) | Windows · Python 3.13 (mcp 설치) | v1.0.0 | `pytest -q` | 148 passed · 2 skipped (150 collected) |
| 공개판 (임시 사본) | 없음 (공공누리 제4유형이라 제외) | Windows · Python 3.13 | v1.0.0에서 추출 | `pytest -q -rs` | 128 passed · 22 skipped · 0 failed |
| 공개판 | 없음 | GitHub Actions · Ubuntu / Windows · Python 3.11 | 공개 저장소 `main` | `pytest -q -rs` 외 | Actions 탭 로그가 기준 |

> 테스트 개수는 환경에 따라 달라집니다. 예를 들어 MCP 선택 의존성이 없는 환경에서는 `test_mcp_server`가 건너뛰어지고, 수집되는 테스트 수도 달라질 수 있습니다. 그래서 수치마다 실행 환경·커밋·명령을 함께 적습니다. 원격 CI 결과는 이 문서가 아니라 GitHub Actions 로그로 확인해 주세요.

---

## 1. 공개판에서 건너뛰는 테스트 22개

건너뛴다는 것은 **실패를 숨긴다는 뜻이 아닙니다.** 각 테스트는 사유를 출력하며(`pytest -rs`), 해설서 원문을 개발용 위치에 두면 자동으로 다시 실행됩니다.

| 묶음 | 개수 | 테스트 위치 | 건너뛰는 이유 | 공개판에서 검증하지 못하는 것 |
|---|---|---|---|---|
| ① 평가 수치 재현 | 11 | `test_provenance`(2) · `test_label_views`(2) · `test_human_review`(2) · `test_clarify`(3) · `test_decision`(1) · `test_eval_v3`(1) | 해설서 문맥·FAQ 질문이 있어야 E1·E3 수치를 다시 계산할 수 있음 (`requires_manual` 표시) | E1 세 기준·E3 결정 수치가 **처음부터 다시 계산해도 같은지**. 공개판에서는 결과 JSON·평가 코드·라벨로 검토 |
| ② 해설서 파서 | 9 | `test_guide`(8) · `test_guide_drift`(1) | 해설서 본문 청크(`data/guide/`)가 공개판에 없음 | 해설서 PDF를 절 단위로 나누는 파서와 판본 변동 검사 |
| ③ 아직 하지 않은 작업 | 2 | `test_p1b_data`(2) | 보건의료 R&D 법령 추가, 시행일 기준 재수집이 아직 미실시 | 해당 기능 자체가 없음(개발판에서도 건너뜀) |

MCP 선택 의존성(`pip install .[mcp]`)을 설치하지 않은 환경에서는 `test_mcp_server`가 추가로 건너뜁니다.

## 2. 공개판에서도 실행되는 핵심 검사

| 영역 | 무엇을 확인하나 | 위치 |
|---|---|---|
| 판정 게이트 | P·T·A 각 축의 판정, 결정 우선순위, 설정별(B0~B4) 동작 | `test_decision`, `test_provenance`, `test_temporal`, `test_applicability`, `test_pipeline` |
| 검색·파싱 | BM25, 법령 XML 파싱, 조문 분할 | `test_bm25`, `test_parser`, `test_splitter` |
| 라벨 정합성 | 라벨 세 파일 100행·조인·분포·사람 확인 5건 반영·미확정 0건 | `test_check_labels`, `scripts/check_labels.py` |
| 문서 수치 | README·RESULTS·HUMAN_REVIEW의 수치가 결과 JSON과 같은지 | `test_sync_numbers`, `scripts/sync_numbers.py` |
| 보안 기반 | 키 하드코딩 없음, `.gitignore`, 개인정보 검사기 동작, 데이터 파일 줄바꿈 보존 | `test_s0_security` |

## 3. 직접 실행하기

```bash
python -m pip install -r requirements-lock.txt
PYTHONPATH=src python -m pytest -q -rs        # -rs: 건너뛴 테스트와 사유 표시
```

Windows PowerShell에서는 한글 입출력을 위해 먼저 `$env:PYTHONPATH="src"; $env:PYTHONUTF8="1"`을 설정하세요.

`test_s0_security`의 줄바꿈 보존 검사는 `git` 저장소 안에서만 의미가 있습니다. ZIP으로 내려받아 실행하면 이 검사는 사유를 표시하고 건너뜁니다.

## 4. 해설서가 있는 환경에서 전체 재현하기

1. 공식 게시처에서 「국가연구개발혁신법 매뉴얼」을 직접 내려받습니다([DATA_SOURCES](../DATA_SOURCES.md)).
2. 개발용 파서로 로컬에서 청크를 만듭니다(`scripts/parse_manuals.py`). 만든 결과물은 **재배포하지 마세요**(공공누리 제4유형: 변경금지).
3. 다시 `pytest -rs`를 실행하면 ② 묶음(파서)이 실행됩니다.

> ① 묶음(평가 수치 재현)은 정답 라벨의 **해설서 문맥 열**이 있어야 합니다. 이 열은 해설서 발췌이므로 공개판에서 제거했고, 그래서 해설서를 직접 받아도 ① 묶음은 계속 사유를 표시하고 건너뜁니다. 이 범위는 결과 JSON(`results/`)과 평가 코드(`scripts/eval_*.py`)로 검토해 주세요.
