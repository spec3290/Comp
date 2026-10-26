"""토큰 재발급 (F1-4): Refresh 회전, 재사용 탐지, 만료·위조 401, Access 검증 의존성."""

from collections.abc import Iterator
from datetime import timedelta

import fakeredis
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.repository import RefreshTokenStore
from app.auth.service import CurrentToken, get_token_issuer
from app.auth.tokens import JWT_ALGORITHM, TokenIssuer
from app.core.errors import install_error_handlers
from app.core.time import now_kst
from tests.unit.auth_support import REFRESH_TTL, bearer, claims, login


def _refresh(client: TestClient, token: object) -> tuple[int, dict[str, object]]:
    response = client.post("/auth/refresh", json={"refresh_token": token})
    body: dict[str, object] = response.json()
    return response.status_code, body


def _error_code(body: dict[str, object]) -> object:
    error = body["error"]
    assert isinstance(error, dict)
    return error["code"]


@pytest.mark.feature("F1-4")
def test_refresh_returns_new_pair_in_same_family(client: TestClient) -> None:
    first = login(client)

    status, body = _refresh(client, first["refresh_token"])

    assert status == 200
    assert body["access_token"] != first["access_token"]
    assert body["refresh_token"] != first["refresh_token"]
    old, new = claims(first["refresh_token"]), claims(body["refresh_token"])
    assert new["sub"] == old["sub"]
    assert new["fam"] == old["fam"]
    assert claims(body["access_token"])["type"] == "access"
    assert "needs_onboarding" not in body


@pytest.mark.feature("F1-4")
def test_rotated_refresh_cannot_be_used_again(client: TestClient) -> None:
    first = login(client)
    _refresh(client, first["refresh_token"])

    status, body = _refresh(client, first["refresh_token"])

    assert status == 401
    assert _error_code(body) == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
def test_reusing_rotated_refresh_revokes_whole_family(client: TestClient) -> None:
    first = login(client)
    _, second = _refresh(client, first["refresh_token"])

    _refresh(client, first["refresh_token"])  # 탈취된 이전 토큰으로 재발급 시도
    status, body = _refresh(client, second["refresh_token"])

    assert status == 401  # 정상 사용자가 가진 최신 토큰도 폐기됐다
    assert _error_code(body) == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
def test_reuse_revokes_only_that_family(client: TestClient) -> None:
    phone = login(client)
    laptop = login(client)  # 같은 회원의 다른 로그인 = 다른 family
    _refresh(client, phone["refresh_token"])
    _refresh(client, phone["refresh_token"])

    status, _ = _refresh(client, laptop["refresh_token"])

    assert status == 200


@pytest.mark.feature("F1-4")
def test_expired_refresh_returns_token_expired(
    client: TestClient, session: Session, issuer: TokenIssuer
) -> None:
    login(client)
    user = session.scalars(select(User)).one()
    stale = issuer.issue(user.id, now_kst() - REFRESH_TTL - timedelta(seconds=1))

    status, body = _refresh(client, stale.refresh_token)

    assert status == 401
    assert _error_code(body) == "TOKEN_EXPIRED"


@pytest.mark.feature("F1-4")
def test_forged_refresh_returns_token_invalid(client: TestClient) -> None:
    payload = claims(login(client)["refresh_token"])
    forged = jwt.encode(payload, "attacker-secret-that-is-32-bytes-long", JWT_ALGORITHM)

    status, body = _refresh(client, forged)

    assert status == 401
    assert _error_code(body) == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
@pytest.mark.parametrize("token", ["not-a-jwt", "a.b.c"])
def test_malformed_refresh_returns_token_invalid(client: TestClient, token: str) -> None:
    status, body = _refresh(client, token)

    assert status == 401
    assert _error_code(body) == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
def test_access_token_cannot_be_used_as_refresh(client: TestClient) -> None:
    status, body = _refresh(client, login(client)["access_token"])

    assert status == 401
    assert _error_code(body) == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
def test_refresh_missing_from_store_returns_token_invalid(
    client: TestClient, redis: fakeredis.FakeRedis
) -> None:
    token = login(client)["refresh_token"]
    redis.flushall()

    status, body = _refresh(client, token)

    assert status == 401
    assert _error_code(body) == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
def test_refresh_of_deleted_user_returns_token_invalid(
    client: TestClient, session: Session
) -> None:
    token = login(client)["refresh_token"]
    session.delete(session.scalars(select(User)).one())
    session.commit()

    status, body = _refresh(client, token)

    assert status == 401
    assert _error_code(body) == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
def test_refresh_without_token_returns_400(client: TestClient) -> None:
    response = client.post("/auth/refresh", json={})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.feature("F1-4")
def test_concurrent_consume_marks_only_one_as_first_use(redis: fakeredis.FakeRedis) -> None:
    store = RefreshTokenStore(redis)
    store.save("jti-1", 7, "family-1", REFRESH_TTL)

    first, second = store.consume("jti-1"), store.consume("jti-1")

    assert first is not None and first.reused is False
    assert second is not None and second.reused is True
    assert store.consume("unknown") is None


# Access 검증 의존성: 인증이 필요한 API 가 쓸 `CurrentToken` 을 작은 앱에서 확인한다.


@pytest.fixture
def protected(issuer: TokenIssuer) -> Iterator[TestClient]:
    api = FastAPI()
    install_error_handlers(api)

    @api.get("/protected")
    def protected_route(token: CurrentToken) -> dict[str, int]:
        return {"user_id": token.user_id}

    api.dependency_overrides[get_token_issuer] = lambda: issuer
    yield TestClient(api)


@pytest.mark.feature("F1-4")
def test_valid_access_token_passes(protected: TestClient, issuer: TokenIssuer) -> None:
    tokens = issuer.issue(42, now_kst())

    response = protected.get("/protected", headers=bearer(tokens.access_token))

    assert response.status_code == 200
    assert response.json() == {"user_id": 42}


@pytest.mark.feature("F1-4")
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic dXNlcjpwYXNz"}, {"Authorization": "Bearer nope"}],
    ids=["missing", "basic-scheme", "garbage"],
)
def test_missing_or_bad_access_token_returns_token_invalid(
    protected: TestClient, headers: dict[str, str]
) -> None:
    response = protected.get("/protected", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
def test_expired_access_token_returns_token_expired(
    protected: TestClient, issuer: TokenIssuer
) -> None:
    tokens = issuer.issue(42, now_kst() - timedelta(minutes=16))

    response = protected.get("/protected", headers=bearer(tokens.access_token))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


@pytest.mark.feature("F1-4")
def test_refresh_token_cannot_be_used_as_access(protected: TestClient, issuer: TokenIssuer) -> None:
    tokens = issuer.issue(42, now_kst())

    response = protected.get("/protected", headers=bearer(tokens.refresh_token))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
@pytest.mark.parametrize("secret", ["", "dev-only-secret", "x" * 31])
def test_short_jwt_secret_is_rejected(redis: fakeredis.FakeRedis, secret: str) -> None:
    with pytest.raises(ValueError, match="32"):
        TokenIssuer(RefreshTokenStore(redis), secret, timedelta(minutes=15), REFRESH_TTL)


@pytest.mark.feature("F1-4")
def test_refresh_response_is_not_cached(client: TestClient) -> None:
    token = login(client)["refresh_token"]

    response = client.post("/auth/refresh", json={"refresh_token": token})

    assert response.headers["cache-control"] == "no-store"
