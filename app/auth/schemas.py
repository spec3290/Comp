from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.auth.service import LoginResult


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105  토큰 종류 이름
    expires_in: int  # Access Token 만료까지 남은 초
    needs_onboarding: bool  # 프로필(F1-7) 미설정 신규 회원이면 True

    @classmethod
    def from_result(cls, result: LoginResult) -> TokenOut:
        return cls(
            access_token=result.tokens.access_token,
            refresh_token=result.tokens.refresh_token,
            expires_in=result.tokens.access_expires_in,
            needs_onboarding=result.needs_onboarding,
        )
