"""인증 API: 소셜 로그인 (F1-6, 콜백은 프론트로 리다이렉트 Q-16), 토큰 재발급 (F1-4),
로그아웃 (F1-5), 내 정보·프로필 (F1-7), 탈퇴 (F1-8, 재인증 후 리다이렉트 Q-25)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response
from fastapi.responses import RedirectResponse

from app.auth.schemas import (
    LoginCodeIn,
    LoginOut,
    ProfileIn,
    RefreshIn,
    TokenOut,
    UserOut,
    WithdrawalOut,
)
from app.auth.service import (
    LOGIN_FAILURES,
    WITHDRAW_FAILURES,
    AuthService,
    CurrentToken,
    UserService,
    get_auth_service,
    get_user_service,
)

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])

STATE_COOKIE = "oauth_state"
STATE_COOKIE_MAX_AGE = 600  # 동의 화면에서 머무를 수 있는 시간(초)

Service = Annotated[AuthService, Depends(get_auth_service)]
Users = Annotated[UserService, Depends(get_user_service)]


@router.get("/{provider}/login", status_code=302, response_class=RedirectResponse)
def login(provider: str, service: Service) -> RedirectResponse:
    """state 쿠키를 심고 제공자 로그인 페이지로 보낸다."""
    start = service.start_login(provider)
    response = RedirectResponse(start.redirect_url, status_code=302)
    response.set_cookie(
        STATE_COOKIE,
        start.state,
        max_age=STATE_COOKIE_MAX_AGE,
        path="/auth",
        httponly=True,
        secure=start.secure_cookie,
        samesite="lax",  # 제공자에서 돌아오는 최상위 GET 이동에는 쿠키가 실린다
    )
    return response


@router.get("/{provider}/callback", status_code=302, response_class=RedirectResponse)
def callback(
    provider: str,
    service: Service,
    code: str | None = None,
    state: str | None = None,
    oauth_state: Annotated[str | None, Cookie()] = None,
) -> RedirectResponse:
    """제공자에서 돌아온 브라우저를 프론트로 보낸다.

    성공은 `?login_code=...`(60초, 한 번만 사용), 실패는 `?error=OAUTH_FAILED` 같은 에러 코드.
    """
    try:
        url = service.complete_login(provider, code, state, oauth_state)
    except LOGIN_FAILURES as exc:
        url = service.failed_login_url(exc.code)
    response = RedirectResponse(url, status_code=302)
    response.delete_cookie(STATE_COOKIE, path="/auth")  # state 는 성공·실패와 관계없이 한 번만 쓴다
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/{provider}/withdraw/callback", status_code=302, response_class=RedirectResponse)
def withdraw_callback(
    provider: str,
    service: Service,
    code: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    """탈퇴 재인증에서 돌아온 브라우저를 프론트로 보낸다.

    성공은 `?withdrawn=true`, 실패는 `?error=OAUTH_UNAVAILABLE` 같은 에러 코드이고 실패하면
    회원은 그대로 남는다 (Q-7).
    """
    try:
        url = service.complete_withdrawal(provider, code, state)
    except WITHDRAW_FAILURES as exc:
        url = service.failed_withdrawal_url(exc.code)
    response = RedirectResponse(url, status_code=302)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/token", response_model=LoginOut)
def exchange_login_code(body: LoginCodeIn, response: Response, service: Service) -> LoginOut:
    """콜백이 프론트로 넘긴 `login_code` 를 토큰으로 바꾼다.

    만료됐거나 이미 쓴 코드는 401 `LOGIN_CODE_INVALID`.
    """
    result = service.exchange_login_code(body.login_code)
    response.headers["Cache-Control"] = "no-store"  # 본문에 토큰이 있어 캐시하지 않는다
    return LoginOut.from_result(result)


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, response: Response, service: Service) -> TokenOut:
    """Refresh 로 새 토큰 쌍을 받는다. 보낸 Refresh 는 더 이상 쓸 수 없다 (회전)."""
    tokens = service.refresh(body.refresh_token)
    response.headers["Cache-Control"] = "no-store"
    return TokenOut.from_tokens(tokens)


@router.post("/logout", status_code=204)
def logout(token: CurrentToken, service: Service) -> Response:
    """이 기기의 로그인을 끝낸다. 다른 기기의 로그인은 유지된다."""
    service.logout(token)
    return Response(status_code=204)


@users_router.get("/me", response_model=UserOut)
def me(token: CurrentToken, users: Users) -> UserOut:
    """로그인한 회원의 정보를 돌려준다."""
    return UserOut.from_user(users.get_me(token.user_id))


@users_router.put("/me/profile", response_model=UserOut)
def update_profile(body: ProfileIn, token: CurrentToken, users: Users) -> UserOut:
    """온보딩·프로필 수정. 닉네임이 다른 회원과 겹치면 409 `NICKNAME_TAKEN`."""
    return UserOut.from_user(users.update_profile(token.user_id, body.to_input()))


@users_router.post("/me/withdrawal", response_model=WithdrawalOut)
def start_withdrawal(token: CurrentToken, service: Service) -> WithdrawalOut:
    """탈퇴를 시작한다. 프론트는 받은 주소로 브라우저를 보내 제공자 재인증을 거친다."""
    return WithdrawalOut(redirect_url=service.start_withdrawal(token.user_id))
