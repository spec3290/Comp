"""접수 상태·D-day·삭제 기준일 계산 테스트 (F2-3)."""

from datetime import date

import pytest

from app.contests.rules import ApplyPeriod, ContestStatus, purge_cutoff

PERIOD = ApplyPeriod(start=date(2026, 11, 1), end=date(2026, 11, 10))


@pytest.mark.feature("F2-3")
@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 10, 31), ContestStatus.UPCOMING),
        (date(2026, 11, 1), ContestStatus.OPEN),
        (date(2026, 11, 10), ContestStatus.OPEN),
        (date(2026, 11, 11), ContestStatus.CLOSED),
    ],
)
def test_status_boundaries(today: date, expected: ContestStatus) -> None:
    """시작 전날·시작일·마감일·마감 다음 날의 상태 경계."""
    assert PERIOD.status(today) == expected


@pytest.mark.feature("F2-3")
def test_missing_start_is_open_until_deadline() -> None:
    """시작일이 없으면 마감일까지 접수 중이다."""
    period = ApplyPeriod(start=None, end=date(2026, 11, 10))
    assert period.status(date(2020, 1, 1)) == ContestStatus.OPEN
    assert period.status(date(2026, 11, 11)) == ContestStatus.CLOSED


@pytest.mark.feature("F2-4")
def test_d_day() -> None:
    """D-day 는 마감일까지 남은 날수이고 지나면 음수다."""
    assert PERIOD.d_day(date(2026, 11, 7)) == 3
    assert PERIOD.d_day(date(2026, 11, 10)) == 0
    assert PERIOD.d_day(date(2026, 11, 12)) == -2


@pytest.mark.feature("F2-3")
def test_closed_contest_is_kept_for_three_days() -> None:
    """마감 대회는 3일 동안 남긴다."""
    deadline = date(2026, 11, 10)
    assert not deadline < purge_cutoff(date(2026, 11, 13))
    assert deadline < purge_cutoff(date(2026, 11, 14))
