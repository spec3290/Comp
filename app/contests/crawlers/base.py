"""크롤러 공통 계약 (F2-1).

사이트별 크롤러는 이 Protocol 을 구현한다. 정규화·분류는 service/rules 몫.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "ContestCollector/0.1 (+https://github.com/spec3290/Comp)"


@dataclass(frozen=True)
class RawContest:
    """크롤러가 돌려주는 원시 대회."""

    title: str
    host: str
    field: str
    apply_start: date | None
    apply_end: date
    event_start: date | None
    event_end: date | None
    eligibility_text: str
    apply_url: str
    source: str
    source_url: str


class ContestSource(Protocol):
    name: str

    def fetch(self) -> list[RawContest]:
        """대회 목록을 가져온다. 실패 시 예외를 던지고, 호출자가 기존 데이터를 유지한다."""
        ...


def new_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)


def convert_each(
    items: Iterable[dict[str, Any]],
    convert: Callable[[dict[str, Any]], RawContest | None],
    source: str,
) -> list[RawContest]:
    """항목별로 변환한다. 형식이 깨진 항목은 건너뛰고 나머지는 살린다. None 은 대회가 아님."""
    contests: list[RawContest] = []
    for item in items:
        try:
            contest = convert(item)
        except (KeyError, TypeError, ValueError):
            logger.warning("%s: 형식이 다른 항목을 건너뜀 (id=%s)", source, item.get("id"))
            continue
        if contest is not None:
            contests.append(contest)
    return contests
