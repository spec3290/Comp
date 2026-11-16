"""접수 상태·D-day·삭제 기준일 계산 테스트 (F2-3)."""

from datetime import date

import pytest

from app.contests.rules import OPEN_ENDED, ApplyPeriod, ContestStatus, purge_cutoff

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


@pytest.mark.feature("F2-3")
def test_unknown_deadline_has_no_d_day_and_closes_after_event() -> None:
    """마감일을 모르면 D-day 가 없고, 대회가 끝난 다음 날부터 마감이다 (Q-35)."""
    period = ApplyPeriod(None, None, event_start=date(2026, 11, 20), event_end=date(2026, 11, 21))
    assert period.d_day(date(2026, 11, 1)) is None
    assert period.closes_on == date(2026, 11, 21)
    assert period.status(date(2026, 11, 21)) == ContestStatus.OPEN
    assert period.status(date(2026, 11, 22)) == ContestStatus.CLOSED


@pytest.mark.feature("F2-3")
def test_unknown_deadline_without_event_end_closes_after_event_start() -> None:
    """대회 끝나는 날도 없으면 시작한 다음 날부터 마감이다 (Q-35)."""
    period = ApplyPeriod(None, None, event_start=date(2026, 11, 20))
    assert period.status(date(2026, 11, 21)) == ContestStatus.CLOSED


@pytest.mark.feature("F2-3")
def test_contest_without_any_date_stays_open() -> None:
    """날짜가 하나도 없으면 마감되지 않고 삭제 기준에도 걸리지 않는다 (Q-35)."""
    period = ApplyPeriod(None, None)
    assert period.closes_on == OPEN_ENDED
    assert period.status(date(2030, 1, 1)) == ContestStatus.OPEN
    assert not period.closes_on < purge_cutoff(date(2030, 1, 1))


@pytest.mark.feature("F2-3")
def test_known_deadline_wins_over_event_dates() -> None:
    """마감일이 있으면 대회 기간과 상관없이 마감일로 판정한다."""
    period = ApplyPeriod(None, date(2026, 11, 10), event_end=date(2026, 11, 30))
    assert period.closes_on == date(2026, 11, 10)
    assert period.status(date(2026, 11, 11)) == ContestStatus.CLOSED
