"""인증 의존성. 로그인 사용자가 필요한 엔드포인트는 `get_current_user_id` 하나로 사용자를 받는다.

Access 토큰 검증(F1-4)이 아직 없어서 지금은 모든 요청을 401 로 거절한다. 토큰 검증을
구현할 때 이 함수만 채우면 되고, 테스트는 `app.dependency_overrides` 로 사용자를 넣는다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import UnauthorizedError

_bearer = HTTPBearer(auto_error=False)


def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> int:
    if credentials is None:
        raise UnauthorizedError("인증이 필요합니다.")
    # TODO(F1-4): Access 토큰의 서명·만료·블랙리스트를 검증하고 sub 를 돌려준다.
    raise UnauthorizedError("유효하지 않은 토큰입니다.")


CurrentUserId = Annotated[int, Depends(get_current_user_id)]
