# AGENT_USAGE — 에이전트용 TAP MCP 도구

LLM 에이전트가 **답하기 전에** TAP에게 "이 근거로 답해도 되나"를 묻게 하는 MCP 서버다. 조문을 *찾아 주는* 도구(예: [korean-rnd-regs-mcp](https://github.com/smilemin07/korean-rnd-regs-mcp))와 나란히 등록해, 찾은 근거의 **답 가능성 판정**을 맡긴다.

> ⚠️ **종단 간(end-to-end) 성능은 평가하지 않았다.** 이 저장소의 실험(E1~E3·사람 검수)은 TAP의 판정만 측정한다. LLM이 이 도구를 실제로 어떻게 활용해 최종 답 품질이 얼마나 좋아지는지는 **측정 범위 밖**이다.

## 설치

```bash
pip install .[mcp]      # 선택 의존성(mcp/FastMCP). 미설치 시 서버·테스트는 자동 skip.
```

## 도구 3개 (모두 readOnly·네트워크 없음·감사 로그 1줄 반환)

| 도구 | 입력 | 하는 일 | 포장 대상 |
|---|---|---|---|
| `tap_decide` | `question`, `config="B4"` | 답해도 되는지 판정(ANSWER/CONFIRM/ABSTAIN/CORRECT) + 검색 상위 3건·질문 인용 확인 | `pipeline.decide` |
| `tap_resolve_citation` | `citation`, `context=""` | 인용이 코퍼스 실제 조문으로 확정되나(P) — pgate, 근거 없으면 보류 | `provenance.resolve` |
| `tap_check_currency` | `law_id`, `article`, `paragraph=None` | 인용 번호가 '낡은 번호'인가(T) — 현행에도 있으면 단정 않고 후보 제시 | `temporal.classify` |

도구는 **기존 함수의 얇은 포장**이며 판정 로직을 새로 만들지 않는다(테스트가 `pipeline.decide`와 동일 결과임을 확인).

## 권장 순서

```
1. (외부 MCP) 조문 조회 — 질문에 맞는 근거 조문을 찾는다.
2. tap_decide(question) 또는 tap_resolve_citation / tap_check_currency 로 "답해도 되는지" 판정.
3. 결과가 CONFIRM·ABSTAIN이면 → 사용자에게 확인/보류. ANSWER면 → 근거를 붙여 답.
```

핵심은 **멈춰야 할 때 멈추는 것**이다. CONFIRM(후보 제시·확인요청)·ABSTAIN(보류)을 받으면 에이전트는 추측하지 말고 사용자에게 넘긴다.

## MCP 클라이언트 설정 예시

### TAP 단독

```json
{
  "mcpServers": {
    "tap-legalrag": {
      "command": "python",
      "args": ["-m", "law_rag.mcp_server"],
      "env": {"PYTHONPATH": "src"}
    }
  }
}
```

### korean-rnd-regs-mcp와 함께 (역할 분담: 사서 + 검수자)

```json
{
  "mcpServers": {
    "korean-rnd-regs": { "command": "...", "args": ["..."] },
    "tap-legalrag": {
      "command": "python",
      "args": ["-m", "law_rag.mcp_server"],
      "env": {"PYTHONPATH": "src"}
    }
  }
}
```
→ `korean-rnd-regs`가 조문을 찾고, `tap-legalrag`가 그 근거로 답해도 되는지 판정한다. 두 도구를 함께 쓰는 구성의 효과 측정은 [ROADMAP](ROADMAP.md) v1.2 실험.

## 반환 예시 (tap_decide)

```json
{
  "decision": "CONFIRM",
  "reason": "...",
  "path": ["router", "retrieval", "...", "gate"],
  "top3_evidence": [{"ref": "...", "score": 0.03}, ...],
  "question_citation": {"citation": "시행령 제64조 제2항", "status": "RESOLVED", "law_id": "013933", "article": "64", "paragraph": "2"},
  "run_id": "…", "config": "B4", "corpus_version": "CORPUS-20260802",
  "audit": "[2026-10-10T..] tap.decide decision=CONFIRM config=B4 run_id=…"
}
```
