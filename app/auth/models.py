"""회원 테이블 (PostgreSQL, ADR-0005). 스키마 변경은 Alembic 마이그레이션과 함께 한다."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.sql import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("provider", "provider_id", name="uq_users_provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    provider_id: Mapped[str] = mapped_column(String(255))
    # 프로필(F1-7). 온보딩 전에는 NULL 이고 nickname 으로 온보딩 여부를 판단한다.
    nickname: Mapped[str | None] = mapped_column(String(50))
    grade: Mapped[str | None] = mapped_column(String(20))
    school_type: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    @property
    def needs_onboarding(self) -> bool:
        return self.nickname is None
