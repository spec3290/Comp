"""대회 도메인 규칙 (순수 파이썬). 상태·D-day는 저장하지 않고 날짜로부터 계산한다.

프레임워크·DB·HTTP 를 import 하지 않는다 (AC-1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

_LEADING_BRACKET = re.compile(r"^\s*\[[^\]]*\]\s*")
_NON_WORD = re.compile(r"[\W_]+")


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


def normalize_text(text: str) -> str:
    """중복 비교용 정규화: 맨 앞 [접두어] 제거, 공백·특수문자 제거, 소문자 (Q-2 기본값)."""
    return _NON_WORD.sub("", _LEADING_BRACKET.sub("", text)).lower()


def merge_key(title: str, host: str, apply_end: date) -> str:
    """F2-2 병합 기준: 제목 + 주최 + 마감일."""
    return f"{normalize_text(title)}|{normalize_text(host)}|{apply_end.isoformat()}"
