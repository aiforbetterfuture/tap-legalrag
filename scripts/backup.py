"""
scripts/backup.py — 백업 (S0-6, 가용성).

환경변수 BACKUP_DIR이 있으면:
  - git bundle(--all): 전체 이력을 파일 하나로 묶어 날짜 이름으로 저장(오프사이트 복구용).
  - data/manifest.json의 지문(sha256) 목록도 함께 저장(원천 무결성 스냅샷).
없으면: 안내만 출력. 프로젝트 폴더는 이동하지 않는다(이동은 사용자가 결정).

실행: python scripts/backup.py
"""
from __future__ import annotations

import datetime
import io
import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):  # Windows 콘솔(CP949) 한글 깨짐 방지
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from law_rag.config import get_env  # noqa: E402


def main() -> int:
    bdir = get_env("BACKUP_DIR")
    if not bdir:
        print("BACKUP_DIR 미설정 — 백업을 건너뜁니다.")
        print("  .env에 BACKUP_DIR=<백업폴더> 를 넣으면 git bundle과 지문 목록을 저장합니다.")
        print("  (수동 예) git bundle create <BACKUP_DIR>/law_rag_YYYYMMDD.bundle --all")
        return 0

    out = Path(bdir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.date.today().strftime("%Y%m%d")

    bundle = out / f"law_rag_{stamp}.bundle"
    r = subprocess.run(["git", "bundle", "create", str(bundle), "--all"], cwd=ROOT)
    if r.returncode != 0:
        print("git bundle 실패 — 커밋이 하나 이상 있는지 확인하세요.")
        return 1
    print(f"번들 저장: {bundle}")

    manifest = ROOT / "data" / "manifest.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        laws = data.get("laws", data if isinstance(data, list) else [])
        fp = out / f"fingerprints_{stamp}.txt"
        lines = [f"{e.get('sha256', '-')}  {e.get('fixture', e.get('id', '?'))}" for e in laws]
        fp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"지문 목록 저장: {fp} ({len(lines)}건)")
    else:
        print("manifest 없음 — 지문 목록은 P1B-1(data/manifest.json) 이후 생성됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
