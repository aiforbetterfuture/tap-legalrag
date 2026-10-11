"""
scripts/export_public.py — 공개판 허용목록 추출 (작업지시서 26 §1-2, '시험 추출').

왜: 개발 저장소에는 공개하면 안 되는 것(검수 엑셀·handoff 맥락·CLAUDE.md·내부 경로)이 섞여 있다.
    '무엇을 넣지 않을지'를 코드로 고정해, 허용목록에 든 것만 깨끗한 폴더로 복사한다.
원칙: **복사만 한다**(git init 안 함, 삭제·수정 없음). push·공개 전환은 사용자 승인 후.
사용: python scripts/export_public.py [--dest ../tap-legalrag-public]
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import io
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# 공공누리 제4유형: 매뉴얼 본문은 reduced 모드에서 공개판에 넣지 않는다.
MANUAL_DIR_GLOBS = ["data/guide/*", "data/guide/**/*"]
# reduced: specs는 공개 2개만. 나머지 설계 문서는 내부 맥락(흔적)이 많아 제외.
SPECS_KEEP = {"specs/EVAL_SPEC_v1.md", "specs/TAP_DATA_CONTRACTS_v1.md"}
SPECS_DENY_GLOB = "specs/*"

# 공개용 표기 중립화(P0-5): 내부 코드명·모델명만 정리. AI 활용 사실(PROCESS 기술)은 유지.
import re as _re
_NEUTRALIZE = [
    (_re.compile(r"Claude\s*Code"), "AI 코딩 에이전트"),
    (_re.compile(r"CLAUDE\.md"), "개발 규칙 문서(비공개)"),   # C3
    (_re.compile(r"Cowork"), "작업보조 LLM"),
    (_re.compile(r"ChatGPT"), "검토용 LLM"),
    (_re.compile(r"claude-(?:opus|sonnet|haiku)[\w.-]*", _re.I), "LLM"),
    (_re.compile(r"(?:Opus|Sonnet|Haiku)\s*4\.\d+|GPT-4[\w.-]*"), "LLM"),
    (_re.compile(r"docs/handoff/[0-9A-Za-z_./-]+"), "내부 설계 노트"),
    (_re.compile(r"handoff"), "내부 설계 노트"),
    (_re.compile(r"(?<![A-Za-z])Claude(?!\s*Code)(?![A-Za-z.])"), "LLM"),  # C3 단독 Claude
]
# 중립화에서 제외(탐지 도구: 그 단어가 '패턴'이라 바꾸면 깨진다).
NO_NEUTRALIZE = {
    "scripts/export_public.py", "scripts/scan_public.py", "scripts/pii_scan.py",
    "scripts/manual_overlap.py", "scripts/check_labels.py",
}
NEUTRALIZE_EXT = {".md", ".py", ".txt"}
# reduced 모드에서 셀 단위로 매뉴얼 겹침을 비울 CSV(자유 서술 포함 가능).
REDUCE_CSVS = [
    "data/eval/provenance_gold.csv",
    "data/eval/provenance_review_R1.csv",
    "tests/golden/golden_set_v3_draft_manual.csv",
]
# 매뉴얼 유래 서술 열(이름에 아래 문자열 포함)은 공개판에서 열째 삭제.
DROP_COL_SUBSTR = ("매뉴얼", "발췌", "메모", "체크리스트", "비고")

# 디렉터리 단위 허용(재귀). 아래 DENY에 걸리는 것만 빠진다.
ALLOW_DIRS = ["src", "tests", "scripts", "configs", "data", "results",
              "docs", "prompts", "specs", "examples", ".github"]
# 최상위 허용 파일.
ALLOW_ROOT_FILES = ["README.md", "LICENSE", "NOTICE", "SECURITY.md", "pyproject.toml",
                    "requirements-lock.txt", ".gitignore", ".gitattributes", ".env.example",
                    "demo.py", "DATA_SOURCES.md"]

# 허용 디렉터리 안이라도 제외(개인정보·내부 맥락·잡동사니).
DENY_GLOBS = [
    "docs/handoff/*", "docs/handoff/**/*",     # 이전 대화 맥락(내부)
    "**/*.xlsx", "**/*.docx", "**/~$*",         # 모든 엑셀/워드/임시 잠금파일
    "**/__pycache__/*", "**/*.pyc", "**/.pytest_cache/*",
    "data/fetch_laws.py",                        # 수집 유틸(OC 키 사용) — 재현에 불필요
    "results/*_20.json",                          # 구버전(20건) 보존 산출물 — 공개 불필요
    "**/*.bak", "**/*.egg-info/**", "**/*.egg-info",  # C4: 빌드·백업 잔여물
    "**/.DS_Store", "**/Thumbs.db",
]
# 절대 나가면 안 되는 최상위(방어적 재확인).
DENY_TOP = {"CLAUDE.md", ".env", "_restricted", ".git", ".claude", "law_rag_prototype.html"}


def _denied(rel: str, reduced: bool = False) -> bool:
    parts = rel.replace("\\", "/")
    if parts.split("/")[0] in DENY_TOP:
        return True
    if reduced and fnmatch.fnmatch(parts, SPECS_DENY_GLOB) and parts not in SPECS_KEEP:
        return True
    globs = DENY_GLOBS + (MANUAL_DIR_GLOBS if reduced else [])
    return any(fnmatch.fnmatch(parts, g) for g in globs)


def _neutralize_text(text: str) -> tuple[str, int]:
    n = 0
    for pat, repl in _NEUTRALIZE:
        text, k = pat.subn(repl, text)
        n += k
    return text, n


def _reduce_csv(src: Path, dst: Path, grams) -> dict:
    """reduced 공개판 CSV 변환(공공누리 제4유형).

    - 서술 열(DROP_COL_SUBSTR) 삭제
    - gold 정답표: `context` 열 삭제 + `context_sha256`(개발판 원문 UTF-8 SHA-256) 추가 (C2)
    - 골든셋: `출처`==매뉴얼FAQ 행의 `질문` 비움(자체 작성 유지) (C1)
    - labeler 'Cowork'→'LLM-draft', 매뉴얼 30자 겹침 셀 비움, 셀 내부 코드명·모델명 중립화 (C3)
    """
    import hashlib
    from manual_overlap import cell_overlaps
    name = src.name
    rows = list(csv.DictReader(src.open(encoding="utf-8-sig")))
    if not rows:
        shutil.copy2(src, dst); return {"blanked": 0, "relabeled": 0, "dropped_cols": [], "faq_blanked": 0}
    fields = list(rows[0].keys())
    drop = [c for c in fields if any(s in c for s in DROP_COL_SUBSTR)]
    blanked = relabeled = faq_blanked = 0

    is_gold = name == "provenance_gold.csv" and "context" in fields
    if is_gold:
        drop = list(dict.fromkeys(drop + ["context"]))
    keep = [c for c in fields if c not in drop]
    if is_gold and "context_sha256" not in keep:
        keep = keep + ["context_sha256"]

    for r in rows:
        if is_gold:  # C2: 원문 해시만 남긴다(원문·발췌는 제외)
            ctx = r.get("context", "") or ""
            r["context_sha256"] = hashlib.sha256(ctx.encode("utf-8")).hexdigest() if ctx else ""
        if "질문" in r and (r.get("출처", "") or "").strip() == "매뉴얼FAQ":  # C1
            if r["질문"].strip():
                r["질문"] = ""; faq_blanked += 1
        for k in keep:
            v = r.get(k, "")
            if k == "labeler" and v.strip() == "Cowork":
                r[k] = "LLM-draft"; relabeled += 1
            elif v and len(v) >= 30 and cell_overlaps(v, grams):
                r[k] = ""; blanked += 1
            elif v:
                r[k], _ = _neutralize_text(v)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8-sig", newline="") as fo:
        w = csv.DictWriter(fo, fieldnames=keep, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    return {"blanked": blanked, "relabeled": relabeled, "dropped_cols": drop, "faq_blanked": faq_blanked}


def _collect(reduced: bool = False) -> list[str]:
    out = []
    for d in ALLOW_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(ROOT)).replace("\\", "/")
                if not _denied(rel, reduced):
                    out.append(rel)
    for f in ALLOW_ROOT_FILES:
        if (ROOT / f).exists() and not _denied(f, reduced):
            out.append(f)
    return sorted(set(out))


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(ROOT.parent / "tap-legalrag-public"))
    ap.add_argument("--clean", action="store_true", help="대상 폴더를 먼저 비운다(복사 전)")
    ap.add_argument("--data-mode", default="reduced", choices=["full", "reduced"],
                    help="reduced(기본): 공공누리 제4유형 매뉴얼 제외·겹침 셀 비움 / full: 개발용 그대로")
    args = ap.parse_args()
    reduced = args.data_mode == "reduced"
    dest = Path(args.dest).resolve()
    if ROOT in dest.parents or dest == ROOT:
        print("[중단] 대상이 개발 저장소 안이면 안 됩니다."); return 2

    files = _collect(reduced)
    grams = set()
    if reduced:
        from manual_overlap import load_manual_ngrams
        grams = load_manual_ngrams()
    if args.clean and dest.exists():
        shutil.rmtree(dest)
    reduce_summary = {}
    neutralized = 0
    for rel in files:
        tgt = dest / rel
        tgt.parent.mkdir(parents=True, exist_ok=True)
        if reduced and rel in REDUCE_CSVS:
            reduce_summary[rel] = _reduce_csv(ROOT / rel, tgt, grams)
        elif (reduced and Path(rel).suffix.lower() in NEUTRALIZE_EXT
              and rel not in NO_NEUTRALIZE):
            txt, k = _neutralize_text((ROOT / rel).read_text(encoding="utf-8", errors="replace"))
            tgt.write_text(txt, encoding="utf-8")
            neutralized += k
        else:
            shutil.copy2(ROOT / rel, tgt)

    if reduced:  # C7: 공개판(해설서 제외) 표식 — conftest가 requires_manual을 skip하는 신호
        (dest / "data").mkdir(parents=True, exist_ok=True)
        (dest / "data" / "PUBLIC_REDUCED.txt").write_text(
            "이 사본은 공개용 reduced 추출본입니다(공공누리 제4유형 해설서 원문 제외).\n"
            "requires_manual 테스트는 해설서 문맥이 없어 skip됩니다.\n", encoding="utf-8")

    from collections import Counter
    by_top = Counter(r.split("/")[0] for r in files)
    print(f"[export] 대상: {dest}  (data-mode={args.data_mode})")
    print(f"[export] 포함 {len(files)}개 파일 — 상위별: {dict(by_top)}")
    leaked = [r for r in files if _denied(r, reduced)]
    xlsx = [r for r in files if r.endswith((".xlsx", ".docx"))]
    handoff = [r for r in files if r.startswith("docs/handoff")]
    claude = [r for r in files if r.split("/")[0] in DENY_TOP]
    guide = [r for r in files if r.startswith("data/guide")]
    print(f"[확인] 엑셀/워드={len(xlsx)} · handoff={len(handoff)} · 내부최상위={len(claude)} · 매뉴얼(data/guide)={len(guide)} (reduced면 매뉴얼 0이어야 함)")
    if reduced:
        print(f"[reduced] 매뉴얼 겹침 셀 비움/라벨 치환: {reduce_summary}")
        print(f"[reduced] 표기 중립화 치환: {neutralized}건 · specs 공개: {sorted(SPECS_KEEP)}")
    if not (ROOT / "LICENSE").exists():
        print("[경고] LICENSE 없음 — 공개 전 사용자가 추가(결정: Apache-2.0).")
    if leaked or (reduced and guide):
        print(f"[오류] 제외돼야 하는데 복사됨: {leaked or guide}"); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
