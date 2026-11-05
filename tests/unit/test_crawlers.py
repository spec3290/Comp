import json
from datetime import date
from typing import Any

import httpx
import pytest

from app.contests.crawlers.dacon import DaconSource, parse_apply_period
from app.contests.crawlers.dev_event import DevEventSource
from app.contests.crawlers.devpost import DevpostSource, parse_period


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
            _dev_event(id=5, organizer=None),
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
    # 일정에 참가 기간이 없으면 대회 기간을 접수 기간으로 쓴다
    assert (contest.apply_start, contest.apply_end) == (date(2026, 10, 12), date(2026, 11, 2))
    assert (contest.event_start, contest.event_end) == (date(2026, 10, 12), date(2026, 11, 2))
    assert contest.source_url.endswith("/236771/overview/description")


@pytest.mark.feature("F2-1")
@pytest.mark.parametrize(
    ("schedule", "expected"),
    [
        (
            "<p>- 참가 기간 : 2026년 09월 30일(수) ~ 2026년 11월 02일(월) 10:00</p>"
            "<p>- 대회 기간 : 2026년 10월 12일(월) 10:00 ~ 2026년 11월 02일(월) 10:00</p>",
            (date(2026, 9, 30), date(2026, 11, 2)),
        ),
        (
            "- 대회 기간: 2026.07.16 (목) ~ 2026.08.20 (목)<br>"
            "- 참가 신청 기간: 2026.07.16 (목) 오전 10시 ~ 2026.08.17 (월) 오후 11시 59분",
            (date(2026, 7, 16), date(2026, 8, 17)),
        ),
        (
            "- 참가자 모집 기간 : 2025년 9월 29일 (월) ~ 2025년 11월 14일 (금)",
            (date(2025, 9, 29), date(2025, 11, 14)),
        ),
        ("- 대회 기간 : 2026년 10월 12일 ~ 2026년 11월 02일", None),
        ("- 참가 기간 : 2026년 13월 01일 ~ 2026년 11월 02일", None),
        (None, None),
    ],
)
def test_dacon_parses_apply_period_from_schedule(
    schedule: str | None, expected: tuple[date, date] | None
) -> None:
    assert parse_apply_period(schedule) == expected


def _hackathon(hackathon_id: int, **overrides: Any) -> dict[str, Any]:
    item = {
        "id": hackathon_id,
        "title": " PayPal AI Hackathon ",
        "organization_name": "PayPal",
        "url": f"https://hack{hackathon_id}.devpost.com/",
        "submission_period_dates": "Oct 01 - Nov 12, 2026",
        "displayed_location": {"icon": "globe", "location": "Online"},
        "invite_only": False,
        "themes": [{"id": 6, "name": "Machine Learning/AI"}, {"id": 25, "name": "Web"}],
        "thumbnail_url": "//cdn.example.com/thumb.png",
    }
    return item | overrides


@pytest.mark.feature("F2-1")
def test_devpost_paginates_each_status_and_uses_hackathon_page_as_apply_url() -> None:
    pages = {
        ("open", "1"): [_hackathon(1), _hackathon(2)],
        ("open", "2"): [_hackathon(3)],
        ("upcoming", "1"): [_hackathon(4)],
    }
    totals = {"open": 3, "upcoming": 1}
    requested: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.url.params["status[]"], request.url.params["page"])
        requested.append(key)
        body = {"hackathons": pages.get(key, []), "meta": {"total_count": totals[key[0]]}}
        return httpx.Response(200, json=body)

    contests = DevpostSource(_client(handler), delay_seconds=0).fetch()

    assert requested == [("open", "1"), ("open", "2"), ("upcoming", "1")]
    assert len(contests) == 4
    contest = contests[0]
    assert contest.title == "PayPal AI Hackathon"
    assert contest.host == "PayPal"
    assert contest.field == "Machine Learning/AI, Web"
    assert (contest.apply_start, contest.apply_end) == (date(2026, 10, 1), date(2026, 11, 12))
    assert contest.apply_url == contest.source_url == "https://hack1.devpost.com/"


@pytest.mark.feature("F2-1")
def test_devpost_skips_offline_invite_only_and_broken_hackathons() -> None:
    items = [
        _hackathon(1),
        _hackathon(2, displayed_location={"icon": "map-marker", "location": "Seattle"}),
        _hackathon(3, invite_only=True),
        _hackathon(4, submission_period_dates="TBD"),
        _hackathon(5, organization_name=None),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        found = items if request.url.params["status[]"] == "open" else []
        return httpx.Response(200, json={"hackathons": found, "meta": {"total_count": len(found)}})

    contests = DevpostSource(_client(handler), delay_seconds=0).fetch()

    assert [c.apply_url for c in contests] == [
        "https://hack1.devpost.com/",
        "https://hack5.devpost.com/",
    ]
    assert contests[1].host == "Devpost"


@pytest.mark.feature("F2-1")
def test_devpost_http_error_raises() -> None:
    source = DevpostSource(_client(lambda _: httpx.Response(503)), delay_seconds=0)
    with pytest.raises(httpx.HTTPStatusError):
        source.fetch()


@pytest.mark.feature("F2-1")
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Oct 01 - Nov 12, 2026", (date(2026, 10, 1), date(2026, 11, 12))),
        ("Nov 01 - 05, 2026", (date(2026, 11, 1), date(2026, 11, 5))),
        ("Jul 16, 2026 - Jan 15, 2027", (date(2026, 7, 16), date(2027, 1, 15))),
        ("Dec 31, 2026", (date(2026, 12, 31), date(2026, 12, 31))),
    ],
)
def test_devpost_parses_submission_period(text: str, expected: tuple[date, date]) -> None:
    assert parse_period(text) == expected


@pytest.mark.feature("F2-1")
@pytest.mark.parametrize("text", ["TBD", "Foo 01 - 05, 2026", "Feb 30, 2027"])
def test_devpost_rejects_unknown_period(text: str) -> None:
    with pytest.raises(ValueError):
        parse_period(text)
