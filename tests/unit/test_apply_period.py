from datetime import date

import pytest

from app.contests.rules import ApplyPeriod, ContestStatus

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


@pytest.mark.feature("F2-4")
def test_d_day() -> None:
    assert PERIOD.d_day(date(2026, 11, 7)) == 3
    assert PERIOD.d_day(date(2026, 11, 10)) == 0
    assert PERIOD.d_day(date(2026, 11, 12)) == -2
