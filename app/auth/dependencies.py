"""인증 의존성. 로그인 사용자가 필요한 엔드포인트는 `CurrentUserId` 로 회원 id 를 받는다.

인증이 선택인 엔드포인트는 `OptionalUserId` 로 받는다. 비로그인이면 None 이다.

토큰 검증(서명·만료·블랙리스트)은 `app.auth.service.require_access` 가 한다 (F1-4, F1-5).
테스트는 `app.dependency_overrides[get_current_user_id]` 로 회원을 넣는다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.auth.service import CurrentToken, OptionalToken


def get_current_user_id(token: CurrentToken) -> int:
    """검증된 Access Token 에서 회원 ID 만 꺼낸다."""
    return token.user_id


CurrentUserId = Annotated[int, Depends(get_current_user_id)]


def get_optional_user_id(token: OptionalToken) -> int | None:
    """토큰이 있으면 검증한 회원 ID, 없으면 None."""
    return None if token is None else token.user_id


OptionalUserId = Annotated[int | None, Depends(get_optional_user_id)]
