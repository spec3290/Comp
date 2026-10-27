"""대회 저장 (F2-7). 북마크는 메모리 SQLite, 대회 저장소는 메모리 가짜로 바꾸고 날짜를 고정한다."""

from collections.abc import Iterator
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.dependencies import get_current_user_id
from app.auth.service import get_token_issuer
from app.auth.tokens import TokenIssuer
from app.contests.models import Bookmark
from app.contests.repository import BookmarkRepository, ContestRecord, ContestRepository
from app.contests.router import today_kst
from app.contests.service import BookmarkService, get_bookmark_service
from app.core.sql import Base
from app.core.time import KST, now_kst
from app.main import app

TODAY = date(2026, 10, 23)
NOW = datetime(2026, 10, 23, 9, 0, tzinfo=KST)
USER = 1
OTHER_USER = 2


def _record(contest_id: str, apply_start: date | None, apply_end: date) -> ContestRecord:
    return ContestRecord(
        id=contest_id,
        title=f"대회 {contest_id}",
        host="주최사",
        field="AI",
        apply_start=apply_start,
        apply_end=apply_end,
        event_start=None,
        event_end=None,
        eligibility_text="대상: 대학생",
        apply_url="https://example.com/apply",
        source_url="https://example.com",
        sources=("dacon",),
    )


class FakeContests(ContestRepository):
    def __init__(self, records: list[ContestRecord]) -> None:
        self.records = {r.id: r for r in records}

    def get(self, contest_id: str) -> ContestRecord | None:
        return self.records.get(contest_id)

    def get_many(self, contest_ids: list[str]) -> dict[str, ContestRecord]:
        return {i: self.records[i] for i in contest_ids if i in self.records}


@pytest.fixture
def contests() -> FakeContests:
    return FakeContests(
        [
            _record("open", date(2026, 10, 1), date(2026, 10, 30)),
            _record("upcoming", date(2026, 11, 1), date(2026, 11, 30)),
            _record("ends-today", None, TODAY),
            _record("closed", date(2026, 10, 1), TODAY - timedelta(days=1)),
        ]
    )


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    engine.dispose()


@pytest.fixture
def service(session: Session, contests: FakeContests) -> BookmarkService:
    return BookmarkService(BookmarkRepository(session), contests)


@pytest.fixture
def client(service: BookmarkService) -> Iterator[TestClient]:
    app.dependency_overrides[get_bookmark_service] = lambda: service
    app.dependency_overrides[get_current_user_id] = lambda: USER
    app.dependency_overrides[today_kst] = lambda: TODAY
    yield TestClient(app)
    app.dependency_overrides.clear()


def _bookmark_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Bookmark)) or 0


@pytest.mark.feature("F2-7")
def test_bookmark_open_contest_returns_204_and_appears_in_my_list(client: TestClient) -> None:
    res = client.put("/contests/open/bookmark")

    assert res.status_code == 204
    body = client.get("/users/me/bookmarks").json()
    assert (body["page"], body["size"], body["total"]) == (1, 20, 1)
    item = body["items"][0]
    assert (item["id"], item["status"], item["d_day"]) == ("open", "open", 7)
    assert datetime.fromisoformat(item["bookmarked_at"]).utcoffset() == timedelta(hours=9)


@pytest.mark.feature("F2-7")
@pytest.mark.parametrize("contest_id", ["upcoming", "ends-today"])
def test_bookmark_upcoming_or_closing_today_contest_is_allowed(
    client: TestClient, contest_id: str
) -> None:
    assert client.put(f"/contests/{contest_id}/bookmark").status_code == 204


@pytest.mark.feature("F2-7")
def test_closed_contest_bookmark_returns_409(client: TestClient, session: Session) -> None:
    res = client.put("/contests/closed/bookmark")

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONTEST_CLOSED"
    assert _bookmark_count(session) == 0


@pytest.mark.feature("F2-7")
def test_unknown_contest_bookmark_returns_404(client: TestClient) -> None:
    res = client.put("/contests/missing/bookmark")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.feature("F2-7")
def test_bookmark_twice_keeps_one_row_and_first_saved_at(
    service: BookmarkService, session: Session
) -> None:
    service.add(USER, "open", TODAY, NOW)
    service.add(USER, "open", TODAY, NOW + timedelta(hours=1))

    assert _bookmark_count(session) == 1
    page = service.list_bookmarks(USER, TODAY, page=1, size=20)
    assert page.items[0].bookmarked_at == NOW


@pytest.mark.feature("F2-7")
def test_unbookmark_removes_from_my_list(client: TestClient) -> None:
    client.put("/contests/open/bookmark")

    res = client.delete("/contests/open/bookmark")

    assert res.status_code == 204
    assert client.get("/users/me/bookmarks").json()["total"] == 0


@pytest.mark.feature("F2-7")
def test_unbookmark_not_saved_or_deleted_contest_returns_204(client: TestClient) -> None:
    assert client.delete("/contests/open/bookmark").status_code == 204
    assert client.delete("/contests/missing/bookmark").status_code == 204


@pytest.mark.feature("F2-7")
def test_my_bookmarks_are_per_user(service: BookmarkService) -> None:
    service.add(USER, "open", TODAY, NOW)
    service.add(OTHER_USER, "upcoming", TODAY, NOW)

    page = service.list_bookmarks(USER, TODAY, page=1, size=20)

    assert [v.contest.record.id for v in page.items] == ["open"]


@pytest.mark.feature("F2-7")
def test_my_bookmarks_newest_first_and_paginated(service: BookmarkService) -> None:
    for minutes, contest_id in enumerate(["open", "upcoming", "ends-today"]):
        service.add(USER, contest_id, TODAY, NOW + timedelta(minutes=minutes))

    first = service.list_bookmarks(USER, TODAY, page=1, size=2)
    second = service.list_bookmarks(USER, TODAY, page=2, size=2)

    assert [v.contest.record.id for v in first.items] == ["ends-today", "upcoming"]
    assert [v.contest.record.id for v in second.items] == ["open"]
    assert first.total == second.total == 3


@pytest.mark.feature("F2-7")
def test_my_bookmarks_skip_deleted_contests(
    service: BookmarkService, contests: FakeContests
) -> None:
    service.add(USER, "open", TODAY, NOW)
    service.add(USER, "upcoming", TODAY, NOW)
    del contests.records["upcoming"]  # 마감 3일 뒤 삭제 (Q-3)

    page = service.list_bookmarks(USER, TODAY, page=1, size=20)

    assert [v.contest.record.id for v in page.items] == ["open"]
    assert page.total == 1


@pytest.mark.feature("F2-7")
def test_my_bookmarks_show_contests_closed_after_saving(
    service: BookmarkService,
) -> None:
    service.add(USER, "ends-today", TODAY, NOW)

    page = service.list_bookmarks(USER, TODAY + timedelta(days=1), page=1, size=20)

    assert [(v.contest.record.id, v.contest.status) for v in page.items] == [
        ("ends-today", "closed")
    ]


@pytest.mark.feature("F2-7")
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", "/contests/open/bookmark"),
        ("DELETE", "/contests/open/bookmark"),
        ("GET", "/users/me/bookmarks"),
    ],
)
def test_bookmark_endpoints_without_token_return_401(
    service: BookmarkService, issuer: TokenIssuer, method: str, path: str
) -> None:
    app.dependency_overrides[get_bookmark_service] = lambda: service
    app.dependency_overrides[get_token_issuer] = lambda: issuer
    try:
        res = TestClient(app).request(method, path)
    finally:
        app.dependency_overrides.clear()

    assert res.status_code == 401
    assert res.json()["error"]["code"] == "TOKEN_INVALID"


@pytest.mark.feature("F2-7")
def test_bookmark_with_real_access_token(service: BookmarkService, issuer: TokenIssuer) -> None:
    """인증 override 없이 F1-4 토큰 검증을 그대로 거친다. 로그아웃한 토큰은 거절된다."""
    app.dependency_overrides[get_bookmark_service] = lambda: service
    app.dependency_overrides[get_token_issuer] = lambda: issuer
    app.dependency_overrides[today_kst] = lambda: TODAY
    access = issuer.issue(USER, now_kst()).access_token
    headers = {"Authorization": f"Bearer {access}"}
    try:
        api = TestClient(app)
        saved = api.put("/contests/open/bookmark", headers=headers)
        listed = api.get("/users/me/bookmarks", headers=headers).json()
        issuer.revoke_session(issuer.authenticate(access), now_kst())  # 로그아웃
        after_logout = api.get("/users/me/bookmarks", headers=headers)
    finally:
        app.dependency_overrides.clear()

    assert saved.status_code == 204
    assert [item["id"] for item in listed["items"]] == ["open"]
    assert after_logout.status_code == 401
