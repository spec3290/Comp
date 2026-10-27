"""회원 테이블 (PostgreSQL, ADR-0005). 스키마 변경은 Alembic 마이그레이션과 함께 한다."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.sql import AwareDateTime, Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_users_provider"),
        UniqueConstraint("nickname", name="uq_users_nickname"),  # NULL 은 여럿 허용된다
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    provider_id: Mapped[str] = mapped_column(String(255))
    # 프로필(F1-7, 값 규칙은 rules.py). 온보딩 전에는 NULL 이고 nickname 으로 온보딩
    # 여부를 판단한다.
    nickname: Mapped[str | None] = mapped_column(String(50))
    grade: Mapped[int | None] = mapped_column(SmallInteger)
    school_type: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(AwareDateTime())  # UTC 저장, KST 로 읽음
    interests: Mapped[list[UserInterestField]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", passive_deletes=True
    )

    @property
    def needs_onboarding(self) -> bool:
        return self.nickname is None


class UserInterestField(Base):
    __tablename__ = "user_interest_fields"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    field: Mapped[str] = mapped_column(String(20), primary_key=True)
