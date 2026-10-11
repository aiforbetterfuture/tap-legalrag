"""
law_rag.prompts — 프롬프트 로더.

프롬프트를 코드가 아닌 prompts/ 폴더의 텍스트 파일에서 읽는다.
→ 프롬프트 수정 시 코드를 건드리지 않는다. 버전 관리·논문 부록에 유리.

사용:
    from law_rag.prompts import load
    policy = load("system_policy")
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# prompts/ 폴더 위치: 패키지 루트(src/law_rag)의 상위/상위 = 프로젝트 루트/prompts
_PROMPT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"


@lru_cache(maxsize=32)
def load(name: str) -> str:
    """이름(확장자 제외)으로 프롬프트 파일을 읽는다. 결과는 캐시된다.

    없으면 FileNotFoundError. 프롬프트 누락은 조용히 넘기면 안 되는
    치명적 설정 오류이므로 예외를 그대로 올린다."""
    path = _PROMPT_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(
            f"프롬프트 파일 없음: {path}. prompts/ 폴더에 {name}.txt를 두세요."
        )
    return path.read_text(encoding="utf-8").strip()


def prompt_dir() -> Path:
    """프롬프트 폴더 경로(테스트·디버깅용)."""
    return _PROMPT_DIR
