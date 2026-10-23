import pytest

from app.core import db
from app.core.config import Settings
from app.core.time import KST


@pytest.fixture(autouse=True)
def _reset_db(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(mongodb_uri="mongodb://localhost:1", mongodb_db="contest_test")
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    db.close_client()


def test_database_uses_configured_name() -> None:
    assert db.get_database().name == "contest_test"


def test_client_is_reused_and_tz_aware() -> None:
    client = db.get_client()
    assert db.get_client() is client
    assert client.codec_options.tz_aware
    assert client.codec_options.tzinfo is KST


def test_close_client_drops_cached_client() -> None:
    client = db.get_client()
    db.close_client()
    assert db.get_client() is not client
