"""MongoDB 클라이언트 생성·재사용·정리 테스트."""

import pytest

from app.core import db
from app.core.config import Settings
from app.core.time import KST


@pytest.fixture(autouse=True)
def _reset_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """테스트 설정을 끼우고 캐시된 클라이언트를 비운다."""
    settings = Settings(mongodb_uri="mongodb://localhost:1", mongodb_db="contest_test")
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    db.close_client()


def test_database_uses_configured_name() -> None:
    """설정한 이름의 데이터베이스를 쓴다."""
    assert db.get_database().name == "contest_test"


def test_client_is_reused_and_tz_aware() -> None:
    """클라이언트를 재사용하고 KST tz-aware 로 읽는다."""
    client = db.get_client()
    assert db.get_client() is client
    assert client.codec_options.tz_aware
    assert client.codec_options.tzinfo is KST


def test_close_client_drops_cached_client() -> None:
    """닫으면 다음 호출에 새 클라이언트를 만든다."""
    client = db.get_client()
    db.close_client()
    assert db.get_client() is not client
