"""
scripts/install_hooks.py — 커밋 전 PII 자동 검사 훅 설치 (S0-4).

실행: python scripts/install_hooks.py
설치 후: 커밋 때마다 스테이징된 tests/golden·docs·data 텍스트 파일을 pii_scan으로 검사하고,
         의심 항목이 있으면 커밋을 차단한다(위치만 출력, 원문 미출력).
오탐 확신 시에만 'git commit --no-verify'.
"""
from __future__ import annotations

import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HOOK = """#!/bin/sh
# 자동 설치됨(scripts/install_hooks.py) — 커밋 전 PII 검사 (S0-4)
python scripts/pii_scan.py --staged
if [ $? -ne 0 ]; then
  echo "커밋 차단: 개인정보/민감정보 의심 항목이 있습니다(위 위치 확인)."
  echo "오탐이 확실하면: git commit --no-verify"
  exit 1
fi
"""


def _hooks_dir() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks"],
        capture_output=True, text=True, cwd=ROOT,
    ).stdout.strip()
    p = Path(out)
    return p if p.is_absolute() else (ROOT / p)


def main() -> int:
    hooks = _hooks_dir()
    hooks.mkdir(parents=True, exist_ok=True)
    pc = hooks / "pre-commit"
    pc.write_text(HOOK, encoding="utf-8", newline="\n")  # sh는 LF 필요
    pc.chmod(pc.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"pre-commit 훅 설치됨: {pc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
