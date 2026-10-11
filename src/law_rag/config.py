"""
law_rag.config — 환경변수·.env 로딩 (외부 패키지 없이).

왜 필요한가 (S0-2, 기밀성):
    법제처 OC 키 같은 비밀값을 코드에 하드코딩하면 git 이력에 영구히 남는다.
    .env(= git 제외)에 두고 환경변수로 읽으면, 코드를 공유해도 키가 새지 않는다.
"""
from __future__ import annotations

import os
from pathlib import Path

# 이 파일: src/law_rag/config.py → parents[2] = 프로젝트 루트(law_rag_kit)
_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(root: str | Path | None = None) -> None:
    """프로젝트 루트의 .env를 읽어 환경변수로 올린다(이미 설정된 값은 덮지 않음).
    KEY=VALUE 형식, '#' 주석·빈 줄 무시. .env가 없으면 조용히 통과."""
    f = Path(root or _ROOT) / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get_env(name: str, default: str | None = None) -> str | None:
    load_dotenv()
    return os.environ.get(name, default)


def require_env(name: str, hint: str = "") -> str:
    """필수 환경변수. 없으면 설정 오류를 명확히 알린다(조용한 실패 금지)."""
    val = get_env(name)
    if not val:
        raise RuntimeError(
            f"환경변수 {name}가 없습니다. .env.example을 복사해 .env를 만들고 값을 채우세요."
            + (f" ({hint})" if hint else "")
        )
    return val


def get_law_oc() -> str:
    """법제처 Open API 인증키(OC). .env의 LAW_OC에서 읽는다."""
    return require_env("LAW_OC", "국가법령정보 공동활용 신청 후 발급받은 OC 값")
