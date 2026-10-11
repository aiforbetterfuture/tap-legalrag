#!/usr/bin/env bash
# scripts/repro_check.sh — 공개 후보 폴더만으로 재현 시험 (작업지시서 26 §1-4).
# 새 가상환경 → requirements-lock 설치 → pytest → CLI → eval_decision 해시 비교.
# 네트워크·.env 불필요. push 하지 않는다. 사용: bash scripts/repro_check.sh [DEST]
set -u
DEST="${1:-../tap-legalrag-public}"
cd "$DEST" || { echo "[중단] 폴더 없음: $DEST"; exit 2; }
echo "[repro] 대상: $(pwd)"

python -m venv .venv_repro || exit 1
# shellcheck disable=SC1091
source .venv_repro/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements-lock.txt || { echo "[실패] 설치"; exit 1; }

echo "[repro] pytest"
python -m pytest -q || { echo "[실패] pytest"; exit 1; }

echo "[repro] CLI"
PYTHONPATH=src python -m law_rag ask "시행령 제64조 제2항은 무엇을 규정하나요?" || { echo "[실패] CLI"; exit 1; }

echo "[repro] eval_decision 해시"
PYTHONPATH=src python scripts/eval_decision.py > /tmp/e3_a.txt 2>&1
PYTHONPATH=src python scripts/eval_decision.py > /tmp/e3_b.txt 2>&1
if diff -q /tmp/e3_a.txt /tmp/e3_b.txt >/dev/null; then
  echo "[repro] 결정성 OK (2회 동일)"
else
  echo "[경고] eval_decision 2회 출력이 다름"; fi

deactivate
echo "[repro] 완료 — .venv_repro 는 수동 삭제 가능"
