"""알림 발급 (F4-1) + 내 알림 목록·읽음 처리 + 알림 설정.

발급: 하루 한 번(`dispatch.py`) 모든 저장 대회의 최신 날짜로 오늘 보낼 알림을 계산해
서비스 안 알림 목록에 넣는다(Q-34). 같은 회원·대회·종류는 한 번만 보낸다. 저장을 해제했거나
삭제된 대회(Q-3)는 계산 대상에 없으므로 보내지 않는다(Q-32).
설정: 회원이 알림 종류마다 켜고 끈다. 기본은 모두 켜져 있다 (Q-36).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.contests.repository import ContestRepository
from app.core.errors import NotFoundError
from app.core.sql import get_session
from app.notifications.repository import NotificationRecord, NotificationRepository
from app.notifications.rules import ALL_KINDS, ContestDates, NotificationKind, due_notifications
from app.notifications.rules import message as notification_message

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """저장한 대회의 오늘 알림을 만드는 유스케이스 (F4-1)."""

    def __init__(self, notifications: NotificationRepository, contests: ContestRepository) -> None:
        """알림·대회 저장소를 받는다."""
        self._notifications = notifications
        self._contests = contests

    def dispatch(self, today: date, now: datetime) -> int:
        """오늘 보낼 알림을 만들고 새로 만든 건수를 돌려준다. 다시 실행해도 중복되지 않는다."""
        bookmarks = self._notifications.bookmarks()
        records = self._contests.get_many(sorted({contest_id for _, contest_id in bookmarks}))
        opt_outs = self._notifications.all_opt_outs()
        created = 0
        for user_id, contest_id in bookmarks:
            record = records.get(contest_id)
            if record is None:  # 삭제된 대회 (Q-3)
                continue
            enabled = ALL_KINDS - opt_outs.get(user_id, frozenset())
            dates = ContestDates(apply_end=record.apply_end, event_start=record.event_start)
            for due in due_notifications(dates, today, enabled):
                if self._notifications.sent(user_id, contest_id, due.kind):
                    continue
                self._notifications.add(
                    user_id, contest_id, due.kind, record.title, due.target_date, now
                )
                created += 1
        self._notifications.commit()
        logger.info("알림 발급: %d건 (저장 %d건, 기준일 %s)", created, len(bookmarks), today)
        return created


@dataclass(frozen=True)
class NotificationView:
    """알림 한 건과 보여 줄 문장."""

    record: NotificationRecord
    message: str


@dataclass(frozen=True)
class NotificationPage:
    """내 알림 목록 한 페이지와 안 읽은 건수."""

    items: list[NotificationView]
    page: int
    size: int
    total: int
    unread_count: int


class NotificationService:
    """내 알림 목록·읽음 처리·알림 설정 유스케이스."""

    def __init__(self, repo: NotificationRepository) -> None:
        """알림 저장소를 받는다."""
        self._repo = repo

    def list_notifications(self, user_id: int, page: int, size: int) -> NotificationPage:
        """최근 보낸순 한 페이지. 대회가 삭제돼도 보낸 알림은 남는다."""
        found = self._repo.list_for_user(user_id, page, size)
        items = [
            NotificationView(r, notification_message(r.kind, r.title, r.target_date))
            for r in found.items
        ]
        return NotificationPage(
            items=items, page=page, size=size, total=found.total, unread_count=found.unread
        )

    def mark_read(self, user_id: int, notification_id: int, now: datetime) -> None:
        """알림을 읽음으로 표시한다. 내 알림이 아니거나 없으면 404."""
        if not self._repo.mark_read(user_id, notification_id, now):
            raise NotFoundError("알림을 찾을 수 없습니다.")

    def mark_all_read(self, user_id: int, now: datetime) -> None:
        """안 읽은 알림을 모두 읽음으로 표시한다."""
        self._repo.mark_all_read(user_id, now)

    def enabled_kinds(self, user_id: int) -> frozenset[NotificationKind]:
        """회원이 켜 둔 알림 종류."""
        return ALL_KINDS - self._repo.opt_outs(user_id)

    def update_enabled_kinds(
        self, user_id: int, enabled: frozenset[NotificationKind]
    ) -> frozenset[NotificationKind]:
        """켜 둘 알림 종류를 통째로 바꾸고 저장한 값을 돌려준다."""
        self._repo.replace_opt_outs(user_id, ALL_KINDS - enabled)
        return self.enabled_kinds(user_id)


def get_notification_service(
    session: Annotated[Session, Depends(get_session)],
) -> NotificationService:
    """요청마다 알림 서비스를 조립한다."""
    return NotificationService(NotificationRepository(session))
