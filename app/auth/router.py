"""인증 API: 소셜 로그인 (F1-6, 콜백은 JSON 응답 Q-16), 토큰 재발급 (F1-4), 로그아웃 (F1-5),
내 정보·프로필 (F1-7)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response
from fastapi.responses import RedirectResponse

from app.auth.schemas import LoginOut, ProfileIn, RefreshIn, TokenOut, UserOut
from app.auth.service import (
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


@router.get("/{provider}/callback", response_model=LoginOut)
def callback(
    provider: str,
    response: Response,
    service: Service,
    code: str | None = None,
    state: str | None = None,
    oauth_state: Annotated[str | None, Cookie()] = None,
) -> LoginOut:
    result = service.complete_login(provider, code, state, oauth_state)
    response.delete_cookie(STATE_COOKIE, path="/auth")  # state 는 한 번만 쓴다
    return LoginOut.from_result(result)


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, service: Service) -> TokenOut:
    """Refresh 로 새 토큰 쌍을 받는다. 보낸 Refresh 는 더 이상 쓸 수 없다 (회전)."""
    return TokenOut.from_tokens(service.refresh(body.refresh_token))


@router.post("/logout", status_code=204)
def logout(token: CurrentToken, service: Service) -> Response:
    """이 기기의 로그인을 끝낸다. 다른 기기의 로그인은 유지된다."""
    service.logout(token)
    return Response(status_code=204)


@users_router.get("/me", response_model=UserOut)
def me(token: CurrentToken, users: Users) -> UserOut:
    return UserOut.from_user(users.get_me(token.user_id))


@users_router.put("/me/profile", response_model=UserOut)
def update_profile(body: ProfileIn, token: CurrentToken, users: Users) -> UserOut:
    """온보딩·프로필 수정. 닉네임이 다른 회원과 겹치면 409 `NICKNAME_TAKEN`."""
    return UserOut.from_user(users.update_profile(token.user_id, body.to_input()))
