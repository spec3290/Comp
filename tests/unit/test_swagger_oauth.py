"""Swagger Authorize 로 하는 소셜 로그인 (F1-6, Q-40). fixture 는 conftest.py 에 있다."""

import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.repository import LoginCodeStore, UserRepository, WithdrawStateStore
from app.auth.router import add_swagger_oauth
from app.auth.service import (
    AuthService,
    UserService,
    get_auth_service,
    get_token_issuer,
    get_user_service,
    swagger_redirect_url,
)
from app.auth.tokens import TokenIssuer
from app.core.config import Settings
from app.main import app
from tests.unit.auth_support import (
    FRONTEND_URL,
    WITHDRAW_FRONTEND_URL,
    FakeProvider,
    HttpResponse,
    claims,
    frontend_params,
    user_count,
)

SWAGGER_URL = "http://testserver/docs/oauth2-redirect"
SWAGGER_STATE = "U3dhZ2dlciBzdGF0ZQ=="  # Swagger UI 처럼 base64 로 만든 state


@pytest.fixture
def swagger_client(
    session: Session, redis: fakeredis.FakeRedis, issuer: TokenIssuer, provider: FakeProvider
) -> Iterator[TestClient]:
    """Swagger 로그인을 켠 API 클라이언트."""
    service = AuthService(
        providers={"google": provider},
        users=UserRepository(session),
        issuer=issuer,
        login_codes=LoginCodeStore(redis),
        withdraw_states=WithdrawStateStore(redis),
        frontend_url=FRONTEND_URL,
        withdraw_frontend_url=WITHDRAW_FRONTEND_URL,
        swagger_redirect_url=SWAGGER_URL,
    )
    app.dependency_overrides[get_auth_service] = lambda: service
    app.dependency_overrides[get_token_issuer] = lambda: issuer
    app.dependency_overrides[get_user_service] = lambda: UserService(UserRepository(session))
    yield TestClient(app)
    app.dependency_overrides.clear()


def swagger_start(client: TestClient, **params: str) -> HttpResponse:
    """Swagger Authorize 팝업이 여는 로그인 시작 요청."""
    query = {"response_type": "code", "client_id": "swagger", "redirect_uri": SWAGGER_URL}
    query["state"] = SWAGGER_STATE
    return client.get("/auth/google/login", params={**query, **params}, follow_redirects=False)


def swagger_callback(client: TestClient, code: str = "code-a") -> dict[str, str]:
    """Swagger 로 시작해 제공자 콜백까지 마치고 Swagger 콜백 페이지로 가는 쿼리를 돌려준다."""
    start = swagger_start(client)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    response = client.get(
        "/auth/google/callback", params={"code": code, "state": state}, follow_redirects=False
    )
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(f"{SWAGGER_URL}?"), location
    return {key: values[0] for key, values in parse_qs(urlsplit(location).query).items()}


def error_code(response: HttpResponse) -> str:
    """ADR-0004 에러 응답의 코드."""
    body: dict[str, Any] = json.loads(response.text)
    return str(body["error"]["code"])


@pytest.mark.feature("F1-6")
def test_swagger_login_stores_swagger_state_in_cookie(swagger_client: TestClient) -> None:
    """Swagger 로 시작하면 제공자로 보내면서 Swagger 의 state 를 쿠키에 담는다."""
    response = swagger_start(swagger_client)

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://accounts.example.com/")
    assert "oauth_swagger_state=" in response.headers["set-cookie"]


@pytest.mark.feature("F1-6")
def test_swagger_login_returns_code_to_swagger_and_issues_tokens(
    swagger_client: TestClient,
) -> None:
    """콜백은 Swagger 콜백 페이지로 code·state 를 넘기고, tokenUrl 이 그 code 를 토큰으로 바꾼다."""
    params = swagger_callback(swagger_client)

    assert params["state"] == SWAGGER_STATE
    response = swagger_client.post(
        "/auth/swagger/token",
        data={"grant_type": "authorization_code", "code": params["code"], "client_id": "swagger"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["token_type"] == "bearer"
    assert claims(body["access_token"])["type"] == "access"


@pytest.mark.feature("F1-6")
def test_swagger_token_works_for_protected_api(swagger_client: TestClient) -> None:
    """Swagger 로 받은 Access Token 으로 인증이 필요한 API 를 부를 수 있다."""
    code = swagger_callback(swagger_client)["code"]
    token = swagger_client.post(
        "/auth/swagger/token", data={"grant_type": "authorization_code", "code": code}
    ).json()["access_token"]

    response = swagger_client.get("/users/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text


@pytest.mark.feature("F1-6")
def test_swagger_login_failure_returns_error_to_swagger(
    swagger_client: TestClient, session: Session
) -> None:
    """제공자가 거절하면 회원을 만들지 않고 Swagger 콜백 페이지로 error·state 를 넘긴다."""
    params = swagger_callback(swagger_client, "forged")

    assert params == {"error": "OAUTH_FAILED", "state": SWAGGER_STATE}
    assert user_count(session) == 0


@pytest.mark.feature("F1-6")
def test_swagger_login_is_rejected_when_disabled(client: TestClient) -> None:
    """Swagger 로그인이 꺼져 있으면 redirect_uri 를 붙인 로그인 시작은 400 이다."""
    response = swagger_start(client)

    assert response.status_code == 400
    assert error_code(response) == "SWAGGER_OAUTH_INVALID"


@pytest.mark.feature("F1-6")
@pytest.mark.parametrize(
    "params",
    [
        {"redirect_uri": "https://evil.example.com/docs/oauth2-redirect"},
        {"redirect_uri": "http://testserver/docs/oauth2-redirect/../x"},
        {"state": "<script>"},
        {"state": ""},
    ],
    ids=["other-origin", "other-path", "bad-state", "empty-state"],
)
def test_swagger_login_rejects_other_redirect_or_bad_state(
    swagger_client: TestClient, params: dict[str, str]
) -> None:
    """이 서버의 Swagger 콜백 페이지가 아닌 주소나 이상한 state 는 받지 않는다 (오픈 리다이렉트)."""
    response = swagger_start(swagger_client, **params)

    assert response.status_code == 400
    assert error_code(response) == "SWAGGER_OAUTH_INVALID"


@pytest.mark.feature("F1-6")
def test_frontend_login_after_abandoned_swagger_login_goes_to_frontend(
    swagger_client: TestClient,
) -> None:
    """Swagger 로 시작하다 멈춘 뒤 프론트에서 로그인하면 콜백은 프론트로 간다."""
    swagger_start(swagger_client)
    start = swagger_client.get("/auth/google/login", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]

    response = swagger_client.get(
        "/auth/google/callback", params={"code": "code-a", "state": state}, follow_redirects=False
    )

    assert "login_code" in frontend_params(response)


@pytest.mark.feature("F1-6")
def test_swagger_token_is_rejected_when_disabled(client: TestClient) -> None:
    """Swagger 로그인이 꺼져 있으면 tokenUrl 은 400 이다."""
    response = client.post(
        "/auth/swagger/token", data={"grant_type": "authorization_code", "code": "x"}
    )

    assert response.status_code == 400
    assert error_code(response) == "SWAGGER_OAUTH_INVALID"


@pytest.mark.feature("F1-6")
def test_swagger_token_rejects_other_grant_type(swagger_client: TestClient) -> None:
    """authorization_code 가 아닌 grant_type 은 400 이다."""
    code = swagger_callback(swagger_client)["code"]

    response = swagger_client.post(
        "/auth/swagger/token", data={"grant_type": "password", "code": code}
    )

    assert response.status_code == 400
    assert error_code(response) == "SWAGGER_OAUTH_INVALID"


@pytest.mark.feature("F1-6")
def test_swagger_code_can_be_used_only_once(swagger_client: TestClient) -> None:
    """Swagger 가 받은 code 도 일회용 로그인 코드라 두 번째 교환은 401 이다."""
    form = {"grant_type": "authorization_code", "code": swagger_callback(swagger_client)["code"]}
    swagger_client.post("/auth/swagger/token", data=form)

    response = swagger_client.post("/auth/swagger/token", data=form)

    assert response.status_code == 401
    assert error_code(response) == "LOGIN_CODE_INVALID"


@pytest.mark.feature("F1-6")
@pytest.mark.parametrize(
    ("enabled", "expected"),
    [(True, "https://api.example.com/docs/oauth2-redirect"), (False, None)],
    ids=["enabled", "disabled"],
)
def test_swagger_redirect_url_follows_registered_callback_origin(
    enabled: bool, expected: str | None
) -> None:
    """Swagger 콜백 주소는 Host 헤더가 아니라 콘솔에 등록한 콜백 주소의 출처로 정한다."""
    settings = Settings(
        swagger_oauth_enabled=enabled,
        google_redirect_uri="https://api.example.com/auth/google/callback",
    )

    assert swagger_redirect_url(settings) == expected


@pytest.mark.feature("F1-6")
def test_openapi_adds_oauth2_scheme_to_bearer_operations() -> None:
    """Bearer 를 받는 API 에만 제공자 OAuth2 스킴을 대안으로 붙인다."""
    schema: dict[str, Any] = {
        "paths": {
            "/users/me": {"get": {"security": [{"HTTPBearer": []}]}},
            "/health": {"get": {}},
        }
    }

    result = add_swagger_oauth(schema, ["google"])

    scheme = result["components"]["securitySchemes"]["googleOAuth2"]
    flow = scheme["flows"]["authorizationCode"]
    assert flow["authorizationUrl"] == "/auth/google/login"
    assert flow["tokenUrl"] == "/auth/swagger/token"
    assert result["paths"]["/users/me"]["get"]["security"] == [
        {"HTTPBearer": []},
        {"googleOAuth2": []},
    ]
    assert "security" not in result["paths"]["/health"]["get"]


@pytest.mark.feature("F1-6")
def test_openapi_is_unchanged_without_providers() -> None:
    """Swagger 로그인이 꺼져 제공자가 없으면 스키마를 바꾸지 않는다."""
    schema: dict[str, Any] = {"paths": {"/users/me": {"get": {"security": [{"HTTPBearer": []}]}}}}

    assert add_swagger_oauth(schema, []) == {
        "paths": {"/users/me": {"get": {"security": [{"HTTPBearer": []}]}}}
    }
