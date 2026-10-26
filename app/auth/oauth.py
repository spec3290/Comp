"""OAuth 제공자 계약과 구현체 (F1-6). Google/Kakao 구현체가 이 Protocol 을 따른다 (ADR-0003).

인가 코드·제공자 토큰은 로그에 남기지 않는다 (AGENTS.md 하드 규칙).
연결 해제(F1-8)는 제공자 토큰이 필요해 그 기능에서 계약에 추가한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
    provider: str
    provider_id: str


class OAuthProvider(Protocol):
    name: str

    def authorization_url(self, state: str) -> str: ...

    def exchange_code(self, code: str) -> OAuthIdentity: ...


@dataclass(frozen=True)
class GoogleConfig:
    client_id: str
    client_secret: str
    redirect_uri: str


class GoogleOAuth:
    """Google OpenID Connect. `openid` 범위만 요청해 `sub`(provider_id)만 받는다."""

    name = "google"
    AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105  엔드포인트 주소
    USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

    def __init__(self, config: GoogleConfig, client: httpx.Client) -> None:
        self._config = config
        self._client = client

    def authorization_url(self, state: str) -> str:
        params = {
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "response_type": "code",
            "scope": "openid",
            "state": state,
        }
        return f"{self.AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> OAuthIdentity:
        token = self._request(
            "POST",
            self.TOKEN_URL,
            data={
                "code": code,
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "redirect_uri": self._config.redirect_uri,
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
        return OAuthIdentity(provider=self.name, provider_id=subject)

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        try:
            response = self._client.request(method, url, data=data, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("Google 요청 실패: %s %s (%s)", method, url, type(exc).__name__)
            raise OAuthUnavailableError("Google 로그인 서버에 연결할 수 없습니다.") from exc
        if response.status_code >= 500:
            logger.warning("Google 서버 오류: %s %s → %d", method, url, response.status_code)
            raise OAuthUnavailableError("Google 로그인 서버에 오류가 발생했습니다.")
        if response.status_code >= 400:
            # 본문에 민감 정보는 없지만 코드가 섞일 수 있어 상태 코드만 남긴다
            logger.info("Google 이 요청을 거절: %s %s → %d", method, url, response.status_code)
            raise OAuthFailedError("Google 로그인에 실패했습니다. 다시 시도해 주세요.")
        body = response.json()
        if not isinstance(body, dict):
            raise OAuthUnavailableError("Google 응답 형식이 올바르지 않습니다.")
        return body
