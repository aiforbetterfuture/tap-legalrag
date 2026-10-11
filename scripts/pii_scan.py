"""
scripts/pii_scan.py — 개인정보·민감정보(PII) 검사기 (S0-4, 기밀성).

왜 필요한가:
    골든셋·문서·데이터에 실제 문의의 개인정보(주민번호·전화·이메일·과제번호 등)가
    섞여 커밋되면, git 이력과 외부 AI 전송 경로로 영구히 샐 수 있다. 커밋 전에 자동으로 막는다.

중요 원칙 — 로그가 유출 경로가 되지 않게:
    탐지된 '원문'은 절대 출력하지 않는다. '종류·파일·줄 번호'만 출력한다.

사용:
    python scripts/pii_scan.py [파일...]      # 지정 파일(없으면 기본 경로) 검사
    python scripts/pii_scan.py --staged       # git 스테이징 파일만 검사(pre-commit 훅용)
종료코드: 발견 시 1, 깨끗하면 0.
"""
from __future__ import annotations

import io
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 탐지 패턴(설정 상수) ───────────────────────────────────────
# R3: 하이픈을 '선택(-?)'으로 둬 하이픈 없는 PII('9001011234567','01012345678')도 잡는다.
# 해시 오탐은 '스캔 전 16진수 토큰 마스킹'(아래)으로 막고, 주민번호는 앞 6자리 날짜 유효성으로 거른다.
PATTERNS: dict[str, re.Pattern] = {
    "주민등록번호": re.compile(r"(?<!\d)\d{6}-?[1-4]\d{6}(?!\d)"),
    "휴대전화": re.compile(r"(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)"),
    "전화번호": re.compile(r"(?<!\d)0\d{1,2}-\d{3,4}-\d{4}(?!\d)"),
    "이메일": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    "사업자등록번호": re.compile(r"(?<!\d)\d{3}-?\d{2}-?\d{5}(?!\d)"),
    "과제번호(HI)": re.compile(r"\bHI\d{2}[A-Z]\d{4}\b"),
    "과제번호(RS)": re.compile(r"\bRS-\d{4}-\d{8}\b"),
}

# 스캔 전 마스킹: 32자 이상 16진수(sha256·version_hash 등)를 공백으로 치환해 숫자열 오탐 제거.
_RE_HEX = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32,}(?![0-9a-fA-F])")


def _mask_hashes(line: str) -> str:
    return _RE_HEX.sub(lambda m: " " * len(m.group()), line)


def _valid_rrn(s: str) -> bool:
    """주민번호 앞 6자리 날짜 유효성(월 01~12, 일 01~31)으로 숫자열 오탐을 줄인다."""
    d = re.sub(r"\D", "", s)[:6]
    if len(d) < 6:
        return False
    mm, dd = int(d[2:4]), int(d[4:6])
    return 1 <= mm <= 12 and 1 <= dd <= 31

# 검사 대상 경로(저장소 기준)와 텍스트 확장자
SCAN_DIRS = ("tests/golden", "docs", "data")
TEXT_EXT = {".txt", ".csv", ".json", ".md", ".xml", ".tsv", ".py"}

# 사용자 사전(기관명·인명). 저장소 밖(민감자료)이라 git에 없음.
USER_DICT = ROOT.parent / "_restricted" / "pii_terms.txt"


def _load_user_terms() -> list[str]:
    if not USER_DICT.exists():
        return []
    terms = []
    for line in USER_DICT.read_text(encoding="utf-8", errors="replace").splitlines():
        t = line.strip()
        if t and not t.startswith("#"):
            terms.append(t)
    return terms


def scan_text(text: str, user_terms: list[str] | None = None) -> list[tuple[str, int]]:
    """텍스트에서 PII 의심 항목을 찾아 (종류, 줄번호) 목록 반환. 원문은 담지 않는다."""
    hits: list[tuple[str, int]] = []
    user_terms = user_terms or []
    for i, line in enumerate(text.splitlines(), start=1):
        masked = _mask_hashes(line)  # 해시 토큰 제거 후 스캔(오탐 방지)
        for kind, pat in PATTERNS.items():
            found = False
            for m in pat.finditer(masked):
                if kind == "주민등록번호" and not _valid_rrn(m.group()):
                    continue
                found = True
                break
            if found:
                hits.append((kind, i))
        for term in user_terms:
            if term and term in line:
                hits.append(("사용자사전", i))
    return hits


def scan_file(path: Path, user_terms: list[str] | None = None) -> list[tuple[str, int]]:
    if path.suffix.lower() not in TEXT_EXT or not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    return scan_text(text, user_terms)


def _staged_files() -> list[Path]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, cwd=ROOT,
    ).stdout.splitlines()
    files = []
    for f in out:
        if any(f.startswith(d) for d in SCAN_DIRS) and Path(f).suffix.lower() in TEXT_EXT:
            files.append(ROOT / f)
    return files


def _default_files() -> list[Path]:
    files: list[Path] = []
    for d in SCAN_DIRS:
        base = ROOT / d
        if base.exists():
            files += [p for p in base.rglob("*") if p.suffix.lower() in TEXT_EXT]
    return files


def main(argv: list[str]) -> int:
    # 콘솔 UTF-8은 '실행 시'에만(import 시 재지정하면 pytest 출력 캡처가 깨진다).
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    staged = "--staged" in argv
    given = [a for a in argv if not a.startswith("--")]
    if staged:
        files = _staged_files()
    elif given:
        files = [Path(g) for g in given]
    else:
        files = _default_files()

    user_terms = _load_user_terms()
    total = 0
    for p in files:
        for kind, lineno in scan_file(p, user_terms):
            rel = p.relative_to(ROOT) if ROOT in p.parents or p == ROOT else p
            print(f"  [PII:{kind}] {rel}:{lineno}")  # 원문 미출력 — 종류·위치만
            total += 1
    if total:
        print(f"PII 의심 {total}건 발견 - 원문은 출력하지 않음(종류·위치만).")
        return 1
    print("PII 검사 통과(의심 없음).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
