"""대회 도메인 규칙 (순수 파이썬). 상태·D-day는 저장하지 않고 날짜로부터 계산한다.

프레임워크·DB·HTTP 를 import 하지 않는다 (AC-1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class ContestStatus(StrEnum):
    UPCOMING = "예정"
    OPEN = "접수 중"
    CLOSED = "마감"


@dataclass(frozen=True)
class ApplyPeriod:
    start: date
    end: date

    def status(self, today: date) -> ContestStatus:
        if today < self.start:
            return ContestStatus.UPCOMING
        if today > self.end:
            return ContestStatus.CLOSED
        return ContestStatus.OPEN

    def d_day(self, today: date) -> int:
        """접수 마감까지 남은 일수. 마감 당일 0, 지나면 음수."""
        return (self.end - today).days
