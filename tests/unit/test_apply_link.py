"""참가 바로가기 (F2-5). 클릭은 메모리 SQLite, 대회는 메모리 가짜로 바꾸고 날짜를 고정한다."""

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.dependencies import get_optional_user_id
from app.auth.models import User
from app.contests.models import ContestClick
from app.contests.repository import ClickRepository, ContestRecord
from app.contests.router import today_kst
from app.contests.service import (
    ApplyService,
    ContestQueryService,
    get_apply_service,
    get_query_service,
)
from app.core.sql import Base
from app.core.time import KST
from app.main import app
from tests.unit.test_bookmarks import FakeContests

TODAY = date(2026, 10, 23)
NOW = datetime(2026, 10, 23, 9, 0, tzinfo=KST)
APPLY_URL = "https://example.com/apply"


def _record(contest_id: str, apply_end: date, apply_url: str | None = APPLY_URL) -> ContestRecord:
    """접수 마감일과 참가 링크만 다른 테스트용 대회."""
    return ContestRecord(
        id=contest_id,
        title=f"대회 {contest_id}",
        host="주최사",
        field="AI",
        apply_start=None,
        apply_end=apply_end,
        event_start=None,
        event_end=None,
        apply_url=apply_url,
        source_url="https://example.com",
        sources=("dacon",),
    )


@pytest.fixture
def contests() -> FakeContests:
    """접수 중·오늘 마감·마감·링크 없는 대회를 담은 가짜 저장소."""
    return FakeContests(
        [
            _record("open", date(2026, 10, 30)),
            _record("ends-today", TODAY),
            _record("closed", TODAY - timedelta(days=1)),
            _record("no-link", date(2026, 10, 30), apply_url=None),
        ]
    )


def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    """SQLite 는 연결마다 외래 키 검사를 켜야 탈퇴의 ON DELETE CASCADE 가 동작한다."""
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


@pytest.fixture
def session() -> Iterator[Session]:
    """회원 한 명(id=1)이 있는 메모리 SQLite 세션."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    event.listen(engine, "connect", _enable_foreign_keys)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        s.add(User(id=1, provider="google", provider_id="g-1", created_at=NOW))
        s.commit()
        yield s
    engine.dispose()


@pytest.fixture
def user_id() -> list[int | None]:
    """요청을 보낸 회원 id. 비로그인이면 None. 테스트에서 바꿔 끼운다."""
    return [None]


@pytest.fixture
def client(
    session: Session, contests: FakeContests, user_id: list[int | None]
) -> Iterator[TestClient]:
    """가짜 저장소·회원·날짜를 끼운 API 클라이언트."""
    app.dependency_overrides[get_apply_service] = lambda: ApplyService(
        ClickRepository(session), contests
    )
    app.dependency_overrides[get_query_service] = lambda: ContestQueryService(contests)
    app.dependency_overrides[get_optional_user_id] = lambda: user_id[0]
    app.dependency_overrides[today_kst] = lambda: TODAY
    yield TestClient(app)
    app.dependency_overrides.clear()


def _clicks(session: Session) -> list[tuple[str, int | None]]:
    """기록된 클릭의 (대회 id, 회원 id) 목록."""
    rows = session.scalars(select(ContestClick).order_by(ContestClick.id))
    return [(r.contest_id, r.user_id) for r in rows]


@pytest.mark.feature("F2-5")
def test_click_returns_apply_url_and_records_click(client: TestClient, session: Session) -> None:
    """참가 바로가기를 누르면 공식 참가 URL 을 돌려주고 클릭을 한 건 남긴다."""
    res = client.post("/contests/open/clicks")

    assert res.status_code == 200
    assert res.json() == {"apply_url": APPLY_URL}
    assert _clicks(session) == [("open", None)]


@pytest.mark.feature("F2-5")
def test_click_by_member_records_user_and_every_click_counts(
    client: TestClient, session: Session, user_id: list[int | None]
) -> None:
    """로그인 회원의 클릭은 회원 id 와 함께 남고, 누를 때마다 한 건씩 쌓인다."""
    user_id[0] = 1

    client.post("/contests/open/clicks")
    client.post("/contests/open/clicks")

    assert _clicks(session) == [("open", 1), ("open", 1)]


@pytest.mark.feature("F2-5")
def test_click_on_deadline_day_is_allowed(client: TestClient) -> None:
    """마감 당일까지는 바로가기를 쓸 수 있다."""
    assert client.post("/contests/ends-today/clicks").status_code == 200


@pytest.mark.feature("F2-5")
def test_closed_contest_click_returns_409(client: TestClient, session: Session) -> None:
    """마감된 대회의 클릭 기록은 409 CONTEST_CLOSED 로 거절하고 남기지 않는다."""
    res = client.post("/contests/closed/clicks")

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONTEST_CLOSED"
    assert _clicks(session) == []


@pytest.mark.feature("F2-5")
def test_contest_without_apply_url_click_returns_409(client: TestClient, session: Session) -> None:
    """참가 링크를 못 가져온 대회는 409 APPLY_URL_MISSING 이고 클릭을 남기지 않는다 (Q-29)."""
    res = client.post("/contests/no-link/clicks")

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "APPLY_URL_MISSING"
    assert _clicks(session) == []


@pytest.mark.feature("F2-5")
def test_unknown_contest_click_returns_404(client: TestClient) -> None:
    """없는 대회의 클릭은 404 다."""
    res = client.post("/contests/missing/clicks")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.feature("F2-5")
def test_contest_without_apply_url_is_shown_with_null_link(client: TestClient) -> None:
    """링크가 없는 대회도 상세에 보이고 apply_url 이 null 이다. 프론트는 직접 검색 안내를 띄운다."""
    res = client.get("/contests/no-link")

    assert res.status_code == 200
    assert res.json()["apply_url"] is None


@pytest.mark.feature("F2-5")
def test_withdrawal_deletes_member_clicks(session: Session) -> None:
    """탈퇴(F1-8)하면 그 회원의 클릭 기록도 함께 지워진다."""
    ApplyService(ClickRepository(session), FakeContests([_record("open", TODAY)])).click(
        "open", 1, TODAY, NOW
    )

    session.delete(session.get(User, 1))
    session.commit()

    assert _clicks(session) == []
