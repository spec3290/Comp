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
    assert PERIOD.status(today) == expected


@pytest.mark.feature("F2-3")
def test_missing_start_is_open_until_deadline() -> None:
    period = ApplyPeriod(start=None, end=date(2026, 11, 10))
    assert period.status(date(2020, 1, 1)) == ContestStatus.OPEN
    assert period.status(date(2026, 11, 11)) == ContestStatus.CLOSED


@pytest.mark.feature("F2-4")
def test_d_day() -> None:
    assert PERIOD.d_day(date(2026, 11, 7)) == 3
    assert PERIOD.d_day(date(2026, 11, 10)) == 0
    assert PERIOD.d_day(date(2026, 11, 12)) == -2


@pytest.mark.feature("F2-3")
def test_closed_contest_is_kept_for_three_days() -> None:
    deadline = date(2026, 11, 10)
    assert not deadline < purge_cutoff(date(2026, 11, 13))
    assert deadline < purge_cutoff(date(2026, 11, 14))
