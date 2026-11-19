"""알림 규칙 (F4-1, 순수 파이썬). 저장한 대회의 날짜로 오늘 보낼 알림을 정한다.

알림은 미리 예약해 두지 않고, 하루 한 번 실행할 때 그 시점의 최신 날짜로 계산한다(Q-32).
그래서 저장을 해제하거나 대회가 삭제되면 따로 취소하지 않아도 보내지 않는다.
보낼 날(마감 D-7 등)이 오늘인 알림만 보내고, 실행을 건너뛴 날의 알림은 나중에 보내지 않는다(Q-36).

프레임워크·DB·HTTP 를 import 하지 않는다 (AC-1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class NotificationKind(StrEnum):
    """알림 종류. 값은 API·저장 키로 그대로 쓰고, 회원이 종류마다 켜고 끈다 (Q-36)."""

    DEADLINE_D7 = "deadline_d7"
    DEADLINE_D3 = "deadline_d3"
    DEADLINE_D1 = "deadline_d1"
    EVENT_START_D1 = "event_start_d1"  # 발표일은 주는 소스가 없어 시작일만 (Q-36)


# 마감 알림 종류별로 접수 마감까지 남은 일수
_DEADLINE_DAYS = {
    NotificationKind.DEADLINE_D7: 7,
    NotificationKind.DEADLINE_D3: 3,
    NotificationKind.DEADLINE_D1: 1,
}

ALL_KINDS = frozenset(NotificationKind)


@dataclass(frozen=True)
class ContestDates:
    """알림 계산에 쓰는 대회 날짜. 접수 마감일을 모르면 `apply_end` 가 None 이다 (Q-35)."""

    apply_end: date | None
    event_start: date | None


@dataclass(frozen=True)
class DueNotification:
    """오늘 보낼 알림 한 건과 그 알림이 가리키는 날짜(마감일·시작일)."""

    kind: NotificationKind
    target_date: date


def due_notifications(
    dates: ContestDates, today: date, enabled: frozenset[NotificationKind] = ALL_KINDS
) -> list[DueNotification]:
    """오늘 보낼 알림 목록. 회원이 끈 종류와 마감일 미확인 대회의 마감 알림은 뺀다 (Q-35)."""
    due: list[DueNotification] = []
    if dates.apply_end is not None:
        left = (dates.apply_end - today).days
        due += [
            DueNotification(kind, dates.apply_end)
            for kind, days in _DEADLINE_DAYS.items()
            if days == left
        ]
    if dates.event_start is not None and (dates.event_start - today).days == 1:
        due.append(DueNotification(NotificationKind.EVENT_START_D1, dates.event_start))
    return [d for d in due if d.kind in enabled]


def message(kind: NotificationKind, title: str, target_date: date) -> str:
    """알림 목록에 보여 줄 문장. 남은 일수는 보낸 시점 기준이라 종류에서 정해진다."""
    when = f"{target_date.month}월 {target_date.day}일"
    if kind is NotificationKind.EVENT_START_D1:
        return f"'{title}' 대회가 내일({when}) 시작해요."
    return f"'{title}' 접수 마감 {_DEADLINE_DAYS[kind]}일 전이에요. ({when} 마감)"
