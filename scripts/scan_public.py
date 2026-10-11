"""
scripts/scan_public.py — 공개 후보 폴더 흔적 검사 (작업지시서 26 §1-3).

왜: 공개 폴더에 비밀키·개인정보·내부 작업 흔적(모델명·'Cowork'·'Claude Code'·handoff)·절대경로·
    엑셀 속성이 남으면 안 된다. 이 스크립트는 **찾아서 목록만 보고**한다 — 자동 삭제하지 않는다(사용자 판단).
블라인드 원칙: 민감어는 '어떤 종류가 어디에' 있는지만 보고하고 **원문은 출력하지 않는다**.
사용: python scripts/scan_public.py [--dest ../tap-legalrag-public]
종료코드: 심각(비밀·PII·엑셀) 발견 시 1, 흔적(검토 필요)만 있으면 0 — 어느 쪽이든 목록 출력.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from pii_scan import scan_text  # noqa: E402  (원문 미출력 보장)

TEXT_EXT = {".txt", ".csv", ".json", ".md", ".xml", ".tsv", ".py", ".yml", ".yaml", ".toml", ".html"}

# '설명 가능'(공개해도 되는 자기참조·공개정보) — 심각 집계에서 제외하고 사유와 함께 보고.
#   탐지 도구는 그 단어가 '패턴'이라 포함될 수밖에 없다. 법령 XML은 정부 공개 연락처, 합성 테스트는 가짜 값.
_TOOLING = {"scripts/export_public.py", "scripts/scan_public.py", "scripts/pii_scan.py",
            "scripts/manual_overlap.py", "scripts/check_labels.py"}


def _explained(grp: str, rel: str) -> str | None:
    r = rel.replace("\\", "/")
    if r in _TOOLING:
        return "탐지 도구 패턴 정의(자기참조)"
    if grp == "pii":
        if r.startswith("tests/fixtures/") and r.endswith(".xml"):
            return "법령 XML의 정부 공개 연락처(국가법령정보)"
        if r == "tests/test_s0_security.py" or r == "scripts/pii_scan.py":
            return "PII 탐지기의 합성 예시(가짜 값)"
    return None

# 비밀/키 (심각)
SECRET = {
    "OC키_하드코딩": re.compile(r"""OC\s*=\s*["']\d{3,}["']"""),
    "API키_sk": re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    "GitHub토큰": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    "AWS키": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "PEM개인키": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
# 내부 작업 흔적 (검토 필요)
TRACE = {
    "Co-Authored-By": re.compile(r"Co-Authored-By", re.I),
    "ClaudeCode": re.compile(r"Claude\s*Code"),
    "Cowork": re.compile(r"Cowork"),
    "handoff": re.compile(r"handoff"),
    "모델명": re.compile(r"claude-(?:opus|sonnet|haiku)|Opus\s*4|Sonnet\s*4|Haiku\s*4|ChatGPT|GPT-4|gpt-4", re.I),
}
# 절대경로 (검토 필요)
ABSPATH = {
    "Windows절대경로": re.compile(r"[A-Za-z]:\\\\?Users\\\\?|[A-Za-z]:/Users/"),
    "Unix홈경로": re.compile(r"/Users/|/home/[a-z]"),
    # 개발 폴더 경로만 — 법령 이름 '연구개발혁신법'은 정상이므로 'RAG' 접미/경로 구분자와 함께 있을 때만.
    "개발폴더명": re.compile(r"law_rag_kit[/\\]|연구개발혁신법\s*RAG|Downloads[/\\]"),
}


def _scan_file(p: Path):
    kinds = []
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return kinds
    for i, line in enumerate(text.splitlines(), 1):
        for name, pat in SECRET.items():
            if pat.search(line):
                kinds.append(("secret", name, i))
        for name, pat in TRACE.items():
            if pat.search(line):
                kinds.append(("trace", name, i))
        for name, pat in ABSPATH.items():
            if pat.search(line):
                kinds.append(("abspath", name, i))
    for kind, lineno in scan_text(text):  # PII (원문 미출력)
        kinds.append(("pii", kind, lineno))
    return kinds


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(ROOT.parent / "tap-legalrag-public"))
    ap.add_argument("--max-list", type=int, default=40)
    ap.add_argument("--manual-ref", nargs="?", const=str(ROOT / "data" / "guide"),
                    help="매뉴얼 원문 폴더(개발용 data/guide). 공개 후보와 30자 이상 겹침 검사.")
    ap.add_argument("--denylist", help="블라인드 금칙어 목록 파일(실명·소속 등). 내용은 출력하지 않는다.")
    args = ap.parse_args()
    dest = Path(args.dest).resolve()
    if not dest.exists():
        print(f"[중단] 공개 후보 폴더 없음: {dest} — export_public.py 먼저 실행"); return 2

    # 매뉴얼 원문 겹침 검사(공공누리 제4유형): 연속 30자 이상 일치를 파일·건수로 보고(원문 미출력).
    manual_hits = []
    if args.manual_ref:
        from manual_overlap import load_manual_ngrams, overlap_runs
        grams = load_manual_ngrams(Path(args.manual_ref))
        for p in dest.rglob("*"):
            if p.is_file() and p.suffix.lower() in TEXT_EXT:
                try:
                    runs = overlap_runs(p.read_text(encoding="utf-8", errors="replace"), grams)
                except Exception:
                    runs = 0
                if runs:
                    rel = str(p.relative_to(dest))
                    is_law = rel.replace("\\", "/").startswith("tests/fixtures")  # 법령 원문은 별도 검토
                    manual_hits.append((rel, runs, is_law))

    cat = Counter()
    explained_cat = Counter()
    rows = []
    explained_reasons = Counter()
    office = []
    for p in dest.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() in (".xlsx", ".docx", ".pptx"):
            office.append(str(p.relative_to(dest)))
            continue
        if p.suffix.lower() not in TEXT_EXT:
            continue
        rel = str(p.relative_to(dest))
        for grp, name, lineno in _scan_file(p):
            why = _explained(grp, rel)
            if why:
                explained_cat[grp] += 1
                explained_reasons[why] += 1
            else:
                cat[grp] += 1
                rows.append((grp, name, rel, lineno))

    print(f"[scan] 대상: {dest}")
    print(f"[scan] 범주별 건수: {dict(cat)} · 오피스문서(엑셀/워드) {len(office)}개")
    # 종류·위치만(원문 미출력). 범주별로 묶어 일부 표시.
    for grp in ("secret", "pii", "abspath", "trace"):
        g = [r for r in rows if r[0] == grp]
        if not g:
            continue
        print(f"\n== {grp} ({len(g)}건) ==")
        for _, name, rel, lineno in g[:args.max_list]:
            print(f"  [{name}] {rel}:{lineno}")
        if len(g) > args.max_list:
            print(f"  … 외 {len(g) - args.max_list}건")
    if office:
        print(f"\n== 오피스 문서(공개 금지) ==\n  " + "\n  ".join(office))

    # 블라인드 금칙어 검사 (C11) — 단어는 절대 출력하지 않는다. 건수·파일·줄·항목번호만.
    deny_status = "미사용"
    deny_serious = []   # (file, line, item_no) — 법령 XML 외
    deny_explained = 0  # 법령 원문(시행령 등)에 나오는 기관명 등
    if args.denylist:
        dp = Path(args.denylist)
        terms = []
        if dp.exists():
            for raw in dp.read_text(encoding="utf-8", errors="replace").splitlines():
                t = raw.strip()
                if t and not t.startswith("#"):
                    terms.append(t.lower())
        if not terms:
            deny_status = "비어 있음 → NOT VERIFIED (PASS 처리 금지)"
        else:
            deny_status = f"{len(terms)}개 항목으로 검사"
            for p in dest.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in TEXT_EXT:
                    continue
                rel = str(p.relative_to(dest)).replace("\\", "/")
                if rel in _TOOLING:
                    continue
                try:
                    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                except Exception:
                    continue
                law_xml = rel.startswith("tests/fixtures") and rel.endswith(".xml")
                for ln, line in enumerate(lines, 1):
                    low = line.lower()
                    for idx, t in enumerate(terms):
                        if t in low:
                            if law_xml:
                                deny_explained += 1
                            else:
                                deny_serious.append((rel, ln, idx))

    manual_block = [h for h in manual_hits if not h[2]]
    manual_law = [h for h in manual_hits if h[2]]
    if args.manual_ref:
        print(f"\n== 매뉴얼 원문 겹침(연속 30자+) — 매뉴얼 유래 {len(manual_block)}건 · 법령원문(별도검토) {len(manual_law)}건 ==")
        for rel, runs, _ in manual_block[:args.max_list]:
            print(f"  [매뉴얼겹침:{runs}곳] {rel}")
        for rel, runs, _ in manual_law[:10]:
            print(f"  [법령원문겹침:{runs}곳·사람검토] {rel}")

    if explained_cat:
        print(f"\n== 설명 가능(공개 OK, 심각 집계 제외) {sum(explained_cat.values())}건 ==")
        print(f"  범주 {dict(explained_cat)} · 사유 {dict(explained_reasons)}")

    if args.denylist:
        print(f"\n== 블라인드 금칙어 검사 (C11) — 상태: {deny_status} ==")
        print(f"  심각(법령 원문 외) {len(deny_serious)}건 · 설명 가능(법령 XML) {deny_explained}건  [단어는 출력하지 않음]")
        for rel, ln, idx in deny_serious[:args.max_list]:
            print(f"  [금칙어#{idx}] {rel}:{ln}")

    serious = cat["secret"] + cat["pii"] + len(office) + len(manual_block) + len(deny_serious)
    deny_empty = args.denylist and "비어 있음" in deny_status
    print(f"\n[판정] 실제 심각(비밀/PII/오피스/매뉴얼겹침/금칙어)={serious} · 검토필요(흔적/절대경로/법령겹침)={cat['trace']+cat['abspath']+len(manual_law)}")
    print(f"       설명 가능 {sum(explained_cat.values())}건(자기참조·공개정보)은 제외. 매뉴얼 유래 겹침은 공개 전 0건이어야 함.")
    if deny_empty:
        print("       [NOT VERIFIED] denylist가 비어 있어 블라인드 검사가 성립하지 않음 — 공개 전 목록을 채워야 함.")
    print("       ※ 자동 삭제하지 않음. 위 목록을 사용자가 검토해 처리.")
    return 1 if (serious or deny_empty) else 0


if __name__ == "__main__":
    raise SystemExit(main())
