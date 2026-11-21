"""Refresh Token HttpOnly 쿠키 (F1-4·F1-5·F1-6, Q-41): 쿠키 발급·회전·삭제, Origin 검사, CORS."""

import json
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.repository import LoginCodeStore, UserRepository, WithdrawStateStore
from app.auth.service import AuthService, get_auth_service
from app.auth.tokens import TokenIssuer
from app.main import app
from tests.unit.auth_support import (
    FRONTEND_ORIGIN,
    REFRESH_COOKIE,
    REFRESH_TTL,
    WITHDRAW_FRONTEND_URL,
    FakeProvider,
    HttpResponse,
    bearer,
    callback,
    claims,
    frontend_params,
    login,
    refresh,
)


def _refresh_cookie(response: HttpResponse) -> SimpleCookie:
    """응답의 Set-Cookie 중 Refresh 쿠키를 속성과 함께 풀어 돌려준다."""
    headers = response.headers
    assert hasattr(headers, "get_list")
    for raw in headers.get_list("set-cookie"):
        cookie = SimpleCookie()
        cookie.load(raw)
        if REFRESH_COOKIE in cookie:
            return cookie
    raise AssertionError("Refresh 쿠키가 없습니다")


def _exchange(client: TestClient) -> HttpResponse:
    """콜백까지 마치고 프론트처럼 로그인 코드를 교환한 응답을 돌려준다."""
    login_code = frontend_params(callback(client))["login_code"]
    return client.post("/auth/token", json={"login_code": login_code})


def _error_code(body: dict[str, object]) -> object:
    """에러 응답의 error.code."""
    error = body["error"]
    assert isinstance(error, dict)
    return error["code"]


@pytest.mark.feature("F1-6")
def test_login_sets_refresh_as_httponly_cookie_not_in_body(client: TestClient) -> None:
    """로그인은 Refresh 를 본문 대신 HttpOnly·SameSite=Lax·Path=/auth 쿠키로 내린다."""
    response = _exchange(client)

    assert response.status_code == 200
    assert "refresh_token" not in json.loads(response.text)
    morsel = _refresh_cookie(response)[REFRESH_COOKIE]
    assert claims(morsel.value)["type"] == "refresh"
    assert morsel["httponly"] is True
    assert morsel["samesite"] == "lax"
    assert morsel["path"] == "/auth"
    assert morsel["max-age"] == str(int(REFRESH_TTL.total_seconds()))
    assert not morsel["secure"]  # http 프론트(로컬)에서는 Secure 를 붙이지 않는다


@pytest.mark.feature("F1-4")
def test_refresh_rotates_cookie_and_returns_access_only(client: TestClient) -> None:
    """재발급은 Access 만 본문에 주고 회전한 Refresh 를 같은 속성의 쿠키로 다시 심는다."""
    old = login(client)["refresh_token"]

    response = client.post("/auth/refresh", headers={"Origin": FRONTEND_ORIGIN})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in"}
    morsel = _refresh_cookie(response)[REFRESH_COOKIE]
    assert morsel.value != old
    assert claims(morsel.value)["fam"] == claims(old)["fam"]
    assert morsel["httponly"] is True and morsel["path"] == "/auth"


@pytest.mark.feature("F1-4")
def test_browser_keeps_refreshing_with_rotated_cookie(client: TestClient) -> None:
    """브라우저처럼 받은 쿠키만으로 재발급을 이어서 할 수 있다."""
    login(client)
    headers = {"Origin": FRONTEND_ORIGIN}

    statuses = [client.post("/auth/refresh", headers=headers).status_code for _ in range(3)]

    assert statuses == [200, 200, 200]


@pytest.mark.feature("F1-4")
@pytest.mark.parametrize(
    "origin", [None, "http://evil.example", "http://frontend.test:8080", "https://frontend.test"]
)
def test_refresh_from_other_origin_returns_403(client: TestClient, origin: str | None) -> None:
    """Origin 이 없거나 프론트 출처와 다르면 쿠키가 있어도 403 `ORIGIN_FORBIDDEN` 이다 (CSRF)."""
    token = login(client)["refresh_token"]

    status, body = refresh(client, token, origin=origin)

    assert status == 403
    assert _error_code(body) == "ORIGIN_FORBIDDEN"
    assert refresh(client, token)[0] == 200  # 거절한 요청은 Refresh 를 회전하지 않았다


@pytest.mark.feature("F1-4")
def test_refresh_body_token_is_ignored(client: TestClient) -> None:
    """예전처럼 본문에 Refresh 를 보내도 쿠키가 없으면 401 이다."""
    token = login(client)["refresh_token"]
    client.cookies.clear()

    response = client.post(
        "/auth/refresh", json={"refresh_token": token}, headers={"Origin": FRONTEND_ORIGIN}
    )

    assert response.status_code == 401
    assert _error_code(response.json()) == "TOKEN_INVALID"


@pytest.mark.feature("F1-5")
def test_logout_deletes_refresh_cookie(client: TestClient) -> None:
    """로그아웃은 Refresh 쿠키를 지운다."""
    access = login(client)["access_token"]

    response = client.post("/auth/logout", headers=bearer(access))

    assert response.status_code == 204
    morsel = _refresh_cookie(response)[REFRESH_COOKIE]
    assert morsel.value == ""
    assert morsel["max-age"] == "0"
    assert morsel["path"] == "/auth"


@pytest.fixture
def https_client(
    client: TestClient,
    session: Session,
    redis: fakeredis.FakeRedis,
    issuer: TokenIssuer,
    provider: FakeProvider,
) -> TestClient:
    """https 프론트 배포처럼 AuthService 만 바꿔 끼운 클라이언트. 정리는 client fixture 가 한다."""
    service = AuthService(
        providers={"google": provider},
        users=UserRepository(session),
        issuer=issuer,
        login_codes=LoginCodeStore(redis),
        withdraw_states=WithdrawStateStore(redis),
        frontend_url="https://frontend.test/auth/callback",
        withdraw_frontend_url=WITHDRAW_FRONTEND_URL,
    )
    app.dependency_overrides[get_auth_service] = lambda: service
    return client


@pytest.mark.feature("F1-6")
def test_refresh_cookie_is_secure_for_https_frontend(https_client: TestClient) -> None:
    """https 프론트면 Refresh 쿠키에 Secure 를 붙인다."""
    location = callback(https_client).headers["location"]
    login_code = parse_qs(urlsplit(location).query)["login_code"][0]

    response = https_client.post("/auth/token", json={"login_code": login_code})

    assert response.status_code == 200
    assert _refresh_cookie(response)[REFRESH_COOKIE]["secure"] is True


@pytest.mark.feature("F1-6")
def test_cors_allows_credentials_only_for_frontend(client: TestClient) -> None:
    """프론트 출처의 재발급 preflight 에만 credentials 를 허용한다."""
    preflight = {"Access-Control-Request-Method": "POST"}

    frontend = client.options(
        "/auth/refresh", headers={"Origin": "http://localhost:5173", **preflight}
    )
    other = client.options("/auth/refresh", headers={"Origin": "http://evil.example", **preflight})

    assert frontend.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert frontend.headers.get("access-control-allow-credentials") == "true"
    assert other.headers.get("access-control-allow-origin") is None
