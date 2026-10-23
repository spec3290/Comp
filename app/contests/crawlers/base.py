"""크롤러 공통 계약 (F2-1).

사이트별 크롤러는 이 Protocol 을 구현한다. 정규화·분류는 service/rules 몫.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol


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
