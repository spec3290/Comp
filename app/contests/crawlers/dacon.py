"""DACON 크롤러. dacon.io 프런트가 쓰는 공개 목록 API 를 그대로 호출한다.

응답에 과거 대회까지 전부(수백 건, 20MB 안팎) 담겨 와서 타임아웃을 길게 둔다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from app.contests.crawlers.base import RawContest, convert_each

NAME = "dacon"
URL = "https://newapi.dacon.io/competition/list"
DETAIL_URL = "https://dacon.io/competitions/official/{cpt_id}/overview/description"
DEFAULT_HOST = "데이콘"
TIMEOUT_SECONDS = 180


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


def _to_contest(item: dict[str, Any]) -> RawContest | None:
    if not (item.get("is_public") and item.get("show_cpt_list")):
        return None
    url = DETAIL_URL.format(cpt_id=item["cpt_id"])
    keywords = (item.get("keyword") or "").split("|")
    return RawContest(
        title=item["name"].strip(),
        host=(item.get("sponsor") or DEFAULT_HOST).strip(),
        field=", ".join(k.strip() for k in keywords if k.strip()),
        # DACON 은 대회 기간 중 언제든 참가할 수 있어 대회 기간을 접수 기간으로 쓴다
        apply_start=date.fromisoformat(item["period_start"][:10]),
        apply_end=date.fromisoformat(item["period_end"][:10]),
        event_start=None,
        event_end=None,
        eligibility_text="",
        apply_url=url,
        source=NAME,
        source_url=url,
    )
