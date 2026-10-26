"""소셜 로그인 (F1-6) + 토큰 재발급 (F1-4).

로그인은 두 단계다. 시작 때 무작위 state 를 만들어 제공자 동의 화면으로 보내고, 콜백에서
쿠키로 보관한 state 와 비교해 CSRF 를 막은 뒤 인가 코드를 회원 식별자로 바꾼다.
최초 로그인이면 `provider + provider_id` 로 회원을 만든다 (자동 가입, ADR-0003).
재발급은 Refresh 를 회전하고 같은 family 로 새 토큰 쌍을 발급한다 (ADR-0002).
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Annotated

import httpx
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.oauth import GoogleConfig, GoogleOAuth, OAuthFailedError, OAuthProvider
from app.auth.repository import RefreshTokenStore, UserRepository
from app.auth.tokens import IssuedTokens, TokenClaims, TokenInvalidError, TokenIssuer
from app.core.config import Settings
from app.core.db import get_settings
from app.core.errors import AppError, NotFoundError
from app.core.kv import get_redis
from app.core.sql import get_session
from app.core.time import now_kst

logger = logging.getLogger(__name__)


class OAuthStateMismatchError(AppError):
    status_code = 400
    code = "OAUTH_STATE_MISMATCH"


@dataclass(frozen=True)
class LoginStart:
    redirect_url: str
    state: str
    secure_cookie: bool


@dataclass(frozen=True)
class LoginResult:
    tokens: IssuedTokens
    needs_onboarding: bool


class AuthService:
    def __init__(
        self,
        providers: Mapping[str, OAuthProvider],
        users: UserRepository,
        issuer: TokenIssuer,
    ) -> None:
        self._providers = providers
        self._users = users
        self._issuer = issuer

    def start_login(self, provider_name: str) -> LoginStart:
        provider = self._provider(provider_name)
        state = secrets.token_urlsafe(32)
        return LoginStart(
            redirect_url=provider.authorization_url(state),
            state=state,
            # state 쿠키는 콜백 주소로 돌아올 때 실려야 하므로 프록시 뒤에서도 콜백 scheme 을 따른다
            secure_cookie=provider.redirect_uri.startswith("https://"),
        )

    def complete_login(
        self,
        provider_name: str,
        code: str | None,
        state: str | None,
        expected_state: str | None,
    ) -> LoginResult:
        provider = self._provider(provider_name)
        # str 끼리는 ASCII 만 비교할 수 있어 비ASCII state 가 500 이 되지 않게 바이트로 비교한다
        if (
            not state
            or not expected_state
            or not secrets.compare_digest(state.encode(), expected_state.encode())
        ):
            raise OAuthStateMismatchError("로그인 요청이 만료되었거나 올바르지 않습니다.")
        if not code:
            # 사용자가 동의 화면에서 취소하면 code 없이 error 만 돌아온다
            raise OAuthFailedError("로그인이 취소되었습니다.")
        identity = provider.exchange_code(code)
        now = now_kst()
        user, created = self._users.get_or_create(identity.provider, identity.provider_id, now)
        if created:
            logger.info("신규 회원 가입: user_id=%d provider=%s", user.id, identity.provider)
        tokens = self._issuer.issue(user.id, now)
        return LoginResult(tokens=tokens, needs_onboarding=user.needs_onboarding)

    def refresh(self, refresh_token: str) -> IssuedTokens:
        record = self._issuer.rotate(refresh_token)
        if self._users.get(record.user_id) is None:
            raise TokenInvalidError("유효하지 않은 토큰입니다.")  # 탈퇴한 회원 (F1-8)
        return self._issuer.issue(record.user_id, now_kst(), family_id=record.family_id)

    def _provider(self, name: str) -> OAuthProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise NotFoundError(f"지원하지 않거나 설정되지 않은 로그인 제공자입니다: {name}")
        return provider


@lru_cache
def get_http_client() -> httpx.Client:
    """제공자 호출용 클라이언트. 프로세스당 하나를 재사용하고 종료 때 닫는다."""
    return httpx.Client(timeout=10.0)


def close_http_client() -> None:
    if get_http_client.cache_info().currsize:
        get_http_client().close()
        get_http_client.cache_clear()


def build_providers(settings: Settings, client: httpx.Client) -> dict[str, OAuthProvider]:
    """클라이언트 ID·시크릿이 있는 제공자만 등록한다. 없으면 제공자 화면 대신 404 로 바로 알린다."""
    providers: dict[str, OAuthProvider] = {}
    if settings.google_client_id and settings.google_client_secret:
        google = GoogleConfig(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            redirect_uri=settings.google_redirect_uri,
        )
        providers[GoogleOAuth.name] = GoogleOAuth(google, client)
    return providers


def get_token_issuer() -> TokenIssuer:
    settings = get_settings()
    return TokenIssuer(
        store=RefreshTokenStore(get_redis()),
        secret=settings.jwt_secret,
        access_ttl=timedelta(minutes=settings.access_token_ttl_minutes),
        refresh_ttl=timedelta(days=settings.refresh_token_ttl_days),
    )


Issuer = Annotated[TokenIssuer, Depends(get_token_issuer)]


def get_auth_service(
    session: Annotated[Session, Depends(get_session)], issuer: Issuer
) -> AuthService:
    return AuthService(
        providers=build_providers(get_settings(), get_http_client()),
        users=UserRepository(session),
        issuer=issuer,
    )


_bearer = HTTPBearer(auto_error=False, description="로그인·재발급으로 받은 Access Token")


def require_access(
    issuer: Issuer,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> TokenClaims:
    """`Authorization: Bearer <access_token>` 을 검증한다. 인증이 필요한 API 의 의존성."""
    if credentials is None:
        raise TokenInvalidError("인증이 필요합니다.")
    return issuer.verify(credentials.credentials, "access")


CurrentToken = Annotated[TokenClaims, Depends(require_access)]
