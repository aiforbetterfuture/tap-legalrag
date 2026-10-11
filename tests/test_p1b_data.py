"""
tests/test_p1b_data.py — P1-B 데이터 정비의 완료 기준.

지금 검증 가능한 것(오프라인):
  - manifest.json 형식(필수 키)
  - 모든 fixture 지문(SHA-256) = manifest(M11 원천 무결성)
  - manifest의 모든 법령에서 청크 1개 이상 로드
  - 파서가 version_hash를 채움(P1B-4)
재수집·법령추가 후 활성화(현재 skip):
  - 보건의료 시행령 "법 제N조" → 보건의료기술 진흥법 연결(P1B-3)
  - 진단 M12(공포본 없음) PASS(P1B-2 시행일 기준 재수집)
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from law_rag.knowledge.loader import load_corpus, load_manifest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

REQUIRED_KEYS = {
    "id", "law_id", "law_name", "law_type", "hierarchy_level",
    "api_target", "fixture", "sha256", "enforce_date", "fetched_at",
}


@pytest.fixture(scope="module")
def manifest():
    return load_manifest()


def test_manifest_has_laws(manifest):
    assert manifest.get("laws"), "manifest에 laws가 없음"


def test_manifest_entries_have_required_keys(manifest):
    for e in manifest["laws"]:
        missing = REQUIRED_KEYS - set(e)
        assert not missing, f"{e.get('id')} 필수 키 누락: {missing}"
        assert e["api_target"] in ("eflaw", "law", "admrul"), e["api_target"]
        assert 1 <= e["hierarchy_level"] <= 5


def test_fixture_fingerprints_match_manifest(manifest):
    """M11: 모든 fixture의 실제 SHA-256이 manifest 기록과 일치."""
    for e in manifest["laws"]:
        fp = FIXTURES / e["fixture"]
        assert fp.exists(), f"{e['fixture']} 없음"
        actual = hashlib.sha256(fp.read_bytes()).hexdigest()
        assert actual == e["sha256"], f"{e['fixture']} 지문 불일치"


def test_every_manifest_law_loads_chunks(manifest):
    corpus = load_corpus(FIXTURES, split=True)
    ids = {c.law_id for c in corpus}
    for e in manifest["laws"]:
        assert e["law_id"] in ids, f"{e['law_name']}({e['law_id']}) 청크 0개"


def test_parser_fills_version_hash():
    """P1B-4: 모든 청크에 version_hash(body_text SHA-256)가 채워진다."""
    corpus = load_corpus(FIXTURES, split=True)
    assert all(c.version_hash for c in corpus)
    # 지문이 실제 body_text와 일치하는지 표본 검증
    c = corpus[0]
    assert c.version_hash == hashlib.sha256(c.body_text.encode("utf-8")).hexdigest()


@pytest.mark.skip(reason="P1B-3(보건의료 R&D 법령 추가) 후 활성화")
def test_health_decree_law_reference_resolves_to_health_act():
    """보건의료 시행령 본문의 '법 제N조'가 보건의료기술 진흥법으로 연결(고정 테스트 자리)."""
    ...


@pytest.mark.skip(reason="P1B-2(시행일 기준 재수집)로 공포본 제거 후 M12 PASS")
def test_no_pre_enforcement_version():
    """진단 M12: 모든 fixture 시행일 ≤ 수집일(공포본 없음)."""
    ...
