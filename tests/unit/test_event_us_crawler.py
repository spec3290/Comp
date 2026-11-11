"""이벤터스 수집기 파싱·HTTP 동작 테스트 (F2-1). 실제 네트워크를 호출하지 않는다."""

import json
from datetime import date
from typing import Any

import httpx
import pytest

from app.contests.crawlers.event_us import QUERIES, EventUsSource, parse_external_apply_url


def _event(event_id: int, **overrides: Any) -> dict[str, Any]:
    """검색 엔진 결과 한 건. 값마다 {"raw": ...} 로 감싼다."""
    fields: dict[str, Any] = {
        "id": str(event_id),
        "title": " 2026 AI 해커톤 ",
        "app_title": "주최사",
        "subdomain": "host",
        "event_type": "대회/공모전",
        "category": "IT/프로그래밍",
        "category2": "AI",
        "tags": ["AI", "해커톤"],
        "reception_type": "eventus",
        # 2026-10-31T15:00Z 는 KST 11월 1일 0시다
        "register_start_date": "2026-10-31T15:00:00Z",
        "register_due_date": "2026-11-15T14:30:00Z",
        "start_date": "2026-11-20T00:30:00Z",
        "close_date": "2026-11-21T08:00:00Z",
    }
    return {key: {"raw": value} for key, value in (fields | overrides).items()}


def _search_page(events: list[dict[str, Any]] | None) -> str:
    """검색 결과를 searchDataRaw 로 박은 검색 페이지 HTML. None 이면 결과 없이 null."""
    data = "null" if events is None else json.dumps({"meta": {}, "results": events})
    # 실제 페이지처럼 searchDataRaw 가 Vue data() 안에서 한 줄을 차지한다
    lines = [
        "<script>",
        "data() { return {",
        f"    searchDataRaw: {data},",
        "    x: 1 } }",
        "</script>",
    ]
    return "\n".join(lines)


def _detail_page(href: str) -> str:
    """외부 신청 버튼이 있는 행사 상세 페이지 HTML."""
    return (
        f'<ev-button class="bg-primary-500" @click="addClickCount" href="{href}" target="_blank">'
        '<div><span class="trans" data-trans-ko="신청하기(외부)">신청하기(외부)</span></div>'
        "</ev-button>"
    )


def _source(handler: Any) -> EventUsSource:
    """주어진 핸들러로 응답하는 가짜 클라이언트를 쓰는 수집기."""
    return EventUsSource(httpx.Client(transport=httpx.MockTransport(handler)), delay_seconds=0)


@pytest.mark.feature("F2-1")
def test_event_us_keeps_only_it_contests_and_converts_dates_to_kst() -> None:
    """IT/프로그래밍 대회/공모전만 남기고 UTC 시각을 KST 날짜로 옮긴다."""
    events = [
        _event(1),
        _event(2, event_type="강연/세미나"),
        _event(3, category="예술"),
        {"id": {"raw": "4"}, "broken": {"raw": True}},
        # 유형·분야는 맞지만 태그가 깨진 항목은 그 항목만 건너뛴다
        _event(6, tags=[None]),
        _event(7, title=None),
        # 대회/공모전으로 올린 교육 과정 광고는 형식 단어가 없어 뺀다
        _event(5, title="자바 풀스택 개발자 국비지원 과정", tags=["자바", "국비지원"]),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        """첫 검색어에만 결과를 돌려준다."""
        found = events if request.url.params["query"] == QUERIES[0] else []
        return httpx.Response(200, text=_search_page(found))

    contests = _source(handler).fetch()

    assert len(contests) == 1
    contest = contests[0]
    assert contest.title == "2026 AI 해커톤"
    assert contest.host == "주최사"
    assert contest.field == "AI, 해커톤"
    assert (contest.apply_start, contest.apply_end) == (date(2026, 11, 1), date(2026, 11, 15))
    assert (contest.event_start, contest.event_end) == (date(2026, 11, 20), date(2026, 11, 21))
    assert contest.apply_url == contest.source_url == "https://event-us.kr/host/event/1"


@pytest.mark.feature("F2-1")
def test_event_us_merges_queries_without_duplicates() -> None:
    """여러 검색어에 같은 행사가 걸려도 한 번만 수집한다."""
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """모든 검색어에 같은 행사를 돌려주고 검색어를 기록한다."""
        queries.append(request.url.params["query"])
        return httpx.Response(200, text=_search_page([_event(1)]))

    contests = _source(handler).fetch()

    assert queries == list(QUERIES)
    assert len(contests) == 1


@pytest.mark.feature("F2-1")
def test_event_us_uses_external_apply_link_and_skips_contests_without_one() -> None:
    """외부 접수 행사는 상세 페이지의 외부 신청 링크를 쓰고, 링크가 없으면 건너뛴다 (Q-20)."""
    events = [
        _event(1, reception_type="external", subdomain="a"),
        _event(2, reception_type="external", subdomain="b"),
        _event(3, reception_type="donotuse"),
    ]
    details = {
        "/a/event/1": _detail_page("https://forms.gle/abc?x=1&amp;y=2"),
        "/b/event/2": "<html>신청 버튼 없음</html>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """검색은 첫 검색어에만 결과를, 상세는 경로별 페이지를 돌려준다."""
        if request.url.path in details:
            return httpx.Response(200, text=details[request.url.path])
        found = events if request.url.params["query"] == QUERIES[0] else []
        return httpx.Response(200, text=_search_page(found))

    contests = _source(handler).fetch()

    assert len(contests) == 1
    assert contests[0].apply_url == "https://forms.gle/abc?x=1&y=2"
    assert contests[0].source_url == "https://event-us.kr/a/event/1"


@pytest.mark.feature("F2-1")
def test_event_us_skips_contest_when_detail_page_fails() -> None:
    """상세 페이지 요청이 실패한 행사만 건너뛰고 수집은 이어 간다."""
    events = [_event(1, reception_type="external"), _event(2)]

    def handler(request: httpx.Request) -> httpx.Response:
        """상세 페이지는 503, 검색은 첫 검색어에만 결과를 돌려준다."""
        if request.url.path.startswith("/host/event/"):
            return httpx.Response(503)
        found = events if request.url.params["query"] == QUERIES[0] else []
        return httpx.Response(200, text=_search_page(found))

    contests = _source(handler).fetch()

    assert [c.source_url for c in contests] == ["https://event-us.kr/host/event/2"]


@pytest.mark.feature("F2-1")
def test_event_us_search_page_without_results_counts_as_empty() -> None:
    """검색 결과가 null 로 실린 페이지는 빈 결과로 본다."""
    contests = _source(lambda _: httpx.Response(200, text=_search_page(None))).fetch()

    assert contests == []


@pytest.mark.feature("F2-1")
def test_event_us_redirect_to_empty_result_page_counts_as_empty() -> None:
    """결과가 없어 빈 결과 페이지로 리다이렉트된 검색어는 건너뛴다."""

    def handler(request: httpx.Request) -> httpx.Response:
        """첫 검색어만 결과를 주고, 나머지는 빈 결과 페이지로 보낸다."""
        if request.url.path == "/search/emptyresult":
            return httpx.Response(200, text="<html>검색 결과 없음</html>")
        if request.url.params["query"] == QUERIES[0]:
            return httpx.Response(200, text=_search_page([_event(1)]))
        return httpx.Response(302, headers={"Location": "/search/emptyresult"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, follow_redirects=True)
    contests = EventUsSource(client, delay_seconds=0).fetch()

    assert len(contests) == 1


@pytest.mark.feature("F2-1")
def test_event_us_page_without_search_data_raises() -> None:
    """searchDataRaw 가 없으면 페이지 구조가 바뀐 것으로 보고 실패한다."""
    source = _source(lambda _: httpx.Response(200, text="<html></html>"))
    with pytest.raises(ValueError, match="searchDataRaw"):
        source.fetch()


@pytest.mark.feature("F2-1")
def test_event_us_search_http_error_raises() -> None:
    """검색 페이지 HTTP 오류는 예외로 올린다."""
    with pytest.raises(httpx.HTTPStatusError):
        _source(lambda _: httpx.Response(500)).fetch()


@pytest.mark.feature("F2-1")
def test_parse_external_apply_url_ignores_other_links() -> None:
    """외부 신청 버튼이 아닌 링크는 신청 주소로 쓰지 않는다."""
    page = '<a href="https://play.google.com/app">앱</a>' + _detail_page(
        "https://apply.example.com"
    )

    assert parse_external_apply_url(page) == "https://apply.example.com"
    assert parse_external_apply_url('<a href="https://play.google.com/app">앱</a>') is None
