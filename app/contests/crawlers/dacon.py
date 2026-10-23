"""DACON 크롤러. dacon.io 프런트가 쓰는 공개 목록 API 를 그대로 호출한다.

응답에 과거 대회까지 전부(수백 건, 20MB 안팎) 담겨 와서 타임아웃을 길게 둔다.
대회 상세 페이지의 '일정' 탭 HTML 도 같은 응답의 `schedule` 에 들어 있다.
"""

from __future__ import annotations

import html
import re
from datetime import date
from typing import Any

import httpx

from app.contests.crawlers.base import RawContest, convert_each

NAME = "dacon"
URL = "https://newapi.dacon.io/competition/list"
DETAIL_URL = "https://dacon.io/competitions/official/{cpt_id}/overview/description"
DEFAULT_HOST = "데이콘"
TIMEOUT_SECONDS = 180

_TAG = re.compile(r"<[^>]+>")
_DATE = r"(\d{4})\s*[.년]\s*(\d{1,2})\s*[.월]\s*(\d{1,2})"
# 예: "참가 기간 : 2026년 09월 30일(수) ~ 2026년 11월 02일(월) 10:00",
#     "참가 신청 기간: 2026.07.16 (목) 오전 10시 ~ 2026.08.17 (월)", "참가자 모집 기간 : ..."
_APPLY_PERIOD = re.compile(
    rf"(?:참가\s*(?:신청|자\s*모집)?|접수|신청)\s*기간\s*[:：]?\s*{_DATE}[^~]*~\s*{_DATE}"
)


class DaconSource:
    name = NAME

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def fetch(self) -> list[RawContest]:
        res = self._client.get(URL, params={"is_official": 1}, timeout=TIMEOUT_SECONDS)
        res.raise_for_status()
        return parse_competitions(res.json())


def parse_competitions(payload: dict[str, Any]) -> list[RawContest]:
    return convert_each(payload["data"], _to_contest, NAME)


def parse_apply_period(schedule_html: str | None) -> tuple[date, date] | None:
    """일정 HTML 에서 '참가(신청) 기간'을 찾는다. 없거나 형식이 다르면 None."""
    text = _TAG.sub(" ", html.unescape(schedule_html or ""))
    match = _APPLY_PERIOD.search(text)
    if match is None:
        return None
    y1, m1, d1, y2, m2, d2 = (int(g) for g in match.groups())
    try:
        return date(y1, m1, d1), date(y2, m2, d2)
    except ValueError:  # 오타 등으로 없는 날짜면 대회 기간으로 대신한다
        return None


def _to_contest(item: dict[str, Any]) -> RawContest | None:
    if not (item.get("is_public") and item.get("show_cpt_list")):
        return None
    url = DETAIL_URL.format(cpt_id=item["cpt_id"])
    keywords = (item.get("keyword") or "").split("|")
    event_start = date.fromisoformat(item["period_start"][:10])
    event_end = date.fromisoformat(item["period_end"][:10])
    # 참가 기간이 따로 없으면 대회 기간 중 언제든 참가할 수 있다 (Q-6)
    apply_start, apply_end = parse_apply_period(item.get("schedule")) or (event_start, event_end)
    return RawContest(
        title=item["name"].strip(),
        host=(item.get("sponsor") or DEFAULT_HOST).strip(),
        field=", ".join(k.strip() for k in keywords if k.strip()),
        apply_start=apply_start,
        apply_end=apply_end,
        event_start=event_start,
        event_end=event_end,
        eligibility_text="",
        apply_url=url,
        source=NAME,
        source_url=url,
    )
