"""링커리어 크롤러. linkareer.com 프런트가 쓰는 GraphQL API 로 모집 중인 공모전을 받는다."""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any

import httpx

from app.contests.crawlers.base import RawContest, convert_each
from app.core.time import KST

NAME = "linkareer"
URL = "https://api.linkareer.com/graphql"
DETAIL_URL = "https://linkareer.com/activity/{id}"
PAGE_SIZE = 50
MAX_PAGES = 40
# activityTypeID 3 = 공모전
QUERY = """
query Contests($page: Int!, $size: Int!) {
  activities(
    filterBy: {activityTypeID: "3", status: OPEN}
    orderBy: {direction: DESC, field: CREATED_AT}
    pagination: {page: $page, pageSize: $size}
  ) {
    totalCount
    nodes {
      id title organizationName recruitStartAt recruitCloseAt homepageURL
      categories { name } targets { name } regions { name }
    }
  }
}
"""


class LinkareerSource:
    name = NAME

    def __init__(self, client: httpx.Client, delay_seconds: float = 1.0) -> None:
        self._client = client
        self._delay = delay_seconds

    def fetch(self) -> list[RawContest]:
        nodes: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            total, page_nodes = self._fetch_page(page)
            nodes.extend(page_nodes)
            if not page_nodes or len(nodes) >= total:
                break
            time.sleep(self._delay)
        return convert_each(nodes, _to_contest, NAME)

    def _fetch_page(self, page: int) -> tuple[int, list[dict[str, Any]]]:
        res = self._client.post(
            URL, json={"query": QUERY, "variables": {"page": page, "size": PAGE_SIZE}}
        )
        res.raise_for_status()
        body = res.json()
        if body.get("errors"):
            raise ValueError(f"linkareer: GraphQL 오류 {body['errors'][0].get('message')}")
        activities = body["data"]["activities"]
        return int(activities["totalCount"]), list(activities["nodes"])


def _to_date(millis: int | None) -> date | None:
    return None if millis is None else datetime.fromtimestamp(millis / 1000, KST).date()


def _names(items: list[dict[str, Any]] | None) -> list[str]:
    return [item["name"] for item in items or []]


def _eligibility(node: dict[str, Any]) -> str:
    parts = []
    if targets := _names(node.get("targets")):
        parts.append(f"대상: {', '.join(targets)}")
    if regions := _names(node.get("regions")):
        parts.append(f"지역: {', '.join(regions)}")
    return " / ".join(parts)


def _to_contest(node: dict[str, Any]) -> RawContest | None:
    apply_end = _to_date(node["recruitCloseAt"])
    if apply_end is None:
        return None
    detail_url = DETAIL_URL.format(id=node["id"])
    return RawContest(
        title=node["title"].strip(),
        host=(node.get("organizationName") or "").strip(),
        field=", ".join(_names(node.get("categories"))),
        apply_start=_to_date(node.get("recruitStartAt")),
        apply_end=apply_end,
        event_start=None,
        event_end=None,
        eligibility_text=_eligibility(node),
        apply_url=node.get("homepageURL") or detail_url,
        source=NAME,
        source_url=detail_url,
    )
