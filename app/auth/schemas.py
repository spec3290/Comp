from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.auth.models import User
from app.auth.rules import (
    GRADE_MAX,
    GRADE_MIN,
    InterestField,
    ProfileInput,
    SchoolType,
    normalize_nickname,
    ordered_fields,
)
from app.auth.service import LoginResult
from app.auth.tokens import IssuedTokens
from app.core.time import to_kst


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


class ProfileIn(BaseModel):
    """프로필 전체를 바꾼다(PUT). 관심 분야를 비우려면 빈 목록을 보낸다."""

    nickname: str = Field(description="앞뒤 공백 제외 2~12자, 다른 회원과 겹칠 수 없다")
    grade: int | None = Field(default=None, ge=GRADE_MIN, le=GRADE_MAX)
    school_type: SchoolType
    interest_fields: list[InterestField] = Field(
        default_factory=list, max_length=len(InterestField)
    )

    @field_validator("nickname")
    @classmethod
    def _nickname(cls, value: str) -> str:
        return normalize_nickname(value)

    def to_input(self) -> ProfileInput:
        return ProfileInput(
            nickname=self.nickname,
            grade=self.grade,
            school_type=self.school_type,
            interest_fields=frozenset(self.interest_fields),
        )


class UserOut(BaseModel):
    id: int
    provider: str
    nickname: str | None
    grade: int | None
    school_type: SchoolType | None
    interest_fields: list[InterestField]
    needs_onboarding: bool
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> UserOut:
        return cls(
            id=user.id,
            provider=user.provider,
            nickname=user.nickname,
            grade=user.grade,
            school_type=SchoolType(user.school_type) if user.school_type else None,
            interest_fields=ordered_fields({InterestField(i.field) for i in user.interests}),
            needs_onboarding=user.needs_onboarding,
            created_at=to_kst(user.created_at),
        )
