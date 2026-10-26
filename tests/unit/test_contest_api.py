"""대회 조회 API (F2-3, F2-4). 저장소는 메모리 가짜로 바꾸고 오늘 날짜를 고정한다."""

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.contests.repository import ContestRecord, ContestRepository
from app.contests.router import today_kst
from app.contests.rules import ContestQuery, ContestStatus, SortOrder
from app.contests.service import ContestQueryService, get_query_service
from app.main import app

TODAY = date(2026, 10, 23)


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
        sources=("dacon", "linkareer"),
    )


class FakeRepository(ContestRepository):
    def __init__(self, records: list[ContestRecord], total: int | None = None) -> None:
        self.records = records
        self.total = len(records) if total is None else total
        self.queries: list[ContestQuery] = []

    def find(self, query: ContestQuery, today: date) -> tuple[list[ContestRecord], int]:
        self.queries.append(query)
        return self.records, self.total

    def get(self, contest_id: str) -> ContestRecord | None:
        return next((r for r in self.records if r.id == contest_id), None)


@pytest.fixture
def repo() -> FakeRepository:
    return FakeRepository(
        [
            _record("a", date(2026, 10, 1), date(2026, 10, 30)),
            _record("b", date(2026, 11, 1), date(2026, 11, 30)),
            _record("c", None, date(2026, 10, 23)),
        ],
        total=45,
    )


@pytest.fixture
def client(repo: FakeRepository) -> Iterator[TestClient]:
    app.dependency_overrides[get_query_service] = lambda: ContestQueryService(repo)
    app.dependency_overrides[today_kst] = lambda: TODAY
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.mark.feature("F2-3")
def test_list_defaults_to_20_per_page_excluding_closed(
    client: TestClient, repo: FakeRepository
) -> None:
    res = client.get("/contests")

    assert res.status_code == 200
    body = res.json()
    assert (body["page"], body["size"], body["total"]) == (1, 20, 45)
    query = repo.queries[0]
    assert query.statuses == {ContestStatus.OPEN, ContestStatus.UPCOMING}
    assert (query.page, query.size, query.sort) == (1, 20, SortOrder.DEADLINE)


@pytest.mark.feature("F2-3")
def test_list_computes_status_and_d_day(client: TestClient) -> None:
    items = {item["id"]: item for item in client.get("/contests").json()["items"]}

    assert (items["a"]["status"], items["a"]["d_day"]) == ("open", 7)
    assert (items["b"]["status"], items["b"]["d_day"]) == ("upcoming", 38)
    # 시작일이 없으면 접수 중, 마감 당일은 D-0 (Q-6)
    assert (items["c"]["status"], items["c"]["d_day"]) == ("open", 0)
    assert "eligibility_text" not in items["a"]


@pytest.mark.feature("F2-3")
def test_list_parses_filters_search_sort_and_paging(
    client: TestClient, repo: FakeRepository
) -> None:
    res = client.get(
        "/contests",
        params={
            "page": 3,
            "size": 50,
            "sort": "latest",
            "status": "open, closed",
            "field": "AI,보안,",
            "q": "  해커톤 ",
        },
    )

    assert res.status_code == 200
    query = repo.queries[0]
    assert query.statuses == {ContestStatus.OPEN, ContestStatus.CLOSED}
    assert query.fields == ("AI", "보안")
    assert query.keyword == "해커톤"
    assert (query.page, query.size, query.sort) == (3, 50, SortOrder.LATEST)


@pytest.mark.feature("F2-3")
@pytest.mark.parametrize(
    "params",
    [
        {"size": 101},
        {"size": 0},
        {"page": 0},
        {"sort": "popular"},
        {"status": "open,ended"},
        {"q": "가" * 101},
    ],
)
def test_list_rejects_invalid_params_with_400(
    client: TestClient, params: dict[str, str | int]
) -> None:
    res = client.get("/contests", params=params)

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.feature("F2-4")
def test_detail_returns_eligibility_sources_and_d_day(client: TestClient) -> None:
    res = client.get("/contests/a")

    assert res.status_code == 200
    body = res.json()
    assert body["eligibility_text"] == "대상: 대학생"
    assert body["sources"] == ["dacon", "linkareer"]
    assert (body["status"], body["d_day"]) == ("open", 7)


@pytest.mark.feature("F2-4")
def test_detail_unknown_id_returns_404(client: TestClient) -> None:
    res = client.get("/contests/nope")

    assert res.status_code == 404
    assert res.json() == {"error": {"code": "NOT_FOUND", "message": "대회를 찾을 수 없습니다."}}
