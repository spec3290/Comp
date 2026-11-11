"""대회 조회 API (F2-3, F2-4, F3-3). 저장소는 메모리 가짜로 바꾸고 오늘 날짜를 고정한다.

회원·토큰은 메모리 SQLite·fakeredis 로 띄운 실제 서비스를 쓴다 (`tests/unit/conftest.py`).
"""

from collections.abc import Iterator
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.repository import UserRepository
from app.auth.service import UserService, get_token_issuer, get_user_service
from app.auth.tokens import TokenIssuer
from app.contests.repository import (
    BookmarkRepository,
    ContestOrigin,
    ContestRecord,
    ContestRepository,
)
from app.contests.router import today_kst
from app.contests.rules import NO_TAGS, ContestQuery, ContestStatus, ContestTags, SortOrder
from app.contests.service import (
    BookmarkService,
    ContestQueryService,
    get_bookmark_service,
    get_query_service,
)
from app.core.time import KST, now_kst
from app.main import app
from tests.unit.auth_support import bearer

TODAY = date(2026, 10, 23)
NOW = datetime(2026, 10, 23, 9, 0, tzinfo=KST)
UPDATED_AT = datetime(2026, 10, 22, 6, 0, tzinfo=KST)


def _record(
    contest_id: str,
    apply_start: date | None,
    apply_end: date,
    tags: ContestTags = NO_TAGS,
) -> ContestRecord:
    """접수 기간·태그만 다른 테스트용 대회."""
    return ContestRecord(
        id=contest_id,
        title=f"대회 {contest_id}",
        host="주최사",
        field="AI",
        apply_start=apply_start,
        apply_end=apply_end,
        event_start=None,
        event_end=None,
        apply_url="https://example.com/apply",
        source_url="https://example.com",
        sources=(
            ContestOrigin("dacon", "https://dacon.io/c/1", representative=True),
            ContestOrigin("linkareer", "https://linkareer.com/c/1", representative=False),
        ),
        updated_at=UPDATED_AT,
        tags=tags,
    )


class FakeRepository(ContestRepository):
    """고정 목록을 돌려주고 받은 조건을 기록하는 가짜 대회 저장소."""

    def __init__(self, records: list[ContestRecord], total: int | None = None) -> None:
        """돌려줄 목록과 전체 건수를 받는다."""
        self.records = records
        self.total = len(records) if total is None else total
        self.queries: list[ContestQuery] = []

    def find(self, query: ContestQuery, today: date) -> tuple[list[ContestRecord], int]:
        """받은 조건을 기록하고 고정 목록을 돌려준다."""
        self.queries.append(query)
        return self.records, self.total

    def get(self, contest_id: str) -> ContestRecord | None:
        """ID 로 대회를 찾는다."""
        return next((r for r in self.records if r.id == contest_id), None)


@pytest.fixture
def repo() -> FakeRepository:
    """상태가 다른 대회들을 담은 가짜 저장소."""
    return FakeRepository(
        [
            _record(
                "a",
                date(2026, 10, 1),
                date(2026, 10, 30),
                ContestTags(field=frozenset({"AI", "개발"}), format=frozenset({"해커톤"})),
            ),
            _record("b", date(2026, 11, 1), date(2026, 11, 30)),
            _record("c", None, date(2026, 10, 23)),
        ],
        total=45,
    )


@pytest.fixture
def client(repo: FakeRepository, session: Session, issuer: TokenIssuer) -> Iterator[TestClient]:
    """조회 서비스·날짜·회원 저장소·토큰 발급기를 고정한 API 클라이언트."""
    app.dependency_overrides[get_query_service] = lambda: ContestQueryService(repo)
    app.dependency_overrides[today_kst] = lambda: TODAY
    app.dependency_overrides[get_token_issuer] = lambda: issuer
    app.dependency_overrides[get_user_service] = lambda: UserService(UserRepository(session))
    app.dependency_overrides[get_bookmark_service] = lambda: BookmarkService(
        BookmarkRepository(session), repo
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def _member(
    session: Session, issuer: TokenIssuer, provider_id: str, fields: list[str]
) -> dict[str, str]:
    """관심 분야를 정한 회원을 만들고 그 회원의 Authorization 헤더를 돌려준다."""
    users = UserRepository(session)
    user, _ = users.get_or_create("google", provider_id, NOW)
    users.update_profile(
        user, nickname=f"회원{provider_id}", grade=None, school_type="대학교", fields=fields
    )
    return bearer(issuer.issue(user.id, now_kst()).access_token)  # 만료 검사는 실제 시각 기준


@pytest.mark.feature("F2-3")
def test_list_defaults_to_20_per_page_excluding_closed(
    client: TestClient, repo: FakeRepository
) -> None:
    """기본 목록은 20개씩이고 마감 대회를 뺀다."""
    res = client.get("/contests")

    assert res.status_code == 200
    body = res.json()
    assert (body["page"], body["size"], body["total"]) == (1, 20, 45)
    query = repo.queries[0]
    assert query.statuses == {ContestStatus.OPEN, ContestStatus.UPCOMING}
    assert (query.page, query.size, query.sort) == (1, 20, SortOrder.DEADLINE)


@pytest.mark.feature("F2-3")
def test_list_computes_status_and_d_day(client: TestClient) -> None:
    """목록은 상태와 D-day 를 날짜로 계산해 보여 준다."""
    items = {item["id"]: item for item in client.get("/contests").json()["items"]}

    assert (items["a"]["status"], items["a"]["d_day"]) == ("open", 7)
    assert (items["b"]["status"], items["b"]["d_day"]) == ("upcoming", 38)
    # 시작일이 없으면 접수 중, 마감 당일은 D-0 (Q-6)
    assert (items["c"]["status"], items["c"]["d_day"]) == ("open", 0)
    assert {"sources", "updated_at", "bookmarked"}.isdisjoint(items["a"])


@pytest.mark.feature("F2-3")
def test_list_parses_filters_search_sort_and_paging(
    client: TestClient, repo: FakeRepository
) -> None:
    """필터·검색·정렬·페이지 파라미터를 조회 조건으로 바꾼다."""
    res = client.get(
        "/contests",
        params={
            "page": 3,
            "size": 50,
            "sort": "latest",
            "status": "open, closed",
            "field": "AI,보안,",
            "format": "해커톤",
            "q": "  해커톤 ",
        },
    )

    assert res.status_code == 200
    query = repo.queries[0]
    assert query.statuses == {ContestStatus.OPEN, ContestStatus.CLOSED}
    assert query.tags == ContestTags(field=frozenset({"AI", "보안"}), format=frozenset({"해커톤"}))
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
        {"field": "AI,게임"},
        {"format": "챌린지"},
    ],
)
def test_list_rejects_invalid_params_with_400(
    client: TestClient, params: dict[str, str | int]
) -> None:
    """잘못된 파라미터는 단일 포맷 400 이다."""
    res = client.get("/contests", params=params)

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.feature("F2-4")
def test_detail_returns_source_links_updated_at_and_d_day(client: TestClient) -> None:
    """상세는 사이트별 원문 링크·갱신 시각·D-day 를 돌려주고 내부 값은 숨긴다 (Q-29)."""
    res = client.get("/contests/a")

    assert res.status_code == 200
    body = res.json()
    assert body["sources"] == [
        {"source": "dacon", "source_url": "https://dacon.io/c/1", "is_representative": True},
        {
            "source": "linkareer",
            "source_url": "https://linkareer.com/c/1",
            "is_representative": False,
        },
    ]
    assert body["updated_at"] == "2026-10-22T06:00:00+09:00"
    assert (body["status"], body["d_day"]) == ("open", 7)
    assert {"eligibility_text", "created_at", "merge_key"}.isdisjoint(body)


@pytest.mark.feature("F2-4")
def test_detail_without_login_has_null_bookmarked(client: TestClient) -> None:
    """비로그인 상세의 저장 여부는 알 수 없어 null 이다 (Q-29)."""
    assert client.get("/contests/a").json()["bookmarked"] is None


@pytest.mark.feature("F2-4")
def test_detail_shows_whether_member_bookmarked(
    client: TestClient, session: Session, issuer: TokenIssuer
) -> None:
    """로그인 상세는 내가 저장한 대회인지 알려 준다. 다른 회원의 저장은 섞이지 않는다."""
    mine = _member(session, issuer, "a", [])
    other = _member(session, issuer, "b", [])
    client.put("/contests/a/bookmark", headers=other)
    client.put("/contests/b/bookmark", headers=mine)

    assert client.get("/contests/a", headers=mine).json()["bookmarked"] is False
    assert client.get("/contests/b", headers=mine).json()["bookmarked"] is True


@pytest.mark.feature("F2-4")
def test_detail_unknown_id_returns_404(client: TestClient) -> None:
    """없는 대회 상세는 404 다."""
    res = client.get("/contests/nope")

    assert res.status_code == 404
    assert res.json() == {"error": {"code": "NOT_FOUND", "message": "대회를 찾을 수 없습니다."}}


@pytest.mark.feature("F3-3")
def test_list_parses_tag_filters(client: TestClient, repo: FakeRepository) -> None:
    """분야·형식 필터 파라미터를 조회 조건으로 바꾼다."""
    res = client.get(
        "/contests", params={"field": "디자인, 아이디어", "format": "공모전,아이디어톤"}
    )

    assert res.status_code == 200
    assert repo.queries[0].tags == ContestTags(
        field=frozenset({"디자인", "아이디어"}), format=frozenset({"공모전", "아이디어톤"})
    )


@pytest.mark.feature("F3-3")
def test_list_drops_removed_eligibility_params(client: TestClient, repo: FakeRepository) -> None:
    """없앤 자격 파라미터(education 등)는 조건에 쓰이지 않는다 (Q-23)."""
    res = client.get("/contests", params={"education": "대학생", "region": "서울"})

    assert res.status_code == 200
    assert repo.queries[0].tags == NO_TAGS


@pytest.mark.feature("F3-3")
def test_list_marks_contests_matched_only_as_unclassified(client: TestClient) -> None:
    """미분류라서 걸린 대회에는 분류 확인 필요를 표시한다."""
    res = client.get("/contests", params={"format": "해커톤"})

    items = {item["id"]: item for item in res.json()["items"]}
    assert items["a"]["tag_check_needed"] is False
    assert items["b"]["tag_check_needed"] is True


@pytest.mark.feature("F2-4")
def test_detail_returns_classified_tags_in_vocabulary_order(client: TestClient) -> None:
    """상세는 분야·형식 태그를 어휘 순서로 돌려준다."""
    body = client.get("/contests/a").json()

    assert body["tags"] == {"field": ["개발", "AI"], "format": ["해커톤"]}
    assert body["tag_check_needed"] is False
    assert client.get("/contests/b").json()["tag_check_needed"] is True


@pytest.mark.feature("F3-3")
def test_logged_in_list_does_not_apply_profile_fields_by_default(
    client: TestClient, repo: FakeRepository, session: Session, issuer: TokenIssuer
) -> None:
    """로그인해도 기본 목록은 프로필 관심 분야로 거르지 않는다 (Q-24)."""
    headers = _member(session, issuer, "a", ["AI", "보안"])

    res = client.get("/contests", headers=headers)

    assert res.status_code == 200
    assert repo.queries[0].tags == NO_TAGS


@pytest.mark.feature("F3-3")
def test_interests_filter_uses_profile_fields(
    client: TestClient, repo: FakeRepository, session: Session, issuer: TokenIssuer
) -> None:
    """interests=true 면 프로필 관심 분야를 분야 필터로 쓰고 다른 조건은 그대로 둔다."""
    headers = _member(session, issuer, "a", ["AI", "보안"])

    res = client.get("/contests", params={"interests": "true", "format": "해커톤"}, headers=headers)

    assert res.status_code == 200
    query = repo.queries[0]
    assert query.tags == ContestTags(field=frozenset({"AI", "보안"}), format=frozenset({"해커톤"}))
    assert query.statuses == {ContestStatus.OPEN, ContestStatus.UPCOMING}


@pytest.mark.feature("F3-3")
def test_interests_filter_keeps_unclassified_marked(
    client: TestClient, session: Session, issuer: TokenIssuer
) -> None:
    """관심 분야 필터로 미분류라서 걸린 대회도 분류 확인 필요로 표시한다."""
    headers = _member(session, issuer, "a", ["AI"])

    res = client.get("/contests", params={"interests": "true"}, headers=headers)

    items = {item["id"]: item for item in res.json()["items"]}
    assert items["a"]["tag_check_needed"] is False
    assert items["b"]["tag_check_needed"] is True


@pytest.mark.feature("F3-3")
def test_interests_filter_without_login_returns_401(
    client: TestClient, repo: FakeRepository
) -> None:
    """로그인하지 않고 interests=true 를 보내면 401 이다."""
    res = client.get("/contests", params={"interests": "true"})

    assert res.status_code == 401
    assert res.json()["error"]["code"] == "TOKEN_INVALID"
    assert repo.queries == []


@pytest.mark.feature("F3-3")
@pytest.mark.parametrize("path", ["/contests", "/contests/a"])
def test_forged_token_returns_401(client: TestClient, path: str) -> None:
    """인증이 선택인 목록·상세도 위조 토큰은 401 `TOKEN_INVALID` 다 (ADR-0004)."""
    res = client.get(path, headers=bearer("not-a-jwt"))

    assert res.status_code == 401
    assert res.json()["error"]["code"] == "TOKEN_INVALID"


@pytest.mark.feature("F3-3")
@pytest.mark.parametrize("path", ["/contests", "/contests/a"])
def test_expired_token_returns_401_expired(
    client: TestClient, issuer: TokenIssuer, path: str
) -> None:
    """만료된 토큰은 401 `TOKEN_EXPIRED` 라 프론트가 Refresh 로 재발급하고 다시 부른다."""
    expired = issuer.issue(1, now_kst() - timedelta(hours=1)).access_token

    res = client.get(path, headers=bearer(expired))

    assert res.status_code == 401
    assert res.json()["error"]["code"] == "TOKEN_EXPIRED"


@pytest.mark.feature("F3-3")
def test_interests_filter_with_field_returns_400(
    client: TestClient, session: Session, issuer: TokenIssuer
) -> None:
    """관심 분야 필터와 분야 직접 선택은 함께 쓸 수 없다."""
    headers = _member(session, issuer, "a", ["AI"])

    res = client.get("/contests", params={"interests": "true", "field": "보안"}, headers=headers)

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.feature("F3-3")
def test_interests_filter_with_empty_profile_lists_all_fields(
    client: TestClient, repo: FakeRepository, session: Session, issuer: TokenIssuer
) -> None:
    """프로필에 관심 분야가 없으면 분야 조건 없이 전체 목록을 보여 준다 (Q-24)."""
    headers = _member(session, issuer, "a", [])

    res = client.get("/contests", params={"interests": "true"}, headers=headers)

    assert res.status_code == 200
    assert repo.queries[0].tags == NO_TAGS
