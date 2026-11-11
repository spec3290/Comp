"""Google OAuth 구현체 (F1-6). 네트워크 대신 httpx.MockTransport 로 Google 응답을 흉내 낸다."""

from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.auth.oauth import (
    GoogleConfig,
    GoogleOAuth,
    OAuthFailedError,
    OAuthIdentity,
    OAuthUnavailableError,
)

CONFIG = GoogleConfig(
    client_id="client-id",
    client_secret="client-secret",
    redirect_uri="http://localhost:8000/auth/google/callback",
    withdraw_redirect_uri="http://localhost:8000/auth/google/withdraw/callback",
)

Handler = Callable[[httpx.Request], httpx.Response]


def _google(handler: Handler) -> GoogleOAuth:
    """가짜 HTTP 핸들러로 응답하는 GoogleOAuth."""
    return GoogleOAuth(CONFIG, httpx.Client(transport=httpx.MockTransport(handler)))


def _ok(request: httpx.Request) -> httpx.Response:
    """토큰 교환과 사용자 정보 조회에 정상 응답한다."""
    if request.url == GoogleOAuth.TOKEN_URL:
        return httpx.Response(200, json={"access_token": "google-at", "id_token": "x"})
    assert request.headers["Authorization"] == "Bearer google-at"
    return httpx.Response(200, json={"sub": "1234567890"})


@pytest.mark.feature("F1-6")
def test_authorization_url_requests_openid_scope_with_state() -> None:
    """로그인 주소는 openid 범위와 state 를 싣는다."""
    url = _google(_ok).authorization_url("state-abc")

    parts = urlsplit(url)
    params = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == GoogleOAuth.AUTHORIZE_URL
    assert params == {
        "client_id": "client-id",
        "redirect_uri": CONFIG.redirect_uri,
        "response_type": "code",
        "scope": "openid",
        "state": "state-abc",
    }


@pytest.mark.feature("F1-6")
def test_exchange_code_returns_google_subject_as_provider_id() -> None:
    """인가 코드 교환 후 Google sub 를 제공자 ID 로 쓴다."""
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """보낸 요청을 기록하고 정상 응답한다."""
        sent.append(request)
        return _ok(request)

    identity = _google(handler).exchange_code("auth-code")

    assert identity == OAuthIdentity(provider="google", provider_id="1234567890")
    token_form = parse_qs(sent[0].content.decode())
    assert token_form["code"] == ["auth-code"]
    assert token_form["grant_type"] == ["authorization_code"]
    assert token_form["redirect_uri"] == [CONFIG.redirect_uri]


@pytest.mark.feature("F1-6")
def test_rejected_code_raises_oauth_failed() -> None:
    """Google 이 거절한 코드는 OAuthFailedError 다."""
    google = _google(lambda _: httpx.Response(400, json={"error": "invalid_grant"}))

    with pytest.raises(OAuthFailedError):
        google.exchange_code("bad-code")


@pytest.mark.feature("F1-6")
@pytest.mark.parametrize(
    "handler",
    [
        lambda _: httpx.Response(503),
        lambda _: httpx.Response(429),
        lambda _: httpx.Response(200, text="<html>점검 중</html>"),
        lambda _: httpx.Response(200, json={"token_type": "Bearer"}),  # access_token 없음
        lambda r: (
            httpx.Response(200, json={"access_token": "at"})
            if r.url == GoogleOAuth.TOKEN_URL
            else httpx.Response(200, json={})  # sub 없음
        ),
    ],
    ids=["server-error", "rate-limited", "not-json", "no-access-token", "no-subject"],
)
def test_broken_google_response_raises_unavailable(handler: Handler) -> None:
    """Google 응답이 깨지면 OAuthUnavailableError 다."""
    with pytest.raises(OAuthUnavailableError):
        _google(handler).exchange_code("auth-code")


@pytest.mark.feature("F1-6")
def test_network_error_raises_unavailable() -> None:
    """네트워크 오류는 OAuthUnavailableError 다."""

    def handler(request: httpx.Request) -> httpx.Response:
        """연결 오류를 던진다."""
        raise httpx.ConnectError("unreachable", request=request)

    with pytest.raises(OAuthUnavailableError):
        _google(handler).exchange_code("auth-code")


@pytest.mark.feature("F1-8")
def test_withdraw_authorization_url_uses_withdraw_callback_and_account_selection() -> None:
    """탈퇴 재인증 주소는 탈퇴 콜백으로 돌아오고 계정 선택을 다시 거친다."""
    params = parse_qs(urlsplit(_google(_ok).withdraw_authorization_url("state-w")).query)

    assert params["redirect_uri"] == [CONFIG.withdraw_redirect_uri]
    assert params["prompt"] == ["select_account"]
    assert params["state"] == ["state-w"]


@pytest.mark.feature("F1-8")
def test_exchange_withdraw_code_returns_identity_and_hidden_access_token() -> None:
    """탈퇴 코드는 탈퇴 콜백 주소로 교환하고, 받은 토큰은 repr 에 드러나지 않는다."""
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """보낸 요청을 기록하고 정상 응답한다."""
        sent.append(request)
        return _ok(request)

    grant = _google(handler).exchange_withdraw_code("auth-code")

    assert grant.identity == OAuthIdentity(provider="google", provider_id="1234567890")
    assert grant.access_token == "google-at"
    assert "google-at" not in repr(grant)
    assert parse_qs(sent[0].content.decode())["redirect_uri"] == [CONFIG.withdraw_redirect_uri]


@pytest.mark.feature("F1-8")
def test_revoke_posts_token_to_google_revoke_endpoint() -> None:
    """연결 해제는 토큰을 revoke 엔드포인트에 폼으로 보낸다. 빈 본문 성공도 받는다."""
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """보낸 요청을 기록하고 본문 없이 성공한다."""
        sent.append(request)
        return httpx.Response(200)

    _google(handler).revoke("google-at")

    assert sent[0].method == "POST"
    assert sent[0].url == GoogleOAuth.REVOKE_URL
    assert parse_qs(sent[0].content.decode()) == {"token": ["google-at"]}


@pytest.mark.feature("F1-8")
@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(400, json={"error": "invalid_token"}), OAuthFailedError),
        (httpx.Response(503), OAuthUnavailableError),
    ],
    ids=["rejected", "server-error"],
)
def test_revoke_failure_raises_domain_error(
    response: httpx.Response, error: type[Exception]
) -> None:
    """연결 해제 실패는 도메인 예외라 탈퇴를 멈출 수 있다 (Q-7)."""
    with pytest.raises(error):
        _google(lambda _: response).revoke("google-at")
