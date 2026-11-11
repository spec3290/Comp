"""OAuth 제공자 계약과 구현체 (F1-6, F1-8). Google/Kakao 구현체가 이 Protocol 을 따른다 (ADR-0003).

인가 코드·제공자 토큰은 로그에 남기지 않는다 (AGENTS.md 하드 규칙).
연결 해제(F1-8)에 쓸 제공자 토큰은 저장하지 않고 탈퇴 때 재인증으로 새로 받는다 (Q-25).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlencode

import httpx

from app.core.errors import AppError

logger = logging.getLogger(__name__)


class OAuthFailedError(AppError):
    """제공자가 인가 코드를 거절했거나 사용자가 동의를 취소했다."""

    status_code = 401
    code = "OAUTH_FAILED"


class OAuthUnavailableError(AppError):
    """제공자에 닿지 않거나 제공자가 서버 오류를 냈다."""

    status_code = 502
    code = "OAUTH_UNAVAILABLE"


@dataclass(frozen=True)
class OAuthIdentity:
    """제공자가 확인해 준 회원 식별자."""

    provider: str
    provider_id: str


@dataclass(frozen=True)
class OAuthGrant:
    """탈퇴 재인증으로 받은 식별자와 연결 해제에 쓸 제공자 Access Token."""

    identity: OAuthIdentity
    access_token: str = field(repr=False)  # 로그·예외 메시지에 찍히지 않게 한다


class OAuthProvider(Protocol):
    """소셜 로그인 제공자 경계. 제공자마다 구현체를 둔다."""

    name: str

    @property
    def redirect_uri(self) -> str:
        """제공자 콘솔에 등록한 콜백 주소."""
        ...

    def authorization_url(self, state: str) -> str:
        """state 를 실은 제공자 로그인 페이지 주소를 만든다."""
        ...

    def exchange_code(self, code: str) -> OAuthIdentity:
        """인가 코드를 회원 식별자로 바꾼다."""
        ...

    def withdraw_authorization_url(self, state: str) -> str:
        """탈퇴 재인증용 로그인 페이지 주소. 계정을 다시 고르게 해 본인 확인을 받는다."""
        ...

    def exchange_withdraw_code(self, code: str) -> OAuthGrant:
        """탈퇴 콜백의 인가 코드를 식별자와 제공자 토큰으로 바꾼다."""
        ...

    def revoke(self, access_token: str) -> None:
        """이 서비스에 준 제공자 동의를 철회한다 (연결 해제)."""
        ...


@dataclass(frozen=True)
class GoogleConfig:
    """Google OAuth 클라이언트 설정."""

    client_id: str
    client_secret: str
    redirect_uri: str
    withdraw_redirect_uri: str  # 탈퇴 재인증 콜백 (F1-8). 콘솔에 따로 등록한다


class GoogleOAuth:
    """Google OpenID Connect. `openid` 범위만 요청해 `sub`(provider_id)만 받는다."""

    name = "google"
    AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105  엔드포인트 주소
    REVOKE_URL = "https://oauth2.googleapis.com/revoke"
    USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

    def __init__(self, config: GoogleConfig, client: httpx.Client) -> None:
        """설정과 공유 HTTP 클라이언트를 받는다."""
        self._config = config
        self._client = client

    @property
    def redirect_uri(self) -> str:
        """Google 콘솔에 등록한 콜백 주소."""
        return self._config.redirect_uri

    def authorization_url(self, state: str) -> str:
        """openid 범위로 Google 로그인 페이지 주소를 만든다."""
        return self._authorization_url(state, self._config.redirect_uri)

    def withdraw_authorization_url(self, state: str) -> str:
        """계정 선택을 다시 거치는 탈퇴 재인증 주소. 자동 로그인으로 본인 확인을 건너뛰지 않는다."""
        return self._authorization_url(
            state, self._config.withdraw_redirect_uri, prompt="select_account"
        )

    def exchange_code(self, code: str) -> OAuthIdentity:
        """인가 코드를 토큰으로 바꾸고 사용자 ID 를 조회한다."""
        return self._exchange(code, self._config.redirect_uri).identity

    def exchange_withdraw_code(self, code: str) -> OAuthGrant:
        """탈퇴 콜백 주소로 받은 인가 코드를 식별자와 Access Token 으로 바꾼다."""
        return self._exchange(code, self._config.withdraw_redirect_uri)

    def revoke(self, access_token: str) -> None:
        """Access Token 을 철회한다. Google 은 같은 동의로 받은 토큰과 앱 연결을 함께 끊는다."""
        self._send("POST", self.REVOKE_URL, data={"token": access_token})

    def _authorization_url(self, state: str, redirect_uri: str, **extra: str) -> str:
        """콜백 주소별 openid 로그인 페이지 주소."""
        params = {
            "client_id": self._config.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid",
            "state": state,
            **extra,
        }
        return f"{self.AUTHORIZE_URL}?{urlencode(params)}"

    def _exchange(self, code: str, redirect_uri: str) -> OAuthGrant:
        """인가 코드를 토큰과 사용자 ID 로 바꾼다. redirect_uri 는 로그인 요청 때와 같아야 한다."""
        token = self._request(
            "POST",
            self.TOKEN_URL,
            data={
                "code": code,
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        access_token = token.get("access_token")
        if not isinstance(access_token, str):
            raise OAuthUnavailableError("Google 응답에 access_token 이 없습니다.")
        userinfo = self._request(
            "GET", self.USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        subject = userinfo.get("sub")
        if not isinstance(subject, str) or not subject:
            raise OAuthUnavailableError("Google 응답에 사용자 식별자가 없습니다.")
        return OAuthGrant(OAuthIdentity(provider=self.name, provider_id=subject), access_token)

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """Google API 를 호출하고 JSON 객체 본문을 돌려준다."""
        response = self._send(method, url, data=data, headers=headers)
        try:
            body = response.json()
        except ValueError as exc:
            raise OAuthUnavailableError("Google 응답 형식이 올바르지 않습니다.") from exc
        if not isinstance(body, dict):
            raise OAuthUnavailableError("Google 응답 형식이 올바르지 않습니다.")
        return body

    def _send(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Google API 를 호출하고 실패를 도메인 예외로 바꾼다."""
        try:
            response = self._client.request(method, url, data=data, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("Google 요청 실패: %s %s (%s)", method, url, type(exc).__name__)
            raise OAuthUnavailableError("Google 로그인 서버에 연결할 수 없습니다.") from exc
        if response.status_code == 429 or response.status_code >= 500:
            # 429 는 코드 거절이 아니라 일시적 요청 제한이라 제공자 장애로 본다
            logger.warning("Google 서버 오류: %s %s → %d", method, url, response.status_code)
            raise OAuthUnavailableError("Google 로그인 서버에 오류가 발생했습니다.")
        if response.status_code >= 400:
            # 본문에 민감 정보는 없지만 코드가 섞일 수 있어 상태 코드만 남긴다
            logger.info("Google 이 요청을 거절: %s %s → %d", method, url, response.status_code)
            raise OAuthFailedError("Google 로그인에 실패했습니다. 다시 시도해 주세요.")
        return response
