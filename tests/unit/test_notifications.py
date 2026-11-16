"""알림 발급 (F4-1). 알림·북마크는 메모리 SQLite, 대회는 메모리 가짜 저장소, 날짜는 고정이다."""

import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.dependencies import get_current_user_id
from app.auth.models import User
from app.auth.service import get_token_issuer
from app.auth.tokens import TokenIssuer
from app.contests.models import Bookmark
from app.contests.repository import ContestOrigin, ContestRecord, ContestRepository
from app.core.sql import Base
from app.core.time import KST
from app.main import app
from app.notifications.models import Notification
from app.notifications.repository import NotificationRepository
from app.notifications.rules import (
    ContestDates,
    DueNotification,
    NotificationKind,
    due_notifications,
    message,
)
from app.notifications.service import (
    NotificationDispatcher,
    NotificationService,
    get_notification_service,
)

TODAY = date(2026, 11, 16)
NOW = datetime(2026, 11, 16, 6, 30, tzinfo=KST)
USER = 1
OTHER_USER = 2
CONTEST_ID = "a" * 24
OTHER_CONTEST_ID = "b" * 24


def _days(n: int) -> date:
    """오늘부터 n 일 뒤 날짜."""
    return TODAY + timedelta(days=n)


def _record(
    contest_id: str, apply_end: date | None, event_start: date | None = None
) -> ContestRecord:
    """접수 마감일·시작일만 다른 테스트용 대회."""
    return ContestRecord(
        id=contest_id,
        title=f"대회 {contest_id[:1]}",
        host="주최사",
        field="AI",
        apply_start=None,
        apply_end=apply_end,
        event_start=event_start,
        event_end=None,
        apply_url="https://example.com/apply",
        source_url="https://example.com",
        sources=(ContestOrigin("dacon", "https://example.com", representative=True),),
        updated_at=NOW,
    )


class FakeContests(ContestRepository):
    """메모리에 대회를 들고 있는 가짜 대회 저장소."""

    def __init__(self, records: list[ContestRecord]) -> None:
        """대회 목록을 ID 로 색인한다."""
        self.records = {r.id: r for r in records}

    def get_many(self, contest_ids: list[str]) -> dict[str, ContestRecord]:
        """있는 ID 의 대회만 돌려준다."""
        return {i: self.records[i] for i in contest_ids if i in self.records}


def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    """SQLite 도 PostgreSQL 처럼 외래 키를 검사하게 한다."""
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


@pytest.fixture
def session() -> Iterator[Session]:
    """회원 둘이 있는 메모리 SQLite 세션."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    event.listen(engine, "connect", _enable_foreign_keys)
    Base.metadata.create_all(engine)
    with sessionmaker(engine, expire_on_commit=False)() as s:
        for user_id in (USER, OTHER_USER):
            s.add(User(id=user_id, provider="google", provider_id=str(user_id), created_at=NOW))
        s.commit()
        yield s
    engine.dispose()


@pytest.fixture
def contests() -> FakeContests:
    """마감 7일 전 대회와 내일 시작하는 대회."""
    return FakeContests(
        [_record(CONTEST_ID, _days(7)), _record(OTHER_CONTEST_ID, _days(20), _days(1))]
    )


@pytest.fixture
def dispatcher(session: Session, contests: FakeContests) -> NotificationDispatcher:
    """테스트용 알림 발급기."""
    return NotificationDispatcher(NotificationRepository(session), contests)


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    """알림 서비스·회원을 고정한 API 클라이언트."""
    service = NotificationService(NotificationRepository(session))
    app.dependency_overrides[get_notification_service] = lambda: service
    app.dependency_overrides[get_current_user_id] = lambda: USER
    yield TestClient(app)
    app.dependency_overrides.clear()


def _bookmark(session: Session, user_id: int, contest_id: str) -> None:
    """회원이 대회를 저장한다."""
    session.add(Bookmark(user_id=user_id, contest_id=contest_id, created_at=NOW))
    session.commit()


def _sent(session: Session) -> list[tuple[int, str, str]]:
    """보낸 알림의 (회원, 대회, 종류) 목록."""
    rows = session.execute(
        select(Notification.user_id, Notification.contest_id, Notification.kind).order_by(
            Notification.id
        )
    )
    return [(u, c, k) for u, c, k in rows]


# ── 규칙 ──────────────────────────────────────────────────────────


@pytest.mark.feature("F4-1")
@pytest.mark.parametrize(
    ("left", "kind"),
    [
        (7, NotificationKind.DEADLINE_D7),
        (3, NotificationKind.DEADLINE_D3),
        (1, NotificationKind.DEADLINE_D1),
    ],
)
def test_deadline_notification_is_due_on_its_day(left: int, kind: NotificationKind) -> None:
    """접수 마감 D-7·D-3·D-1 당일에 그 알림을 보낸다."""
    due = due_notifications(ContestDates(apply_end=_days(left), event_start=None), TODAY)

    assert due == [DueNotification(kind, _days(left))]


@pytest.mark.feature("F4-1")
@pytest.mark.parametrize("left", [8, 6, 2, 0, -1])
def test_no_deadline_notification_on_other_days(left: int) -> None:
    """정해진 날이 아니면(마감 당일·지난 뒤 포함) 마감 알림이 없다."""
    assert due_notifications(ContestDates(apply_end=_days(left), event_start=None), TODAY) == []


@pytest.mark.feature("F4-1")
def test_event_start_notification_is_due_the_day_before() -> None:
    """시작일 전날에 시작 알림을 보내고, 시작 당일에는 보내지 않는다."""
    tomorrow = due_notifications(ContestDates(apply_end=None, event_start=_days(1)), TODAY)
    today = due_notifications(ContestDates(apply_end=None, event_start=TODAY), TODAY)

    assert tomorrow == [DueNotification(NotificationKind.EVENT_START_D1, _days(1))]
    assert today == []


@pytest.mark.feature("F4-1")
def test_unknown_deadline_sends_no_deadline_notification_but_event_start() -> None:
    """접수 마감일 미확인 대회는 마감 알림이 없고 시작 알림만 있다 (Q-35)."""
    due = due_notifications(ContestDates(apply_end=None, event_start=_days(1)), TODAY)

    assert [d.kind for d in due] == [NotificationKind.EVENT_START_D1]


@pytest.mark.feature("F4-1")
def test_disabled_kinds_are_not_due() -> None:
    """회원이 끈 종류는 보낼 날이어도 빠진다."""
    dates = ContestDates(apply_end=_days(1), event_start=_days(1))
    enabled = frozenset({NotificationKind.EVENT_START_D1})

    assert [d.kind for d in due_notifications(dates, TODAY, enabled)] == [
        NotificationKind.EVENT_START_D1
    ]


@pytest.mark.feature("F4-1")
def test_message_names_contest_and_date() -> None:
    """알림 문장에 대회 이름과 마감일·시작일이 들어간다."""
    deadline = message(NotificationKind.DEADLINE_D3, "AI 경진대회", date(2026, 11, 19))
    start = message(NotificationKind.EVENT_START_D1, "해커톤", date(2026, 11, 17))

    assert deadline == "'AI 경진대회' 접수 마감 3일 전이에요. (11월 19일 마감)"
    assert start == "'해커톤' 대회가 내일(11월 17일) 시작해요."


# ── 발급 ──────────────────────────────────────────────────────────


@pytest.mark.feature("F4-1")
def test_dispatch_sends_due_notifications_only_for_bookmarked_contests(
    session: Session, dispatcher: NotificationDispatcher
) -> None:
    """저장한 대회의 오늘 알림만 그 회원에게 보낸다."""
    _bookmark(session, USER, CONTEST_ID)
    _bookmark(session, OTHER_USER, OTHER_CONTEST_ID)

    created = dispatcher.dispatch(TODAY, NOW)

    assert created == 2
    assert _sent(session) == [
        (USER, CONTEST_ID, "deadline_d7"),
        (OTHER_USER, OTHER_CONTEST_ID, "event_start_d1"),
    ]


@pytest.mark.feature("F4-1")
def test_dispatch_again_does_not_duplicate(
    session: Session, dispatcher: NotificationDispatcher
) -> None:
    """같은 날 다시 실행해도 같은 알림은 한 번만 보낸다."""
    _bookmark(session, USER, CONTEST_ID)
    dispatcher.dispatch(TODAY, NOW)

    assert dispatcher.dispatch(TODAY, NOW + timedelta(hours=1)) == 0
    assert len(_sent(session)) == 1


@pytest.mark.feature("F4-1")
def test_unbookmarked_contest_gets_no_notification(
    session: Session, dispatcher: NotificationDispatcher
) -> None:
    """저장을 해제하면 보내기로 한 알림을 보내지 않는다."""
    _bookmark(session, USER, CONTEST_ID)
    session.execute(delete(Bookmark))
    session.commit()

    assert dispatcher.dispatch(TODAY, NOW) == 0


@pytest.mark.feature("F4-1")
def test_deleted_contest_gets_no_notification(
    session: Session, contests: FakeContests, dispatcher: NotificationDispatcher
) -> None:
    """대회가 삭제(Q-3)되면 저장돼 있어도 알림을 보내지 않는다."""
    _bookmark(session, USER, CONTEST_ID)
    del contests.records[CONTEST_ID]

    assert dispatcher.dispatch(TODAY, NOW) == 0


@pytest.mark.feature("F4-1")
def test_dispatch_uses_latest_deadline(
    session: Session, contests: FakeContests, dispatcher: NotificationDispatcher
) -> None:
    """보낼 때 그 시점의 최신 마감일로 계산한다 (Q-32)."""
    _bookmark(session, USER, CONTEST_ID)
    contests.records[CONTEST_ID] = replace(contests.records[CONTEST_ID], apply_end=_days(8))
    assert dispatcher.dispatch(TODAY, NOW) == 0

    contests.records[CONTEST_ID] = replace(contests.records[CONTEST_ID], apply_end=_days(3))
    dispatcher.dispatch(TODAY, NOW)

    assert _sent(session) == [(USER, CONTEST_ID, "deadline_d3")]


@pytest.mark.feature("F4-1")
def test_dispatch_skips_kinds_the_user_turned_off(
    session: Session, dispatcher: NotificationDispatcher, client: TestClient
) -> None:
    """설정에서 끈 종류는 보내지 않고, 다른 회원에게는 그대로 보낸다."""
    _bookmark(session, USER, CONTEST_ID)
    _bookmark(session, OTHER_USER, CONTEST_ID)
    settings = {"deadline_d7": False, "deadline_d3": True, "deadline_d1": True}
    client.put("/users/me/notification-settings", json={**settings, "event_start_d1": True})

    dispatcher.dispatch(TODAY, NOW)

    assert _sent(session) == [(OTHER_USER, CONTEST_ID, "deadline_d7")]


# ── 내 알림 API ────────────────────────────────────────────────────


@pytest.mark.feature("F4-1")
def test_list_returns_my_notifications_newest_first_with_unread_count(
    session: Session, dispatcher: NotificationDispatcher, client: TestClient
) -> None:
    """내 알림만 최근 보낸순으로 문장·안 읽은 건수와 함께 돌려준다."""
    _bookmark(session, USER, OTHER_CONTEST_ID)
    _bookmark(session, OTHER_USER, CONTEST_ID)
    dispatcher.dispatch(TODAY, NOW)  # 시작 전날
    dispatcher.dispatch(_days(13), NOW + timedelta(days=13))  # 마감 D-7

    body = client.get("/users/me/notifications").json()

    assert body["total"] == 2
    assert body["unread_count"] == 2
    assert [item["kind"] for item in body["items"]] == ["deadline_d7", "event_start_d1"]
    first, second = body["items"]
    assert first["contest_id"] == OTHER_CONTEST_ID
    assert first["message"] == "'대회 b' 접수 마감 7일 전이에요. (12월 6일 마감)"
    assert first["target_date"] == "2026-12-06"
    assert first["read_at"] is None
    assert second["message"] == "'대회 b' 대회가 내일(11월 17일) 시작해요."


@pytest.mark.feature("F4-1")
def test_list_is_paginated(
    session: Session, dispatcher: NotificationDispatcher, client: TestClient
) -> None:
    """`page`·`size` 로 나눠 보고 total 은 전체 건수다."""
    _bookmark(session, USER, CONTEST_ID)
    _bookmark(session, USER, OTHER_CONTEST_ID)
    dispatcher.dispatch(TODAY, NOW)

    body = client.get("/users/me/notifications", params={"page": 2, "size": 1}).json()

    assert body["page"] == 2
    assert body["size"] == 1
    assert body["total"] == 2
    assert len(body["items"]) == 1


@pytest.mark.feature("F4-1")
def test_mark_read_keeps_first_read_time(
    session: Session, dispatcher: NotificationDispatcher, client: TestClient
) -> None:
    """읽음 처리는 204 이고 다시 해도 처음 읽은 시각을 유지한다."""
    _bookmark(session, USER, CONTEST_ID)
    dispatcher.dispatch(TODAY, NOW)
    notification_id = client.get("/users/me/notifications").json()["items"][0]["id"]

    first = client.post(f"/users/me/notifications/{notification_id}/read")
    read_at = client.get("/users/me/notifications").json()["items"][0]["read_at"]
    again = client.post(f"/users/me/notifications/{notification_id}/read")

    body = client.get("/users/me/notifications").json()
    assert (first.status_code, again.status_code) == (204, 204)
    assert read_at is not None
    assert body["items"][0]["read_at"] == read_at
    assert body["unread_count"] == 0


@pytest.mark.feature("F4-1")
def test_mark_read_of_others_or_missing_notification_returns_404(
    session: Session, dispatcher: NotificationDispatcher, client: TestClient
) -> None:
    """다른 회원의 알림이나 없는 알림을 읽음 처리하면 404 다."""
    _bookmark(session, OTHER_USER, CONTEST_ID)
    dispatcher.dispatch(TODAY, NOW)
    others = session.scalar(select(Notification.id))

    for notification_id in (others, 999):
        res = client.post(f"/users/me/notifications/{notification_id}/read")
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "NOT_FOUND"
    assert session.scalar(select(func.count()).where(Notification.read_at.is_not(None))) == 0


@pytest.mark.feature("F4-1")
def test_read_all_marks_only_my_notifications(
    session: Session, dispatcher: NotificationDispatcher, client: TestClient
) -> None:
    """모두 읽음은 내 알림만 읽음으로 바꾼다."""
    _bookmark(session, USER, CONTEST_ID)
    _bookmark(session, USER, OTHER_CONTEST_ID)
    _bookmark(session, OTHER_USER, CONTEST_ID)
    dispatcher.dispatch(TODAY, NOW)

    res = client.post("/users/me/notifications/read-all")

    assert res.status_code == 204
    assert client.get("/users/me/notifications").json()["unread_count"] == 0
    unread = select(Notification.user_id).where(Notification.read_at.is_(None))
    assert list(session.scalars(unread)) == [OTHER_USER]


# ── 알림 설정 API ──────────────────────────────────────────────────


@pytest.mark.feature("F4-1")
def test_settings_default_to_all_on(client: TestClient) -> None:
    """설정한 적 없으면 모든 알림이 켜져 있다."""
    res = client.get("/users/me/notification-settings")

    assert res.status_code == 200
    assert res.json() == {
        "deadline_d7": True,
        "deadline_d3": True,
        "deadline_d1": True,
        "event_start_d1": True,
    }


@pytest.mark.feature("F4-1")
def test_settings_update_replaces_all_values(client: TestClient) -> None:
    """설정은 전체 교체이고 저장한 값을 돌려준다. 다시 켜면 켜진다."""
    off = {"deadline_d7": False, "deadline_d3": True, "deadline_d1": False, "event_start_d1": True}

    saved = client.put("/users/me/notification-settings", json=off)
    back_on = client.put("/users/me/notification-settings", json={**off, "deadline_d7": True})

    assert saved.status_code == 200
    assert saved.json() == off
    assert back_on.json()["deadline_d7"] is True
    assert client.get("/users/me/notification-settings").json() == back_on.json()


@pytest.mark.feature("F4-1")
def test_settings_update_without_all_values_returns_400(client: TestClient) -> None:
    """값을 빠뜨리면 400 `VALIDATION_ERROR` 다."""
    res = client.put("/users/me/notification-settings", json={"deadline_d7": False})

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.feature("F4-1")
def test_notification_endpoints_without_token_return_401(issuer: TokenIssuer) -> None:
    """토큰 없이 알림 API 를 부르면 401 이다."""
    app.dependency_overrides[get_token_issuer] = lambda: issuer
    api = TestClient(app)
    try:
        responses = [
            api.get("/users/me/notifications"),
            api.post("/users/me/notifications/1/read"),
            api.post("/users/me/notifications/read-all"),
            api.get("/users/me/notification-settings"),
        ]
    finally:
        app.dependency_overrides.clear()

    for res in responses:
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "TOKEN_INVALID"


@pytest.mark.feature("F4-1")
def test_dispatch_cli_resolves_user_foreign_key_without_app() -> None:
    """앱(main)을 import 하지 않는 dispatch CLI 에서도 알림의 회원 외래 키를 찾는다."""
    code = (
        "import app.notifications.dispatch\n"
        "from app.notifications.models import Notification, NotificationOptOut\n"
        "for model in (Notification, NotificationOptOut):\n"
        "    [fk.column for fk in model.__table__.foreign_keys]\n"
    )

    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)  # noqa: S603

    assert result.returncode == 0, result.stderr
