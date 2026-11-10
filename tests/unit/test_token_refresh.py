"""토큰 재발급 (F1-4): Refresh 회전, 재사용 탐지, 만료·위조 401, Access 검증 의존성."""

from datetime import timedelta

import fakeredis
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import service as service_module
from app.auth.models import User
from app.auth.repository import TokenStore
from app.auth.service import get_token_issuer
from app.auth.tokens import JWT_ALGORITHM, TokenInvalidError, TokenIssuer
from app.core.config import Settings
from app.core.time import now_kst
from app.main import app
from tests.unit.auth_support import REFRESH_TTL, bearer, claims, login


def _refresh(client: TestClient, token: object) -> tuple[int, dict[str, object]]:
    """재발급을 요청하고 상태 코드와 본문을 돌려준다."""
    response = client.post("/auth/refresh", json={"refresh_token": token})
    body: dict[str, object] = response.json()
    return response.status_code, body


def _error_code(body: dict[str, object]) -> object:
    """에러 응답의 error.code."""
    error = body["error"]
    assert isinstance(error, dict)
    return error["code"]


@pytest.mark.feature("F1-4")
def test_refresh_returns_new_pair_in_same_family(client: TestClient) -> None:
    """재발급은 같은 family 의 새 토큰 쌍을 돌려준다."""
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
    """회전된 Refresh Token 은 다시 쓸 수 없다."""
    first = login(client)
    _refresh(client, first["refresh_token"])

    status, body = _refresh(client, first["refresh_token"])

    assert status == 401
    assert _error_code(body) == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
def test_reusing_rotated_refresh_revokes_whole_family(client: TestClient) -> None:
    """회전된 토큰을 재사용하면 family 전체를 폐기한다."""
    first = login(client)
    _, second = _refresh(client, first["refresh_token"])

    _refresh(client, first["refresh_token"])  # 탈취된 이전 토큰으로 재발급 시도
    status, body = _refresh(client, second["refresh_token"])

    assert status == 401  # 정상 사용자가 가진 최신 토큰도 폐기됐다
    assert _error_code(body) == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
def test_reuse_revokes_only_that_family(client: TestClient) -> None:
    """재사용 폐기는 그 family 에만 미친다."""
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
    """만료된 Refresh Token 은 TOKEN_EXPIRED 401 이다."""
    login(client)
    user = session.scalars(select(User)).one()
    stale = issuer.issue(user.id, now_kst() - REFRESH_TTL - timedelta(seconds=1))

    status, body = _refresh(client, stale.refresh_token)

    assert status == 401
    assert _error_code(body) == "TOKEN_EXPIRED"


@pytest.mark.feature("F1-4")
def test_forged_refresh_returns_token_invalid(client: TestClient) -> None:
    """다른 키로 서명한 토큰은 TOKEN_INVALID 401 이다."""
    payload = claims(login(client)["refresh_token"])
    forged = jwt.encode(payload, "attacker-secret-that-is-32-bytes-long", JWT_ALGORITHM)

    status, body = _refresh(client, forged)

    assert status == 401
    assert _error_code(body) == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
@pytest.mark.parametrize("token", ["not-a-jwt", "a.b.c"])
def test_malformed_refresh_returns_token_invalid(client: TestClient, token: str) -> None:
    """형식이 깨진 토큰은 TOKEN_INVALID 401 이다."""
    status, body = _refresh(client, token)

    assert status == 401
    assert _error_code(body) == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
def test_access_token_cannot_be_used_as_refresh(client: TestClient) -> None:
    """Access Token 으로는 재발급할 수 없다."""
    status, body = _refresh(client, login(client)["access_token"])

    assert status == 401
    assert _error_code(body) == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
def test_refresh_missing_from_store_returns_token_invalid(
    client: TestClient, redis: fakeredis.FakeRedis
) -> None:
    """저장소에 없는 Refresh Token 은 TOKEN_INVALID 401 이다."""
    token = login(client)["refresh_token"]
    redis.flushall()

    status, body = _refresh(client, token)

    assert status == 401
    assert _error_code(body) == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
def test_refresh_of_deleted_user_returns_token_invalid(
    client: TestClient, session: Session
) -> None:
    """탈퇴한 회원의 Refresh Token 은 TOKEN_INVALID 401 이다."""
    token = login(client)["refresh_token"]
    session.delete(session.scalars(select(User)).one())
    session.commit()

    status, body = _refresh(client, token)

    assert status == 401
    assert _error_code(body) == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
def test_refresh_without_token_returns_400(client: TestClient) -> None:
    """토큰 없이 재발급하면 400 이다."""
    response = client.post("/auth/refresh", json={})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.feature("F1-4")
def test_concurrent_consume_marks_only_one_as_first_use(redis: fakeredis.FakeRedis) -> None:
    """같은 토큰을 동시에 소비해도 한쪽만 첫 사용이다."""
    store = TokenStore(redis)
    store.save("jti-1", 7, "family-1", REFRESH_TTL)

    first, second = store.consume("jti-1"), store.consume("jti-1")

    assert first is not None and first.reused is False
    assert second is not None and second.reused is True
    assert store.consume("unknown") is None


# Access 검증 의존성: 인증이 필요한 API 가 쓸 `CurrentToken` (protected fixture, conftest.py).


@pytest.mark.feature("F1-4")
def test_valid_access_token_passes(protected: TestClient, issuer: TokenIssuer) -> None:
    """유효한 Access Token 은 통과한다."""
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
    """없거나 잘못된 Access Token 은 TOKEN_INVALID 401 이다."""
    response = protected.get("/protected", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
def test_expired_access_token_returns_token_expired(
    protected: TestClient, issuer: TokenIssuer
) -> None:
    """만료된 Access Token 은 TOKEN_EXPIRED 401 이다."""
    tokens = issuer.issue(42, now_kst() - timedelta(minutes=16))

    response = protected.get("/protected", headers=bearer(tokens.access_token))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


@pytest.mark.feature("F1-4")
def test_refresh_token_cannot_be_used_as_access(protected: TestClient, issuer: TokenIssuer) -> None:
    """Refresh Token 으로는 API 를 호출할 수 없다."""
    tokens = issuer.issue(42, now_kst())

    response = protected.get("/protected", headers=bearer(tokens.refresh_token))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


@pytest.mark.feature("F1-4")
@pytest.mark.parametrize("secret", ["", "dev-only-secret", "x" * 31])
def test_short_jwt_secret_is_rejected(redis: fakeredis.FakeRedis, secret: str) -> None:
    """32바이트보다 짧은 서명 키는 거절한다."""
    with pytest.raises(ValueError, match="32"):
        TokenIssuer(TokenStore(redis), secret, timedelta(minutes=15), REFRESH_TTL)


@pytest.mark.feature("F1-4")
def test_refresh_response_is_not_cached(client: TestClient) -> None:
    """재발급 응답은 캐시하지 않는다."""
    token = login(client)["refresh_token"]

    response = client.post("/auth/refresh", json={"refresh_token": token})

    assert response.headers["cache-control"] == "no-store"


@pytest.mark.feature("F1-4")
def test_refresh_racing_with_reuse_cannot_survive_in_revoked_family(
    client: TestClient, issuer: TokenIssuer, redis: fakeredis.FakeRedis
) -> None:
    """재발급과 재사용 폐기가 겹쳐도 폐기된 family 에 새 토큰이 살아남지 않는다."""
    token = login(client)["refresh_token"]
    assert isinstance(token, str)

    first = issuer.rotate(token)  # 요청 A: 처음 사용, 아직 새 토큰을 발급하기 전
    with pytest.raises(TokenInvalidError):
        issuer.rotate(token)  # 요청 B: 재사용 탐지 → family 폐기

    with pytest.raises(TokenInvalidError):  # 요청 A 가 이어서 발급하려 해도 거절된다
        issuer.issue(first.user_id, now_kst(), family_id=first.family_id)
    assert not redis.smembers(f"refresh_family:{first.family_id}")


@pytest.mark.feature("F1-4")
def test_new_login_family_is_not_affected_by_revoked_family(
    redis: fakeredis.FakeRedis,
) -> None:
    """새 로그인 family 는 폐기된 family 의 영향을 받지 않는다."""
    store = TokenStore(redis)
    store.revoke_family("old-family", REFRESH_TTL)

    assert store.save("jti-new", 1, "new-family", REFRESH_TTL) is True
    assert store.save("jti-old", 1, "old-family", REFRESH_TTL) is False


@pytest.mark.feature("F1-4")
def test_app_refuses_to_start_with_short_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """서명 키가 짧으면 앱이 기동하지 않는다."""
    monkeypatch.setattr(service_module, "get_settings", lambda: Settings(jwt_secret="short"))
    get_token_issuer.cache_clear()

    with pytest.raises(ValueError, match="32"), TestClient(app):
        pass
    get_token_issuer.cache_clear()
