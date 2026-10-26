from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.auth.service import LoginResult
from app.auth.tokens import IssuedTokens


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105  토큰 종류 이름
    expires_in: int  # Access Token 만료까지 남은 초

    @classmethod
    def from_tokens(cls, tokens: IssuedTokens) -> TokenOut:
        return cls(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_in=tokens.access_expires_in,
        )


class LoginOut(TokenOut):
    needs_onboarding: bool  # 프로필(F1-7) 미설정 신규 회원이면 True

    @classmethod
    def from_result(cls, result: LoginResult) -> LoginOut:
        tokens = TokenOut.from_tokens(result.tokens)
        return cls(**tokens.model_dump(), needs_onboarding=result.needs_onboarding)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=2048)
