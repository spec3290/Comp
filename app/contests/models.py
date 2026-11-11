"""대회 기능의 PostgreSQL 테이블 (ADR-0008). 대회 자체는 MongoDB 에 있다."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.sql import AwareDateTime, Base


class Bookmark(Base):
    """대회 저장 (F2-7). 2차 알림(F4)의 대상이라 저장 시각을 남긴다.

    `contest_id` 는 MongoDB `_id` 문자열을 참조만 한다. 대회가 삭제(Q-3)돼도 행은 남을 수 있다.
    """

    __tablename__ = "bookmarks"

    # 탈퇴(F1-8)하면 회원과 함께 지워진다
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    contest_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime())


class ContestClick(Base):
    """참가 바로가기 클릭 한 건 (F2-5). 클릭 수는 행을 세어 집계한다.

    비로그인 클릭은 `user_id` 가 NULL 이다. `contest_id` 는 MongoDB `_id` 를 참조만 한다.
    """

    __tablename__ = "contest_clicks"

    id: Mapped[int] = mapped_column(primary_key=True)
    contest_id: Mapped[str] = mapped_column(String(24), index=True)
    # 탈퇴(F1-8)하면 그 회원의 클릭도 지워진다
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    clicked_at: Mapped[datetime] = mapped_column(AwareDateTime())
