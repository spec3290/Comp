"""알림 API 요청·응답 스키마 (ADR-0004 페이지네이션: {items, page, size, total})."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from app.notifications.rules import NotificationKind
from app.notifications.service import NotificationPage, NotificationView


class NotificationOut(BaseModel):
    """내 알림 한 항목 응답. `target_date` 는 알림이 가리키는 마감일·시작일이다."""

    id: int
    contest_id: str
    kind: NotificationKind
    title: str
    message: str
    target_date: date
    created_at: datetime
    read_at: datetime | None  # 안 읽었으면 null

    @classmethod
    def from_view(cls, view: NotificationView) -> NotificationOut:
        """알림 조회 결과로 응답을 만든다."""
        r = view.record
        return cls(
            id=r.id,
            contest_id=r.contest_id,
            kind=r.kind,
            title=r.title,
            message=view.message,
            target_date=r.target_date,
            created_at=r.created_at,
            read_at=r.read_at,
        )


class NotificationPageOut(BaseModel):
    """내 알림 목록 페이지 응답. 알림 아이콘 표시에 쓸 안 읽은 건수를 더한다."""

    items: list[NotificationOut]
    page: int
    size: int
    total: int
    unread_count: int

    @classmethod
    def from_page(cls, page: NotificationPage) -> NotificationPageOut:
        """알림 조회 결과 페이지로 응답을 만든다."""
        return cls(
            items=[NotificationOut.from_view(v) for v in page.items],
            page=page.page,
            size=page.size,
            total=page.total,
            unread_count=page.unread_count,
        )


class NotificationSettingsBody(BaseModel):
    """알림 종류별 켜기·끄기 (Q-36). 요청은 네 값을 모두 보내는 전체 교체다."""

    deadline_d7: bool
    deadline_d3: bool
    deadline_d1: bool
    event_start_d1: bool

    def enabled(self) -> frozenset[NotificationKind]:
        """켜 둔 알림 종류."""
        return frozenset(kind for kind in NotificationKind if getattr(self, kind.value))

    @classmethod
    def from_enabled(cls, enabled: frozenset[NotificationKind]) -> NotificationSettingsBody:
        """켜 둔 알림 종류로 응답을 만든다."""
        return cls(**{kind.value: kind in enabled for kind in NotificationKind})
