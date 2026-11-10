"""소셜 로그인 (F1-6) + 토큰 재발급 (F1-4) + 로그아웃 (F1-5) + 내 정보·프로필 (F1-7).

로그인은 세 단계다. 시작 때 무작위 state 를 만들어 제공자 동의 화면으로 보내고, 콜백에서
쿠키로 보관한 state 와 비교해 CSRF 를 막은 뒤 인가 코드를 회원 식별자로 바꾼다.
최초 로그인이면 `provider + provider_id` 로 회원을 만든다 (자동 가입, ADR-0003).
콜백은 토큰 대신 일회용 로그인 코드를 붙여 브라우저를 프론트로 보내고, 프론트가 그 코드를
`POST /auth/token` 으로 교환해 토큰을 받는다. URL·브라우저 기록에 토큰이 남지 않는다 (Q-16).
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
from urllib.parse import urlencode

import httpx
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.oauth import (
    GoogleConfig,
    GoogleOAuth,
    OAuthFailedError,
    OAuthProvider,
    OAuthUnavailableError,
)
from app.auth.repository import (
    LoginCodeStore,
    NicknameConflictError,
    TokenStore,
    UserRepository,
)
from app.auth.rules import ProfileInput, ordered_fields
from app.auth.tokens import IssuedTokens, TokenClaims, TokenInvalidError, TokenIssuer
from app.core.config import Settings
from app.core.db import get_settings
from app.core.errors import AppError, NotFoundError
from app.core.kv import get_redis
from app.core.sql import get_session
from app.core.time import now_kst

logger = logging.getLogger(__name__)


class OAuthStateMismatchError(AppError):
    """콜백의 state 가 쿠키와 다를 때 (400)."""

    status_code = 400
    code = "OAUTH_STATE_MISMATCH"


class LoginCodeInvalidError(AppError):
    """로그인 코드가 없거나 만료됐을 때 (401)."""

    status_code = 401
    code = "LOGIN_CODE_INVALID"


# 콜백에서 프론트로 에러 코드를 붙여 돌려보내는 실패. 그 밖의 예외는 그대로 에러 응답이 된다
LOGIN_FAILURES = (OAuthStateMismatchError, OAuthFailedError, OAuthUnavailableError)
LOGIN_CODE_TTL = timedelta(seconds=60)  # 프론트가 리다이렉트를 받고 바로 교환할 시간


class NicknameTakenError(AppError):
    """다른 회원이 쓰는 닉네임일 때 (409)."""

    status_code = 409
    code = "NICKNAME_TAKEN"


@dataclass(frozen=True)
class LoginStart:
    """로그인 시작에 필요한 리다이렉트 주소와 state."""

    redirect_url: str
    state: str
    secure_cookie: bool


@dataclass(frozen=True)
class LoginResult:
    """로그인으로 발급한 토큰과 온보딩 필요 여부."""

    tokens: IssuedTokens
    needs_onboarding: bool


class AuthService:
    """소셜 로그인·토큰 재발급·로그아웃 유스케이스."""

    def __init__(
        self,
        providers: Mapping[str, OAuthProvider],
        users: UserRepository,
        issuer: TokenIssuer,
        login_codes: LoginCodeStore,
        frontend_url: str,
    ) -> None:
        """제공자·저장소·토큰 발급기를 주입받는다."""
        self._providers = providers
        self._users = users
        self._issuer = issuer
        self._login_codes = login_codes
        self._frontend_url = frontend_url

    def start_login(self, provider_name: str) -> LoginStart:
        """state 를 만들고 제공자 로그인 주소를 돌려준다."""
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
    ) -> str:
        """제공자 콜백을 처리하고 브라우저를 보낼 프론트 주소(`?login_code=...`)를 돌려준다.

        실패하면 LOGIN_FAILURES 중 하나를 던진다. 호출한 쪽은 `failed_login_url` 로 보낸다.
        """
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
        user, created = self._users.get_or_create(
            identity.provider, identity.provider_id, now_kst()
        )
        if created:
            logger.info("신규 회원 가입: user_id=%d provider=%s", user.id, identity.provider)
        login_code = secrets.token_urlsafe(32)
        self._login_codes.save(login_code, user.id, LOGIN_CODE_TTL)
        return self._frontend_url_with(login_code=login_code)

    def failed_login_url(self, error_code: str) -> str:
        """로그인 실패를 알릴 프론트 주소 (`?error=OAUTH_FAILED` 등)."""
        return self._frontend_url_with(error=error_code)

    def exchange_login_code(self, login_code: str) -> LoginResult:
        """콜백이 준 일회용 코드를 토큰으로 바꾼다. 코드는 한 번만 쓸 수 있다."""
        user_id = self._login_codes.consume(login_code)
        user = self._users.get(user_id) if user_id is not None else None
        if user is None:
            raise LoginCodeInvalidError("로그인 코드가 만료되었거나 이미 사용되었습니다.")
        tokens = self._issuer.issue(user.id, now_kst())
        return LoginResult(tokens=tokens, needs_onboarding=user.needs_onboarding)

    def refresh(self, refresh_token: str) -> IssuedTokens:
        """Refresh Token 을 회전하고 새 토큰 쌍을 발급한다."""
        record = self._issuer.rotate(refresh_token)
        if self._users.get(record.user_id) is None:
            raise TokenInvalidError("유효하지 않은 토큰입니다.")  # 탈퇴한 회원 (F1-8)
        return self._issuer.issue(record.user_id, now_kst(), family_id=record.family_id)

    def logout(self, access: TokenClaims) -> None:
        """현재 로그인 세션의 토큰을 모두 폐기한다."""
        self._issuer.revoke_session(access, now_kst())
        logger.info("로그아웃: user_id=%d", access.user_id)

    def _provider(self, name: str) -> OAuthProvider:
        """이름으로 설정된 제공자를 찾고 없으면 404."""
        provider = self._providers.get(name)
        if provider is None:
            raise NotFoundError(f"지원하지 않거나 설정되지 않은 로그인 제공자입니다: {name}")
        return provider

    def _frontend_url_with(self, **params: str) -> str:
        """프론트 주소에 쿼리 파라미터를 붙인다."""
        separator = "&" if "?" in self._frontend_url else "?"
        return f"{self._frontend_url}{separator}{urlencode(params)}"


class UserService:
    """회원 정보 조회·프로필 수정 유스케이스."""

    def __init__(self, users: UserRepository) -> None:
        """회원 저장소를 받는다."""
        self._users = users

    def get_me(self, user_id: int) -> User:
        """회원을 찾고 탈퇴했으면 401."""
        user = self._users.get(user_id)
        if user is None:
            raise TokenInvalidError("유효하지 않은 토큰입니다.")  # 토큰은 맞지만 탈퇴한 회원 (F1-8)
        return user

    def update_profile(self, user_id: int, profile: ProfileInput) -> User:
        """온보딩·프로필 수정. 저장하면 needs_onboarding 이 false 가 된다."""
        user = self.get_me(user_id)
        if self._users.nickname_taken(profile.nickname, user.id):
            raise NicknameTakenError("이미 사용 중인 닉네임입니다.")
        try:
            self._users.update_profile(
                user,
                nickname=profile.nickname,
                grade=profile.grade,
                school_type=profile.school_type.value,
                fields=[f.value for f in ordered_fields(profile.interest_fields)],
            )
        except NicknameConflictError as exc:
            raise NicknameTakenError("이미 사용 중인 닉네임입니다.") from exc
        return user


@lru_cache
def get_http_client() -> httpx.Client:
    """제공자 호출용 클라이언트. 프로세스당 하나를 재사용하고 종료 때 닫는다."""
    return httpx.Client(timeout=10.0)


def close_http_client() -> None:
    """만들어 둔 공유 HTTP 클라이언트가 있으면 닫는다."""
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


@lru_cache
def get_token_issuer() -> TokenIssuer:
    """프로세스당 하나. 앱 시작 때 만들어 JWT_SECRET 이 잘못됐으면 바로 실패한다."""
    settings = get_settings()
    return TokenIssuer(
        store=TokenStore(get_redis()),
        secret=settings.jwt_secret,
        access_ttl=timedelta(minutes=settings.access_token_ttl_minutes),
        refresh_ttl=timedelta(days=settings.refresh_token_ttl_days),
    )


Issuer = Annotated[TokenIssuer, Depends(get_token_issuer)]


def get_auth_service(
    session: Annotated[Session, Depends(get_session)], issuer: Issuer
) -> AuthService:
    """요청마다 AuthService 를 조립한다."""
    settings = get_settings()
    return AuthService(
        providers=build_providers(settings, get_http_client()),
        users=UserRepository(session),
        issuer=issuer,
        login_codes=LoginCodeStore(get_redis()),
        frontend_url=settings.frontend_login_redirect_url,
    )


def get_user_service(session: Annotated[Session, Depends(get_session)]) -> UserService:
    """요청마다 UserService 를 조립한다."""
    return UserService(UserRepository(session))


_bearer = HTTPBearer(auto_error=False, description="로그인·재발급으로 받은 Access Token")


def require_access(
    issuer: Issuer,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> TokenClaims:
    """`Authorization: Bearer <access_token>` 을 검증한다. 인증이 필요한 API 의 의존성."""
    if credentials is None:
        raise TokenInvalidError("인증이 필요합니다.")
    return issuer.authenticate(credentials.credentials)


CurrentToken = Annotated[TokenClaims, Depends(require_access)]
