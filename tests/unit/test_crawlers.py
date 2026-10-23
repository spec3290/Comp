import json
from datetime import date
from typing import Any

import httpx
import pytest

from app.contests.crawlers.dacon import DaconSource
from app.contests.crawlers.dev_event import DevEventSource
from app.contests.crawlers.linkareer import LinkareerSource


def _dev_event(**overrides: Any) -> dict[str, Any]:
    event = {
        "id": 1,
        "title": " 2026 AI 해커톤 ",
        "organizer": "주최사",
        "event_link": "https://example.com/hack",
        "event_time_type": "RECRUIT",
        "start_date_time": "2026-10-01T00:00",
        "end_date_time": "2026-10-20T23:59",
        "description": None,
        "tags": [{"tag_name": "온라인"}, {"tag_name": "대회"}, {"tag_name": "AI"}],
    }
    return event | overrides


def _dev_event_page(events: list[dict[str, Any]]) -> str:
    data = {"props": {"pageProps": {"fallbackData": [{"dev_event": events}]}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.feature("F2-1")
def test_dev_event_keeps_only_contests_and_maps_recruit_period() -> None:
    page = _dev_event_page(
        [
            _dev_event(),
            _dev_event(id=2, tags=[{"tag_name": "세미나"}]),
            _dev_event(id=3, event_time_type="DATE", start_date_time="2026-11-01T10:00"),
            {"id": 4, "broken": True},
        ]
    )
    contests = DevEventSource(_client(lambda _: httpx.Response(200, text=page))).fetch()

    assert len(contests) == 2
    recruit, one_day = contests
    assert recruit.title == "2026 AI 해커톤"
    assert recruit.field == "AI"
    assert (recruit.apply_start, recruit.apply_end) == (date(2026, 10, 1), date(2026, 10, 20))
    assert recruit.event_start is None
    assert one_day.apply_start is None
    assert one_day.apply_end == date(2026, 11, 1)
    assert one_day.event_start == date(2026, 11, 1)


@pytest.mark.feature("F2-1")
def test_dev_event_without_next_data_raises() -> None:
    source = DevEventSource(_client(lambda _: httpx.Response(200, text="<html></html>")))
    with pytest.raises(ValueError, match="__NEXT_DATA__"):
        source.fetch()


@pytest.mark.feature("F2-1")
def test_dacon_maps_public_competitions() -> None:
    payload = {
        "data": [
            {
                "cpt_id": 236771,
                "name": "항만 AI 공모전",
                "sponsor": "",
                "keyword": "알고리즘 | 코드 제출 평가",
                "period_start": "2026-10-12 10:00:00",
                "period_end": "2026-11-02 10:00:00",
                "is_public": 1,
                "show_cpt_list": 1,
            },
            {"cpt_id": 2, "name": "비공개", "is_public": 0, "show_cpt_list": 1},
        ]
    }
    contests = DaconSource(_client(lambda _: httpx.Response(200, json=payload))).fetch()

    assert len(contests) == 1
    contest = contests[0]
    assert contest.host == "데이콘"
    assert contest.field == "알고리즘, 코드 제출 평가"
    assert contest.apply_end == date(2026, 11, 2)
    assert contest.source_url.endswith("/236771/overview/description")


def _linkareer_node(node_id: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "title": "[과기부] 6G 공모전",
        "organizationName": "과학기술정보통신부",
        "recruitStartAt": 1791385200000,  # 2026-10-08 00:00 KST
        "recruitCloseAt": 1794149999999,  # 2026-11-08 23:59:59 KST
        "homepageURL": None,
        "categories": [{"name": "기획/아이디어"}],
        "targets": [{"name": "대학생"}],
        "regions": [{"name": "서울"}],
    }


@pytest.mark.feature("F2-1")
def test_linkareer_paginates_until_total() -> None:
    pages = {1: [_linkareer_node("1"), _linkareer_node("2")], 2: [_linkareer_node("3")]}
    requested: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = json.loads(request.content)["variables"]["page"]
        requested.append(page)
        return httpx.Response(
            200, json={"data": {"activities": {"totalCount": 3, "nodes": pages[page]}}}
        )

    contests = LinkareerSource(_client(handler), delay_seconds=0).fetch()

    assert requested == [1, 2]
    assert len(contests) == 3
    contest = contests[0]
    assert (contest.apply_start, contest.apply_end) == (date(2026, 10, 8), date(2026, 11, 8))
    assert contest.eligibility_text == "대상: 대학생 / 지역: 서울"
    assert contest.apply_url == "https://linkareer.com/activity/1"


@pytest.mark.feature("F2-1")
def test_linkareer_graphql_error_raises() -> None:
    body = {"errors": [{"message": "boom"}]}
    source = LinkareerSource(_client(lambda _: httpx.Response(200, json=body)), delay_seconds=0)
    with pytest.raises(ValueError, match="boom"):
        source.fetch()
