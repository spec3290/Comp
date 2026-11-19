"""알림·알림 설정 저장소 (PostgreSQL, F4-1). 알림 대상인 북마크(F2-7)도 여기서 읽는다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Date, delete, exists, func, literal, select, update
from sqlalchemy.dialects.postgresql import Insert as PostgresInsert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import Insert as SqliteInsert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contests.models import Bookmark
from app.core.sql import AwareDateTime
from app.notifications.models import Notification, NotificationOptOut
from app.notifications.rules import NotificationKind

OnConflictInsert = PostgresInsert | SqliteInsert


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


def _insert_for(session: Session) -> Callable[[type[Notification]], OnConflictInsert]:
    """충돌 무시 INSERT 를 방언에 맞게 고른다 (운영 PostgreSQL, 테스트 SQLite)."""
    return sqlite_insert if session.get_bind().dialect.name == "sqlite" else postgresql_insert


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

    def add(
        self,
        user_id: int,
        contest_id: str,
        kind: NotificationKind,
        title: str,
        target_date: date,
        now: datetime,
    ) -> bool:
        """알림 한 건을 넣고 실제로 넣었으면 True. 저장 확정은 `commit` 때 한다.

        이미 보낸 알림(동시 실행 포함)이나 그 사이 탈퇴한 회원의 알림은 오류 없이 건너뛴다.
        충돌·외래 키 오류로 같은 실행의 다른 알림까지 롤백되지 않도록 DB 가 한 문장으로 판단한다.
        """
        values = select(
            literal(user_id),
            literal(contest_id),
            literal(kind.value),
            literal(title),
            literal(target_date, Date()),
            literal(now, AwareDateTime()),
        ).where(exists().where(User.id == user_id))
        columns = ["user_id", "contest_id", "kind", "title", "target_date", "created_at"]
        statement = _insert_for(self._session)(Notification).from_select(columns, values)
        # psycopg 는 INSERT ... SELECT 의 rowcount 를 -1 로 줄 수 있어 넣은 행의 id 로 판단한다
        inserted = self._session.scalar(
            statement.on_conflict_do_nothing(
                index_elements=["user_id", "contest_id", "kind"]
            ).returning(Notification.id)
        )
        return inserted is not None

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
        """회원이 끈 알림 종류를 통째로 바꾼다.

        같은 회원의 교체가 동시에 오면 두 요청 값이 섞일 수 있어, 회원 행을 잠가 차례로 한다.
        끈 종류가 아직 하나도 없어도 잠기도록 설정 행이 아니라 회원 행을 잠근다.
        """
        self._session.execute(select(User.id).where(User.id == user_id).with_for_update())
        self._session.execute(
            delete(NotificationOptOut).where(NotificationOptOut.user_id == user_id)
        )
        self._session.add_all(NotificationOptOut(user_id=user_id, kind=k.value) for k in kinds)
        self._session.commit()
