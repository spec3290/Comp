"""소셜 로그인 (F1-6) + 서비스 JWT 발급 (F1-4, ADR-0002).

로그인은 두 단계다. 시작 때 무작위 state 를 만들어 제공자 동의 화면으로 보내고, 콜백에서
쿠키로 보관한 state 와 비교해 CSRF 를 막은 뒤 인가 코드를 회원 식별자로 바꾼다.
최초 로그인이면 `provider + provider_id` 로 회원을 만든다 (자동 가입, ADR-0003).
"""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Annotated

import httpx
import jwt
from fastapi import Depends
from sqlalchemy.orm import Session

from app.auth.oauth import GoogleConfig, GoogleOAuth, OAuthFailedError, OAuthProvider
from app.auth.repository import RefreshTokenStore, UserRepository
from app.core.config import Settings
from app.core.db import get_settings
from app.core.errors import AppError, NotFoundError
from app.core.kv import get_redis
from app.core.sql import get_session
from app.core.time import now_kst

logger = logging.getLogger(__name__)

JWT_ALGORITHM = "HS256"


class OAuthStateMismatchError(AppError):
    status_code = 400
    code = "OAUTH_STATE_MISMATCH"


@dataclass(frozen=True)
class LoginStart:
    redirect_url: str
    state: str
    secure_cookie: bool


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    access_expires_in: int  # 초


@dataclass(frozen=True)
class LoginResult:
    tokens: IssuedTokens
    needs_onboarding: bool


class TokenIssuer:
    """Access 는 서명만으로 검증하는 짧은 JWT, Refresh 는 jti 를 Redis 에 저장해 회전·폐기한다."""

    def __init__(
        self,
        store: RefreshTokenStore,
        secret: str,
        access_ttl: timedelta,
        refresh_ttl: timedelta,
    ) -> None:
        self._store = store
        self._secret = secret
        self._access_ttl = access_ttl
        self._refresh_ttl = refresh_ttl

    def issue(self, user_id: int, now: datetime, family_id: str | None = None) -> IssuedTokens:
        """새 토큰 쌍. 로그인이면 새 family, 재발급(F1-4)이면 기존 family 를 이어 쓴다."""
        family = family_id or uuid.uuid4().hex
        refresh_jti = uuid.uuid4().hex
        access = self._encode("access", user_id, uuid.uuid4().hex, now, self._access_ttl)
        refresh = self._encode(
            "refresh", user_id, refresh_jti, now, self._refresh_ttl, {"fam": family}
        )
        self._store.save(refresh_jti, user_id, family, self._refresh_ttl)
        return IssuedTokens(
            access_token=access,
            refresh_token=refresh,
            access_expires_in=int(self._access_ttl.total_seconds()),
        )

    def _encode(
        self,
        token_type: str,
        user_id: int,
        jti: str,
        now: datetime,
        ttl: timedelta,
        extra: Mapping[str, str] | None = None,
    ) -> str:
        claims = {
            "sub": str(user_id),
            "jti": jti,
            "type": token_type,
            "iat": now,
            "exp": now + ttl,
            **(extra or {}),
        }
        return jwt.encode(claims, self._secret, algorithm=JWT_ALGORITHM)


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
        if not state or not expected_state or not secrets.compare_digest(state, expected_state):
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


def get_auth_service(session: Annotated[Session, Depends(get_session)]) -> AuthService:
    settings = get_settings()
    issuer = TokenIssuer(
        store=RefreshTokenStore(get_redis()),
        secret=settings.jwt_secret,
        access_ttl=timedelta(minutes=settings.access_token_ttl_minutes),
        refresh_ttl=timedelta(days=settings.refresh_token_ttl_days),
    )
    return AuthService(
        providers=build_providers(settings, get_http_client()),
        users=UserRepository(session),
        issuer=issuer,
    )
