"""
tests/test_s0_security.py — S0 보안·무결성 기반의 완료 기준.

(a) git check-attr로 xml·csv가 text 변환 대상이 아님(바이트 보존, S0-1)
(b) 소스·데이터 스크립트에 OC 키 하드코딩 없음(S0-2)
(c) pii_scan이 합성 예시를 모두 잡고 깨끗한 문장은 통과(S0-4)
(d) .gitignore에 .env와 _restricted 포함(S0-2/S0-3)
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


# git 저장소 밖(ZIP 내려받기 등)에서는 check-attr가 의미 없으므로 사유 표시 후 skip (C6)
def _inside_git_worktree() -> bool:
    try:
        out = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                             cwd=ROOT, capture_output=True, text=True)
        return out.returncode == 0 and out.stdout.strip() == "true"
    except Exception:
        return False


# ── (a) 바이트 보존 속성 ────────────────────────────────────────
@pytest.mark.skipif(not _inside_git_worktree(),
                    reason="git 저장소 밖(ZIP 등) — .gitattributes 줄바꿈 보존 검사는 git 안에서만 유효")
@pytest.mark.parametrize("rel", [
    "tests/fixtures/real_law.xml",
    "tests/golden/golden_set.csv",
    "data/samples/sample_chunks.json",
])
def test_s0_1_data_files_not_text_converted(rel):
    out = subprocess.run(
        ["git", "check-attr", "text", rel], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    # '-text'이면 'text: unset'. 'set'/'auto'(줄바꿈 변환 대상)이면 실패.
    assert out.endswith("unset"), out


# ── (b) OC 키 하드코딩 없음 ─────────────────────────────────────
def test_s0_2_no_hardcoded_oc_key():
    offenders = []
    pat = re.compile(r"""OC\s*=\s*["']\d{3,}["']|["']1365["']""")
    for d in ("src", "scripts", "data"):
        for p in (ROOT / d).rglob("*.py"):
            if pat.search(p.read_text(encoding="utf-8", errors="replace")):
                offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, f"하드코딩 OC 키 의심: {offenders}"


# ── (c) PII 검사기 동작 ─────────────────────────────────────────
def test_s0_4_pii_scan_catches_synthetic_and_passes_clean():
    from pii_scan import scan_text

    dirty = (
        "홍길동 900101-1234567 연락처 010-1234-5678\n"
        "메일 test@example.com 사업자 123-45-67890\n"
        "과제 HI22C1234 와 RS-2024-00000000"
    )
    kinds = {k for k, _ in scan_text(dirty)}
    for expect in ["주민등록번호", "휴대전화", "이메일", "사업자등록번호", "과제번호(HI)", "과제번호(RS)"]:
        assert expect in kinds, (expect, kinds)

    clean = "학생인건비 상한은 얼마인가요? 간접비 비율은 20퍼센트 이내입니다."
    assert scan_text(clean) == []


# ── R3: 하이픈 없는 PII 탐지 복원 + 해시 오탐 0 ──────────────────
def test_s0_4_pii_scan_detects_hyphenless_and_ignores_hashes():
    import json

    from pii_scan import scan_text

    # 하이픈 없는 주민번호(유효 날짜)·휴대전화 탐지
    kinds = {k for k, _ in scan_text("홍길동 9001011234567 연락 01012345678")}
    assert "주민등록번호" in kinds and "휴대전화" in kinds
    # 날짜가 아닌 13자리(월 99)는 주민번호로 오탐하지 않음
    assert "주민등록번호" not in {k for k, _ in scan_text("코드 9099011234567")}
    # SHA-256 해시가 든 스냅샷 줄은 PII로 오탐하지 않음
    snaps = list((ROOT / "data" / "snapshots").glob("*.json"))
    if snaps:
        text = snaps[0].read_text(encoding="utf-8")
        assert scan_text(text) == []


# ── (d) .gitignore 보호 항목 ────────────────────────────────────
def test_s0_gitignore_contains_secrets_and_restricted():
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gi
    assert "_restricted" in gi
