"""대회 도메인 규칙 (순수 파이썬). 상태·D-day는 저장하지 않고 날짜로부터 계산한다.

프레임워크·DB·HTTP 를 import 하지 않는다 (AC-1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

_LEADING_BRACKET = re.compile(r"^\s*\[[^\]]*\]\s*")
_NON_WORD = re.compile(r"[\W_]+")

# 접수 마감 후 이 일수가 지나면 삭제한다 (Q-3)
RETENTION_DAYS_AFTER_CLOSE = 3


class ContestStatus(StrEnum):
    """API 에 그대로 쓰는 값. 화면 표기는 예정 / 접수 중 / 마감."""

    UPCOMING = "upcoming"
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True)
class ApplyPeriod:
    start: date | None  # 없으면 이미 접수 중으로 본다 (Q-6)
    end: date

    def status(self, today: date) -> ContestStatus:
        if self.start is not None and today < self.start:
            return ContestStatus.UPCOMING
        if today > self.end:
            return ContestStatus.CLOSED
        return ContestStatus.OPEN

    def d_day(self, today: date) -> int:
        """접수 마감까지 남은 일수. 마감 당일 0, 지나면 음수."""
        return (self.end - today).days


class SortOrder(StrEnum):
    DEADLINE = "deadline"  # 마감 임박순
    LATEST = "latest"  # 최근 수집순


DEFAULT_STATUSES = frozenset({ContestStatus.OPEN, ContestStatus.UPCOMING})


@dataclass(frozen=True)
class ContestQuery:
    """목록 조회 조건 (F2-3). 같은 항목 안의 값은 OR, 항목 사이는 AND (Q-9)."""

    statuses: frozenset[ContestStatus] = DEFAULT_STATUSES
    fields: tuple[str, ...] = ()
    keyword: str | None = None
    sort: SortOrder = SortOrder.DEADLINE
    page: int = 1
    size: int = 20


def normalize_text(text: str) -> str:
    """중복 비교용 정규화: 맨 앞 [접두어] 제거, 공백·특수문자 제거, 소문자 (Q-2 기본값)."""
    return _NON_WORD.sub("", _LEADING_BRACKET.sub("", text)).lower()


def merge_key(title: str, host: str, apply_end: date) -> str:
    """F2-2 병합 기준: 제목 + 주최 + 마감일."""
    return f"{normalize_text(title)}|{normalize_text(host)}|{apply_end.isoformat()}"


def purge_cutoff(today: date) -> date:
    """접수 마감일이 이 날짜보다 이르면 삭제 대상이다. 마감 뒤 3일은 '마감'으로 남는다 (Q-3)."""
    return today - timedelta(days=RETENTION_DAYS_AFTER_CLOSE)
