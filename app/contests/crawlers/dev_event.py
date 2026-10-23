"""Dev Event 크롤러. 행사 목록 페이지의 __NEXT_DATA__ JSON 에서 '대회' 태그 행사만 고른다.

페이지에는 이번 달부터 몇 달 치 행사가 실려 있다. event_time_type 이 RECRUIT 면 기간이
모집(접수) 기간이고, DATE 면 행사 당일 일정이다.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import httpx

from app.contests.crawlers.base import RawContest, convert_each

NAME = "dev_event"
URL = "https://dev-event.vercel.app/events"
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
CONTEST_TAG = "대회"
NON_FIELD_TAGS = frozenset({CONTEST_TAG, "온라인", "오프라인", "무료", "유료"})


class DevEventSource:
    name = NAME

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def fetch(self) -> list[RawContest]:
        res = self._client.get(URL)
        res.raise_for_status()
        return parse_page(res.text)


def parse_page(html: str) -> list[RawContest]:
    match = NEXT_DATA_RE.search(html)
    if match is None:
        raise ValueError("dev_event: __NEXT_DATA__ 를 찾지 못했다")
    months = json.loads(match.group(1))["props"]["pageProps"]["fallbackData"]
    events = [event for month in months for event in month["dev_event"]]
    return convert_each(events, _to_contest, NAME)


def _to_contest(event: dict[str, Any]) -> RawContest | None:
    tags = [tag["tag_name"] for tag in event["tags"]]
    if CONTEST_TAG not in tags:
        return None
    start = date.fromisoformat(event["start_date_time"][:10])
    end = date.fromisoformat(event["end_date_time"][:10])
    recruit = event["event_time_type"] == "RECRUIT"
    link = event["event_link"]
    return RawContest(
        title=event["title"].strip(),
        host=event["organizer"].strip(),
        field=", ".join(tag for tag in tags if tag not in NON_FIELD_TAGS),
        apply_start=start if recruit else None,
        # 행사 당일 일정(DATE)만 있으면 시작일을 접수 마감으로 본다 (가정, Q-14)
        apply_end=end if recruit else start,
        event_start=None if recruit else start,
        event_end=None if recruit else end,
        eligibility_text=event.get("description") or "",
        apply_url=link,
        source=NAME,
        source_url=link,
    )
