"""소셜 로그인 (F1-6) + 토큰 재발급 (F1-4) + 로그아웃 (F1-5) + 내 정보·프로필 (F1-7) + 탈퇴 (F1-8).

로그인은 세 단계다. 시작 때 무작위 state 를 만들어 제공자 동의 화면으로 보내고, 콜백에서
쿠키로 보관한 state 와 비교해 CSRF 를 막은 뒤 인가 코드를 회원 식별자로 바꾼다.
최초 로그인이면 `provider + provider_id` 로 회원을 만든다 (자동 가입, ADR-0003).
콜백은 토큰 대신 일회용 로그인 코드를 붙여 브라우저를 프론트로 보내고, 프론트가 그 코드를
`POST /auth/token` 으로 교환해 토큰을 받는다. URL·브라우저 기록에 토큰이 남지 않는다 (Q-16).
재발급은 Refresh 를 회전하고 같은 family 로 새 토큰 쌍을 발급한다 (ADR-0002).
Refresh 는 HttpOnly 쿠키로만 오가고, 쿠키로 오는 재발급은 프론트 출처의 요청만 받는다 (Q-41).
탈퇴는 제공자 재인증으로 받은 토큰으로 연결을 해제(revoke)한 뒤 회원을 지운다. 해제가
실패하면 아무것도 지우지 않는다 (Q-7, Q-25).
"""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlencode, urlsplit

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
    WithdrawStateStore,
)
from app.auth.rules import InterestField, ProfileInput, ordered_fields
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


class OAuthAccountMismatchError(AppError):
    """탈퇴 재인증에서 탈퇴를 요청한 회원과 다른 계정을 골랐을 때 (400)."""

    status_code = 400
    code = "OAUTH_ACCOUNT_MISMATCH"


# 콜백에서 프론트로 에러 코드를 붙여 돌려보내는 실패. 그 밖의 예외는 그대로 에러 응답이 된다
LOGIN_FAILURES = (OAuthStateMismatchError, OAuthFailedError, OAuthUnavailableError)
WITHDRAW_FAILURES = (*LOGIN_FAILURES, OAuthAccountMismatchError)
LOGIN_CODE_TTL = timedelta(seconds=60)  # 프론트가 리다이렉트를 받고 바로 교환할 시간
WITHDRAW_STATE_TTL = timedelta(minutes=10)  # 제공자 계정 선택 화면에서 머무를 수 있는 시간
SWAGGER_REDIRECT_PATH = (
    "/docs/oauth2-redirect"  # FastAPI 가 띄우는 Swagger UI 의 OAuth2 콜백 페이지
)
# Swagger UI 의 state 는 base64 문자열이다. 쿠키·쿼리에 그대로 실을 수 있는 문자만 받는다
_SWAGGER_STATE = re.compile(r"[A-Za-z0-9+/=._~-]{1,256}")


class SwaggerOAuthError(AppError):
    """Swagger 로그인이 꺼져 있거나 허용하지 않은 redirect_uri·state 일 때 (400, Q-40)."""

    status_code = 400
    code = "SWAGGER_OAUTH_INVALID"


class OriginForbiddenError(AppError):
    """쿠키로 오는 재발급 요청의 Origin 이 프론트 출처가 아닐 때 (403, Q-41 CSRF 방어)."""

    status_code = 403
    code = "ORIGIN_FORBIDDEN"


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
    swagger_state: str | None = None  # Swagger Authorize 로 시작했을 때 돌려줄 Swagger 의 state


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
        withdraw_states: WithdrawStateStore,
        frontend_url: str,
        withdraw_frontend_url: str,
        swagger_redirect_url: str | None = None,
    ) -> None:
        """제공자·저장소·토큰 발급기를 주입받는다.

        swagger_redirect_url 이 None 이면 Swagger Authorize 로그인은 꺼져 있다 (Q-40).
        """
        self._providers = providers
        self._users = users
        self._issuer = issuer
        self._login_codes = login_codes
        self._withdraw_states = withdraw_states
        self._frontend_url = frontend_url
        self._frontend_origin = _origin(frontend_url)
        self._withdraw_frontend_url = withdraw_frontend_url
        self._swagger_redirect_url = swagger_redirect_url

    def start_login(
        self,
        provider_name: str,
        swagger_redirect_uri: str | None = None,
        swagger_state: str | None = None,
    ) -> LoginStart:
        """state 를 만들고 제공자 로그인 주소를 돌려준다.

        Swagger Authorize 가 보낸 redirect_uri·state 가 있으면 검증해 콜백이 프론트 대신
        Swagger 로 돌아가게 한다 (Q-40).
        """
        provider = self._provider(provider_name)
        if swagger_redirect_uri is not None or swagger_state is not None:
            self._check_swagger(swagger_redirect_uri, swagger_state)
        state = secrets.token_urlsafe(32)
        return LoginStart(
            redirect_url=provider.authorization_url(state),
            state=state,
            # state 쿠키는 콜백 주소로 돌아올 때 실려야 하므로 프록시 뒤에서도 콜백 scheme 을 따른다
            secure_cookie=provider.redirect_uri.startswith("https://"),
            swagger_state=swagger_state,
        )

    def complete_login(
        self,
        provider_name: str,
        code: str | None,
        state: str | None,
        expected_state: str | None,
        swagger_state: str | None = None,
    ) -> str:
        """제공자 콜백을 처리하고 브라우저를 보낼 프론트 주소(`?login_code=...`)를 돌려준다.

        Swagger 로 시작한 로그인이면 Swagger 콜백 페이지(`?code=...&state=...`)를 돌려준다.
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
        swagger_url = self._swagger_return(swagger_state)
        if swagger_url is not None and swagger_state is not None:
            # Swagger UI 는 표준 이름(code)으로 받아 tokenUrl 로 교환한다
            return _with_query(swagger_url, code=login_code, state=swagger_state)
        return _with_query(self._frontend_url, login_code=login_code)

    def failed_login_url(self, error_code: str, swagger_state: str | None = None) -> str:
        """로그인 실패를 알릴 프론트 주소 (`?error=OAUTH_FAILED` 등). Swagger 로그인은 Swagger."""
        swagger_url = self._swagger_return(swagger_state)
        if swagger_url is not None and swagger_state is not None:
            return _with_query(swagger_url, error=error_code, state=swagger_state)
        return _with_query(self._frontend_url, error=error_code)

    def exchange_swagger_code(self, grant_type: str, code: str) -> LoginResult:
        """Swagger Authorize 의 tokenUrl 요청을 처리한다. code 는 콜백이 준 일회용 로그인 코드다."""
        if self._swagger_redirect_url is None:
            raise SwaggerOAuthError("Swagger 로그인이 꺼져 있습니다.")
        if grant_type != "authorization_code":
            raise SwaggerOAuthError("grant_type 은 authorization_code 만 지원합니다.")
        return self.exchange_login_code(code)

    def exchange_login_code(self, login_code: str) -> LoginResult:
        """콜백이 준 일회용 코드를 토큰으로 바꾼다. 코드는 한 번만 쓸 수 있다."""
        user_id = self._login_codes.consume(login_code)
        user = self._users.get(user_id) if user_id is not None else None
        if user is None:
            raise LoginCodeInvalidError("로그인 코드가 만료되었거나 이미 사용되었습니다.")
        tokens = self._issuer.issue(user.id, now_kst())
        return LoginResult(tokens=tokens, needs_onboarding=user.needs_onboarding)

    @property
    def refresh_cookie_secure(self) -> bool:
        """Refresh 쿠키에 Secure 를 붙일지. https 프론트는 https API 만 부르므로 프론트를 따른다."""
        return self._frontend_url.startswith("https://")

    def refresh(self, refresh_token: str | None, origin: str | None) -> IssuedTokens:
        """쿠키로 받은 Refresh Token 을 회전하고 새 토큰 쌍을 발급한다.

        쿠키는 다른 사이트의 요청에도 실릴 수 있어 Origin 이 프론트 출처일 때만 받는다 (Q-41).
        Origin 이 없거나 다르면 403 `ORIGIN_FORBIDDEN`, 쿠키가 없으면 401 `TOKEN_INVALID`.
        """
        if origin is None or origin != self._frontend_origin:
            raise OriginForbiddenError("허용하지 않은 출처의 요청입니다.")
        if not refresh_token:
            raise TokenInvalidError("유효하지 않은 토큰입니다.")
        record = self._issuer.rotate(refresh_token)
        if self._users.get(record.user_id) is None:
            raise TokenInvalidError("유효하지 않은 토큰입니다.")  # 탈퇴한 회원 (F1-8)
        return self._issuer.issue(record.user_id, now_kst(), family_id=record.family_id)

    def logout(self, access: TokenClaims) -> None:
        """현재 로그인 세션의 토큰을 모두 폐기한다."""
        self._issuer.revoke_session(access, now_kst())
        logger.info("로그아웃: user_id=%d", access.user_id)

    def start_withdrawal(self, user_id: int) -> str:
        """탈퇴를 시작한다. 프론트가 브라우저를 보낼 제공자 재인증 주소를 돌려준다."""
        user = self._users.get(user_id)
        if user is None:
            raise TokenInvalidError("유효하지 않은 토큰입니다.")  # 이미 탈퇴한 회원
        provider = self._provider(user.provider)
        state = secrets.token_urlsafe(32)
        self._withdraw_states.save(state, user.id, WITHDRAW_STATE_TTL)
        return provider.withdraw_authorization_url(state)

    def complete_withdrawal(self, provider_name: str, code: str | None, state: str | None) -> str:
        """재인증 콜백을 처리해 연결을 해제하고 회원을 지운다. 프론트 주소를 돌려준다.

        재인증한 계정이 탈퇴를 요청한 회원과 같아야 한다. 연결 해제가 실패하면 회원을 지우지
        않는다 (Q-7). 실패하면 WITHDRAW_FAILURES 중 하나를 던진다.
        """
        provider = self._provider(provider_name)
        user_id = self._withdraw_states.consume(state) if state else None
        user = self._users.get(user_id) if user_id is not None else None
        if user is None or user.provider != provider.name:
            raise OAuthStateMismatchError("탈퇴 요청이 만료되었거나 올바르지 않습니다.")
        if not code:
            raise OAuthFailedError("탈퇴가 취소되었습니다.")
        grant = provider.exchange_withdraw_code(code)
        if grant.identity.provider_id != user.provider_id:
            # 다른 회원일 수 있는 계정의 연결은 끊지 않는다
            raise OAuthAccountMismatchError("탈퇴할 계정과 다른 계정으로 인증했습니다.")
        provider.revoke(grant.access_token)
        self._users.delete(user)
        self._issuer.revoke_user(user.id)
        logger.info("회원 탈퇴: user_id=%d provider=%s", user.id, provider.name)
        return _with_query(self._withdraw_frontend_url, withdrawn="true")

    def failed_withdrawal_url(self, error_code: str) -> str:
        """탈퇴 실패를 알릴 프론트 주소. 회원은 그대로 남아 있다."""
        return _with_query(self._withdraw_frontend_url, error=error_code)

    def _check_swagger(self, redirect_uri: str | None, state: str | None) -> None:
        """Swagger 로그인이 켜져 있고 이 서버의 Swagger 콜백 페이지로만 돌아가는지 확인한다.

        다른 주소를 허용하면 로그인 코드가 외부로 새는 오픈 리다이렉트가 된다.
        """
        if self._swagger_redirect_url is None:
            raise SwaggerOAuthError("Swagger 로그인이 꺼져 있습니다.")
        if redirect_uri != self._swagger_redirect_url:
            raise SwaggerOAuthError("허용하지 않은 redirect_uri 입니다.")
        if state is None or not _SWAGGER_STATE.fullmatch(state):
            raise SwaggerOAuthError("state 가 없거나 올바르지 않습니다.")

    def _swagger_return(self, swagger_state: str | None) -> str | None:
        """Swagger 로 돌려보낼 주소. Swagger 로 시작하지 않았거나 꺼져 있으면 None (프론트로)."""
        if swagger_state is None or not _SWAGGER_STATE.fullmatch(swagger_state):
            return None
        return self._swagger_redirect_url

    def _provider(self, name: str) -> OAuthProvider:
        """이름으로 설정된 제공자를 찾고 없으면 404."""
        provider = self._providers.get(name)
        if provider is None:
            raise NotFoundError(f"지원하지 않거나 설정되지 않은 로그인 제공자입니다: {name}")
        return provider


def swagger_redirect_url(settings: Settings) -> str | None:
    """Swagger 로그인이 켜져 있으면 이 서버의 Swagger 콜백 페이지 주소, 꺼져 있으면 None.

    요청 Host 헤더는 믿지 않고 제공자 콘솔에 등록한 콜백 주소의 출처를 쓴다 (같은 서버다).
    """
    if not settings.swagger_oauth_enabled:
        return None
    callback = urlsplit(settings.google_redirect_uri)
    return f"{callback.scheme}://{callback.netloc}{SWAGGER_REDIRECT_PATH}"


def swagger_oauth_providers() -> list[str]:
    """Swagger Authorize 창에 OAuth2 로 띄울 제공자 이름. 꺼져 있으면 빈 목록."""
    settings = get_settings()
    if swagger_redirect_url(settings) is None:
        return []
    return sorted(build_providers(settings, get_http_client()))


def _origin(url: str) -> str:
    """주소의 출처(scheme://host:port). 브라우저가 Origin 헤더에 싣는 형태다."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _with_query(url: str, **params: str) -> str:
    """프론트 주소에 쿼리 파라미터를 붙인다."""
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(params)}"


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
                fields=[f.value for f in ordered_fields(profile.interest_fields)],
            )
        except NicknameConflictError as exc:
            raise NicknameTakenError("이미 사용 중인 닉네임입니다.") from exc
        return user

    def interest_fields(self, user_id: int) -> frozenset[InterestField]:
        """관심 분야 필터(F3-3)에 쓸 프로필 관심 분야. 비어 있으면 분야 조건이 없다 (Q-24)."""
        return frozenset(InterestField(i.field) for i in self.get_me(user_id).interests)


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
            withdraw_redirect_uri=settings.google_withdraw_redirect_uri,
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
        withdraw_states=WithdrawStateStore(get_redis()),
        frontend_url=settings.frontend_login_redirect_url,
        withdraw_frontend_url=settings.frontend_withdraw_redirect_url,
        swagger_redirect_url=swagger_redirect_url(settings),
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


def optional_access(
    issuer: Issuer,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> TokenClaims | None:
    """인증이 선택인 API 의 의존성. 토큰이 없으면 None, 있으면 검증한다 (만료·위조는 401)."""
    if credentials is None:
        return None
    return issuer.authenticate(credentials.credentials)


OptionalToken = Annotated[TokenClaims | None, Depends(optional_access)]
