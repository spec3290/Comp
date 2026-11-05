"""Devpost 크롤러. devpost.com 해커톤 목록 페이지가 쓰는 공개 JSON API 로 받는다.

해커톤마다 Devpost 안에 공식 참가 페이지(`https://{slug}.devpost.com/`)가 있어 그대로
참가 URL 로 쓴다. 국내 사용자가 참가할 수 있게 온라인 · 공개 해커톤만 고른다 (Q-20).
제출 기간 안에는 언제든 등록·참가할 수 있어 제출 기간을 접수 기간으로 쓴다.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Any

import httpx

from app.contests.crawlers.base import RawContest, convert_each

logger = logging.getLogger(__name__)

NAME = "devpost"
URL = "https://devpost.com/api/hackathons"
DEFAULT_HOST = "Devpost"
STATUSES = ("open", "upcoming")
MAX_PAGES = 40  # 상태별. 한 페이지에 9건씩 온다
ONLINE_ICON = "globe"

_MONTHS = {
    name: number
    for number, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}
# 예: "Oct 01 - Nov 12, 2026", "Nov 01 - 05, 2026", "Jul 16, 2026 - Jan 15, 2027", "Dec 31, 2026"
_MONTH = "|".join(_MONTHS)
_PERIOD = re.compile(
    rf"(?P<m1>{_MONTH}) (?P<d1>\d{{1,2}})(?:, (?P<y1>\d{{4}}))?"
    rf"(?: - (?:(?P<m2>{_MONTH}) )?(?P<d2>\d{{1,2}}))?, (?P<y2>\d{{4}})"
)


class DevpostSource:
    name = NAME

    def __init__(self, client: httpx.Client, delay_seconds: float = 1.0) -> None:
        self._client = client
        self._delay = delay_seconds

    def fetch(self) -> list[RawContest]:
        items: list[dict[str, Any]] = []
        for status in STATUSES:
            items.extend(self._fetch_status(status))
        return convert_each(items, _to_contest, NAME)

    def _fetch_status(self, status: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            res = self._client.get(URL, params={"status[]": status, "page": page})
            res.raise_for_status()
            body = res.json()
            page_items = list(body["hackathons"])
            items.extend(page_items)
            if not page_items or len(items) >= int(body["meta"]["total_count"]):
                break
            time.sleep(self._delay)
        else:
            logger.warning(
                "devpost: %s 최대 %d페이지에서 중단 (%d건)", status, MAX_PAGES, len(items)
            )
        return items


def parse_period(text: str) -> tuple[date, date]:
    """제출 기간 문자열을 (시작일, 마감일)로 바꾼다. 형식이 다르면 ValueError."""
    match = _PERIOD.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"devpost: 제출 기간 형식이 다르다: {text!r}")
    m1, d1, y1, m2, d2, y2 = match.group("m1", "d1", "y1", "m2", "d2", "y2")
    end = date(int(y2), _MONTHS[m2 or m1], int(d2 or d1))
    start = date(int(y1 or y2), _MONTHS[m1], int(d1))
    # "Dec 20 - Jan 05, 2027" 처럼 연도를 넘기면서 시작 연도를 생략한 경우
    if y1 is None and start > end:
        start = start.replace(year=start.year - 1)
    return start, end


def _to_contest(item: dict[str, Any]) -> RawContest | None:
    if item.get("invite_only") or item["displayed_location"].get("icon") != ONLINE_ICON:
        return None
    start, end = parse_period(item["submission_period_dates"])
    url = item["url"]
    return RawContest(
        title=item["title"].strip(),
        host=(item.get("organization_name") or DEFAULT_HOST).strip(),
        field=", ".join(theme["name"] for theme in item.get("themes") or []),
        apply_start=start,
        apply_end=end,
        event_start=start,
        event_end=end,
        eligibility_text="",
        apply_url=url,
        source=NAME,
        source_url=url,
    )
