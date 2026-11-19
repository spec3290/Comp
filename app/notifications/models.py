"""알림 기능의 PostgreSQL 테이블 (F4-1, ADR-0008). 대회 자체는 MongoDB 에 있다."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

# 외래 키가 가리키는 users 를 메타데이터에 올린다. 앱(main)을 거치지 않는 dispatch CLI 에도 필요하다
import app.auth.models  # noqa: F401
from app.core.sql import AwareDateTime, Base


class Notification(Base):
    """보낸 서비스 안 알림 한 건 (Q-34). 같은 회원·대회·종류는 한 번만 보낸다.

    대회가 삭제(Q-3)돼도 알림은 남도록 보낸 때의 제목과 날짜를 함께 둔다.
    `contest_id` 는 MongoDB `_id` 문자열을 참조만 한다.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "contest_id", "kind", name="uq_notifications_once"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # 탈퇴(F1-8)하면 회원과 함께 지워진다
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    contest_id: Mapped[str] = mapped_column(String(24))
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(Text)
    target_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime())
    read_at: Mapped[datetime | None] = mapped_column(AwareDateTime())


class NotificationOptOut(Base):
    """회원이 끈 알림 종류 하나 (Q-36). 행이 없으면 그 종류는 켜져 있다."""

    __tablename__ = "notification_opt_outs"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
