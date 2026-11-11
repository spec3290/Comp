"""인증·회원 API 의 요청·응답 스키마."""

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


class TokenOut(BaseModel):
    """토큰 발급 응답."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105  토큰 종류 이름
    expires_in: int  # Access Token 만료까지 남은 초

    @classmethod
    def from_tokens(cls, tokens: IssuedTokens) -> TokenOut:
        """발급한 토큰으로 응답을 만든다."""
        return cls(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_in=tokens.access_expires_in,
        )


class LoginOut(TokenOut):
    """로그인 응답. 토큰과 온보딩 필요 여부를 담는다."""

    needs_onboarding: bool  # 프로필(F1-7) 미설정 신규 회원이면 True

    @classmethod
    def from_result(cls, result: LoginResult) -> LoginOut:
        """로그인 결과로 응답을 만든다."""
        tokens = TokenOut.from_tokens(result.tokens)
        return cls(**tokens.model_dump(), needs_onboarding=result.needs_onboarding)


class LoginCodeIn(BaseModel):
    """일회용 로그인 코드 교환 요청."""

    login_code: str = Field(min_length=1, max_length=128, description="콜백이 프론트로 넘긴 값")


class RefreshIn(BaseModel):
    """토큰 재발급 요청."""

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
        """닉네임을 정규화한다."""
        return normalize_nickname(value)

    def to_input(self) -> ProfileInput:
        """도메인 입력값으로 바꾼다."""
        return ProfileInput(
            nickname=self.nickname,
            grade=self.grade,
            school_type=self.school_type,
            interest_fields=frozenset(self.interest_fields),
        )


class UserOut(BaseModel):
    """회원 정보 응답."""

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
        """회원 모델로 응답을 만든다."""
        return cls(
            id=user.id,
            provider=user.provider,
            nickname=user.nickname,
            grade=user.grade,
            school_type=SchoolType(user.school_type) if user.school_type else None,
            interest_fields=ordered_fields({InterestField(i.field) for i in user.interests}),
            needs_onboarding=user.needs_onboarding,
            created_at=user.created_at,
        )


class WithdrawalOut(BaseModel):
    """탈퇴 시작 응답 (F1-8). 프론트가 브라우저를 보낼 제공자 재인증 주소."""

    redirect_url: str
