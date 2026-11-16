"""알림·알림 설정 저장소 (PostgreSQL, F4-1). 알림 대상인 북마크(F2-7)도 여기서 읽는다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.contests.models import Bookmark
from app.notifications.models import Notification, NotificationOptOut
from app.notifications.rules import NotificationKind


@dataclass(frozen=True)
class NotificationRecord:
    """보낸 알림 한 건."""

    id: int
    contest_id: str
    kind: NotificationKind
    title: str
    target_date: date
    created_at: datetime
    read_at: datetime | None


@dataclass(frozen=True)
class NotificationSlice:
    """회원의 알림 한 페이지와 전체·안 읽은 건수."""

    items: list[NotificationRecord]
    total: int
    unread: int


def _to_record(row: Notification) -> NotificationRecord:
    """테이블 행을 알림 값으로 옮긴다."""
    return NotificationRecord(
        id=row.id,
        contest_id=row.contest_id,
        kind=NotificationKind(row.kind),
        title=row.title,
        target_date=row.target_date,
        created_at=row.created_at,
        read_at=row.read_at,
    )


class NotificationRepository:
    """PostgreSQL 알림 저장소. 알림 생성은 커밋을 모아서 한다 (`commit`)."""

    def __init__(self, session: Session) -> None:
        """실행·요청 단위 세션을 받는다."""
        self._session = session

    def bookmarks(self) -> list[tuple[int, str]]:
        """알림 대상인 모든 (회원 id, 대회 id) 저장 목록 (F2-7)."""
        rows = self._session.execute(select(Bookmark.user_id, Bookmark.contest_id))
        return [(user_id, contest_id) for user_id, contest_id in rows]

    def all_opt_outs(self) -> dict[int, frozenset[NotificationKind]]:
        """회원별로 끈 알림 종류. 끈 종류가 없는 회원은 빠진다."""
        kinds: dict[int, set[NotificationKind]] = {}
        for row in self._session.scalars(select(NotificationOptOut)):
            kinds.setdefault(row.user_id, set()).add(NotificationKind(row.kind))
        return {user_id: frozenset(values) for user_id, values in kinds.items()}

    def sent(self, user_id: int, contest_id: str, kind: NotificationKind) -> bool:
        """같은 회원·대회·종류의 알림을 이미 보냈는지 본다."""
        found = self._session.scalar(
            select(Notification.id).where(
                Notification.user_id == user_id,
                Notification.contest_id == contest_id,
                Notification.kind == kind.value,
            )
        )
        return found is not None

    def add(
        self,
        user_id: int,
        contest_id: str,
        kind: NotificationKind,
        title: str,
        target_date: date,
        now: datetime,
    ) -> None:
        """알림 한 건을 세션에 더한다. 저장은 `commit` 때 한다."""
        self._session.add(
            Notification(
                user_id=user_id,
                contest_id=contest_id,
                kind=kind.value,
                title=title,
                target_date=target_date,
                created_at=now,
            )
        )

    def commit(self) -> None:
        """더한 알림을 한 번에 저장한다."""
        self._session.commit()

    def list_for_user(self, user_id: int, page: int, size: int) -> NotificationSlice:
        """최근 보낸순 한 페이지와 전체·안 읽은 건수."""
        mine = Notification.user_id == user_id
        rows = self._session.scalars(
            select(Notification)
            .where(mine)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        total = (
            self._session.scalar(select(func.count()).select_from(Notification).where(mine)) or 0
        )
        unread = (
            self._session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(mine, Notification.read_at.is_(None))
            )
            or 0
        )
        return NotificationSlice(items=[_to_record(r) for r in rows], total=total, unread=unread)

    def mark_read(self, user_id: int, notification_id: int, now: datetime) -> bool:
        """회원의 알림을 읽음으로 표시한다. 처음 읽은 시각을 유지하고, 없으면 False."""
        row = self._session.get(Notification, notification_id)
        if row is None or row.user_id != user_id:
            return False
        if row.read_at is None:
            row.read_at = now
            self._session.commit()
        return True

    def mark_all_read(self, user_id: int, now: datetime) -> None:
        """회원의 안 읽은 알림을 모두 읽음으로 표시한다."""
        self._session.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
            .values(read_at=now)
        )
        self._session.commit()

    def opt_outs(self, user_id: int) -> frozenset[NotificationKind]:
        """회원이 끈 알림 종류."""
        rows = self._session.scalars(
            select(NotificationOptOut.kind).where(NotificationOptOut.user_id == user_id)
        )
        return frozenset(NotificationKind(kind) for kind in rows)

    def replace_opt_outs(self, user_id: int, kinds: frozenset[NotificationKind]) -> None:
        """회원이 끈 알림 종류를 통째로 바꾼다."""
        self._session.execute(
            delete(NotificationOptOut).where(NotificationOptOut.user_id == user_id)
        )
        self._session.add_all(NotificationOptOut(user_id=user_id, kind=k.value) for k in kinds)
        self._session.commit()
