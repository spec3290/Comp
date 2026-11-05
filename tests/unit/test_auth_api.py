"""소셜 로그인 API (F1-6) + 로그인 때 토큰 발급 (F1-4). fixture 는 conftest.py 에 있다."""

from urllib.parse import parse_qs, urlsplit

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.repository import LoginCodeStore, TokenStore, UserRepository
from app.auth.service import AuthService, build_providers, get_auth_service
from app.auth.tokens import TokenIssuer
from app.core.config import Settings
from app.core.time import now_kst
from app.main import app
from tests.unit.auth_support import (
    ACCESS_TTL,
    FRONTEND_URL,
    OUTAGE_CODE,
    REFRESH_TTL,
    SECRET,
    FakeProvider,
    callback,
    claims,
    frontend_params,
    login,
    start_state,
    user_count,
)


@pytest.mark.feature("F1-6")
def test_login_redirects_to_provider_with_state_cookie(client: TestClient) -> None:
    response = client.get("/auth/google/login", follow_redirects=False)

    assert response.status_code == 302
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    assert len(state) >= 32
    cookie = response.headers["set-cookie"]
    assert f"oauth_state={state}" in cookie
    assert "HttpOnly" in cookie
    assert "Path=/auth" in cookie
    assert "SameSite=lax" in cookie


@pytest.mark.feature("F1-6")
def test_state_cookie_is_secure_when_callback_is_https(
    session: Session, redis: fakeredis.FakeRedis
) -> None:
    issuer = TokenIssuer(TokenStore(redis), SECRET, ACCESS_TTL, REFRESH_TTL)
    provider = FakeProvider({}, redirect_uri="https://api.example.com/auth/google/callback")
    service = AuthService(
        {"google": provider},
        UserRepository(session),
        issuer,
        login_codes=LoginCodeStore(redis),
        frontend_url=FRONTEND_URL,
    )
    app.dependency_overrides[get_auth_service] = lambda: service
    try:
        response = TestClient(app).get("/auth/google/login", follow_redirects=False)
    finally:
        app.dependency_overrides.clear()

    assert "Secure" in response.headers["set-cookie"]


@pytest.mark.feature("F1-6")
@pytest.mark.parametrize(
    ("client_id", "client_secret", "expected"),
    [("id", "secret", ["google"]), ("", "secret", []), ("id", "", [])],
    ids=["configured", "no-client-id", "no-client-secret"],
)
def test_only_configured_providers_are_registered(
    client_id: str, client_secret: str, expected: list[str]
) -> None:
    settings = Settings(google_client_id=client_id, google_client_secret=client_secret)

    with httpx.Client() as http:
        providers = build_providers(settings, http)

    assert list(providers) == expected


@pytest.mark.feature("F1-6")
def test_first_login_creates_user_and_needs_onboarding(
    client: TestClient, session: Session
) -> None:
    body = login(client)

    assert body["token_type"] == "bearer"
    assert body["expires_in"] == ACCESS_TTL.total_seconds()
    assert body["needs_onboarding"] is True
    user = session.scalars(select(User)).one()
    assert (user.provider, user.provider_id) == ("google", "google-sub-a")
    assert claims(body["access_token"])["sub"] == str(user.id)


@pytest.mark.feature("F1-6")
def test_second_login_reuses_existing_user(client: TestClient, session: Session) -> None:
    first = login(client)
    second = login(client)

    assert user_count(session) == 1
    assert claims(first["access_token"])["sub"] == claims(second["access_token"])["sub"]


@pytest.mark.feature("F1-6")
def test_different_provider_accounts_become_different_users(
    client: TestClient, session: Session
) -> None:
    a = login(client, "code-a")
    b = login(client, "code-b")

    assert user_count(session) == 2
    assert claims(a["access_token"])["sub"] != claims(b["access_token"])["sub"]


@pytest.mark.feature("F1-6")
def test_onboarded_user_does_not_need_onboarding(client: TestClient, session: Session) -> None:
    login(client)
    session.scalars(select(User)).one().nickname = "코딩왕"
    session.commit()

    assert login(client)["needs_onboarding"] is False


@pytest.mark.feature("F1-4")
def test_login_issues_access_and_refresh_jwt_with_expiry(client: TestClient) -> None:
    body = login(client)

    access = claims(body["access_token"])
    refresh = claims(body["refresh_token"])
    assert access["type"] == "access"
    assert refresh["type"] == "refresh"
    assert access["jti"] != refresh["jti"]
    assert isinstance(access["exp"], int) and isinstance(access["iat"], int)
    assert access["exp"] - access["iat"] == ACCESS_TTL.total_seconds()
    assert isinstance(refresh["exp"], int) and isinstance(refresh["iat"], int)
    assert refresh["exp"] - refresh["iat"] == REFRESH_TTL.total_seconds()


@pytest.mark.feature("F1-4")
def test_refresh_token_is_stored_in_redis_until_expiry(
    client: TestClient, redis: fakeredis.FakeRedis
) -> None:
    refresh = claims(login(client)["refresh_token"])

    stored = redis.hgetall(f"refresh:{refresh['jti']}")
    assert stored == {"user_id": refresh["sub"], "family_id": refresh["fam"]}
    ttl = redis.ttl(f"refresh:{refresh['jti']}")
    assert REFRESH_TTL.total_seconds() - 5 <= ttl <= REFRESH_TTL.total_seconds()


@pytest.mark.feature("F1-6")
def test_callback_redirects_to_frontend_with_login_code_only(client: TestClient) -> None:
    response = callback(client)

    params = frontend_params(response)
    assert list(params) == ["login_code"]  # 토큰은 URL 에 싣지 않는다
    assert len(params["login_code"]) >= 32
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.feature("F1-6")
def test_login_code_is_exchanged_for_tokens_without_cache(client: TestClient) -> None:
    login_code = frontend_params(callback(client))["login_code"]

    response = client.post("/auth/token", json={"login_code": login_code})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert claims(response.json()["access_token"])["type"] == "access"


@pytest.mark.feature("F1-6")
def test_login_code_can_be_used_only_once(client: TestClient) -> None:
    login_code = frontend_params(callback(client))["login_code"]
    client.post("/auth/token", json={"login_code": login_code})

    response = client.post("/auth/token", json={"login_code": login_code})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "LOGIN_CODE_INVALID"


@pytest.mark.feature("F1-6")
def test_unknown_login_code_returns_401(client: TestClient) -> None:
    response = client.post("/auth/token", json={"login_code": "never-issued"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "LOGIN_CODE_INVALID"


@pytest.mark.feature("F1-6")
def test_login_code_expires_within_a_minute(client: TestClient, redis: fakeredis.FakeRedis) -> None:
    login_code = frontend_params(callback(client))["login_code"]

    assert 55 <= redis.ttl(f"login_code:{login_code}") <= 60


@pytest.mark.feature("F1-6")
def test_non_ascii_state_redirects_with_state_mismatch_not_500(client: TestClient) -> None:
    start_state(client)

    response = client.get(
        "/auth/google/callback",
        params={"code": "code-a", "state": "é"},
        follow_redirects=False,
    )

    assert frontend_params(response) == {"error": "OAUTH_STATE_MISMATCH"}


@pytest.mark.feature("F1-6")
@pytest.mark.parametrize("code", ["code-a", "forged"], ids=["success", "failure"])
def test_callback_deletes_state_cookie(client: TestClient, code: str) -> None:
    response = callback(client, code)

    assert 'oauth_state=""' in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.feature("F1-6")
@pytest.mark.parametrize("cookie_state", [None, "other-state"], ids=["no-cookie", "mismatch"])
def test_callback_with_wrong_state_redirects_with_error(
    client: TestClient, session: Session, cookie_state: str | None
) -> None:
    if cookie_state is not None:
        client.cookies.set("oauth_state", cookie_state, path="/auth")

    response = client.get(
        "/auth/google/callback", params={"code": "code-a", "state": "s"}, follow_redirects=False
    )

    assert frontend_params(response) == {"error": "OAUTH_STATE_MISMATCH"}
    assert user_count(session) == 0


@pytest.mark.feature("F1-6")
def test_rejected_code_redirects_with_error_without_creating_user(
    client: TestClient, session: Session
) -> None:
    response = callback(client, "forged")

    assert frontend_params(response) == {"error": "OAUTH_FAILED"}
    assert user_count(session) == 0


@pytest.mark.feature("F1-6")
def test_cancelled_consent_redirects_with_error(client: TestClient) -> None:
    state = start_state(client)

    response = client.get(
        "/auth/google/callback",
        params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )

    assert frontend_params(response) == {"error": "OAUTH_FAILED"}


@pytest.mark.feature("F1-6")
def test_provider_outage_redirects_with_error(client: TestClient) -> None:
    assert frontend_params(callback(client, OUTAGE_CODE)) == {"error": "OAUTH_UNAVAILABLE"}


@pytest.mark.feature("F1-6")
@pytest.mark.parametrize("path", ["/auth/naver/login", "/auth/naver/callback"])
def test_unsupported_provider_returns_404(client: TestClient, path: str) -> None:
    response = client.get(path, follow_redirects=False)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


class RacingUserRepository(UserRepository):
    """처음 조회 직후 다른 요청이 같은 회원을 먼저 만든 상황을 흉내 낸다."""

    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self.raced = False

    def get_by_provider(self, provider: str, provider_id: str) -> User | None:
        if not self.raced:
            self.raced = True
            other = User(provider=provider, provider_id=provider_id, created_at=now_kst())
            self._session.add(other)
            self._session.commit()
            self._session.expunge(other)
            return None
        return super().get_by_provider(provider, provider_id)


@pytest.mark.feature("F1-6")
def test_concurrent_first_login_returns_existing_user(session: Session) -> None:
    repo = RacingUserRepository(session)

    user, created = repo.get_or_create("google", "google-sub-a", now_kst())

    assert created is False
    assert (user.provider, user.provider_id) == ("google", "google-sub-a")
    assert user_count(session) == 1


@pytest.mark.feature("F1-6")
@pytest.mark.parametrize(
    ("origin", "allowed"),
    [("http://localhost:5173", True), ("http://evil.example", False)],
    ids=["frontend", "other-site"],
)
def test_only_frontend_origin_can_call_api_from_browser(
    client: TestClient, origin: str, allowed: bool
) -> None:
    response = client.options(
        "/auth/token",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )

    assert (response.headers.get("access-control-allow-origin") == origin) is allowed
