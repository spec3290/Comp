"""로그아웃 (F1-5): 이 세션의 Refresh 삭제 + 쓰던 Access 블랙리스트 (Redis, Q-17)."""

from datetime import timedelta

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.auth.repository import TokenStore
from tests.unit.auth_support import ACCESS_TTL, bearer, claims, login


def _logout(client: TestClient, access: object) -> int:
    return client.post("/auth/logout", headers=bearer(access)).status_code


def _refresh(client: TestClient, token: object) -> int:
    return client.post("/auth/refresh", json={"refresh_token": token}).status_code


@pytest.mark.feature("F1-5")
def test_logout_returns_204(client: TestClient) -> None:
    assert _logout(client, login(client)["access_token"]) == 204


@pytest.mark.feature("F1-5")
def test_access_is_rejected_after_logout_until_expiry(
    client: TestClient, protected: TestClient
) -> None:
    access = login(client)["access_token"]
    assert protected.get("/protected", headers=bearer(access)).status_code == 200

    _logout(client, access)
    response = protected.get("/protected", headers=bearer(access))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


@pytest.mark.feature("F1-5")
def test_refresh_of_logged_out_session_is_rejected(client: TestClient) -> None:
    session = login(client)
    _logout(client, session["access_token"])

    assert _refresh(client, session["refresh_token"]) == 401


@pytest.mark.feature("F1-5")
def test_logout_after_refresh_revokes_latest_refresh(client: TestClient) -> None:
    first = login(client)
    rotated = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()

    _logout(client, rotated["access_token"])

    assert _refresh(client, rotated["refresh_token"]) == 401


@pytest.mark.feature("F1-5")
def test_logout_keeps_other_sessions(client: TestClient, protected: TestClient) -> None:
    phone, laptop = login(client), login(client)

    _logout(client, phone["access_token"])

    assert protected.get("/protected", headers=bearer(laptop["access_token"])).status_code == 200
    assert _refresh(client, laptop["refresh_token"]) == 200


@pytest.mark.feature("F1-5")
def test_blacklist_entry_expires_with_access_token(
    client: TestClient, redis: fakeredis.FakeRedis
) -> None:
    access = login(client)["access_token"]

    _logout(client, access)

    ttl = redis.ttl(f"blacklist:{claims(access)['jti']}")
    assert ACCESS_TTL.total_seconds() - 5 <= ttl <= ACCESS_TTL.total_seconds()


@pytest.mark.feature("F1-5")
def test_logout_twice_with_same_access_returns_401(client: TestClient) -> None:
    access = login(client)["access_token"]
    _logout(client, access)

    assert _logout(client, access) == 401


@pytest.mark.feature("F1-5")
def test_logout_without_token_returns_401(client: TestClient) -> None:
    response = client.post("/auth/logout")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


@pytest.mark.feature("F1-5")
@pytest.mark.parametrize("seconds", [-5, 0])
def test_already_expired_access_is_not_stored(redis: fakeredis.FakeRedis, seconds: int) -> None:
    TokenStore(redis).blacklist("expired-access", timedelta(seconds=seconds))

    assert redis.exists("blacklist:expired-access") == 0
