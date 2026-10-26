"""소셜 로그인 API (F1-6). 콜백은 서비스 JWT 를 JSON 으로 돌려준다 (Q-16)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response
from fastapi.responses import RedirectResponse

from app.auth.schemas import TokenOut
from app.auth.service import AuthService, get_auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

STATE_COOKIE = "oauth_state"
STATE_COOKIE_MAX_AGE = 600  # 동의 화면에서 머무를 수 있는 시간(초)

Service = Annotated[AuthService, Depends(get_auth_service)]


@router.get("/{provider}/login", status_code=302, response_class=RedirectResponse)
def login(provider: str, request: Request, service: Service) -> RedirectResponse:
    start = service.start_login(provider)
    response = RedirectResponse(start.redirect_url, status_code=302)
    response.set_cookie(
        STATE_COOKIE,
        start.state,
        max_age=STATE_COOKIE_MAX_AGE,
        path="/auth",
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",  # 제공자에서 돌아오는 최상위 GET 이동에는 쿠키가 실린다
    )
    return response


@router.get("/{provider}/callback", response_model=TokenOut)
def callback(
    provider: str,
    response: Response,
    service: Service,
    code: str | None = None,
    state: str | None = None,
    oauth_state: Annotated[str | None, Cookie()] = None,
) -> TokenOut:
    result = service.complete_login(provider, code, state, oauth_state)
    response.delete_cookie(STATE_COOKIE, path="/auth")  # state 는 한 번만 쓴다
    return TokenOut.from_result(result)
