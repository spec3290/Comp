"""인증 의존성. 로그인 사용자가 필요한 엔드포인트는 `CurrentUserId` 로 회원 id 를 받는다.

토큰 검증(서명·만료·블랙리스트)은 `app.auth.service.require_access` 가 한다 (F1-4, F1-5).
테스트는 `app.dependency_overrides[get_current_user_id]` 로 회원을 넣는다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.auth.service import CurrentToken


def get_current_user_id(token: CurrentToken) -> int:
    return token.user_id


CurrentUserId = Annotated[int, Depends(get_current_user_id)]
