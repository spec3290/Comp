from dataclasses import replace
from datetime import date, datetime

import pytest

from app.contests.crawlers.base import RawContest
from app.contests.repository import ContestRepository, SaveOutcome
from app.contests.rules import merge_key
from app.contests.service import collect, purge_closed

TODAY = date(2026, 10, 23)


def _contest(source: str, title: str = "2026 AI 해커톤") -> RawContest:
    return RawContest(
        title=title,
        host="주최사",
        field="AI",
        apply_start=None,
        apply_end=date(2026, 11, 1),
        event_start=None,
        event_end=None,
        eligibility_text="",
        apply_url="https://example.com",
        source=source,
        source_url="https://example.com",
    )


class FakeRepository(ContestRepository):
    def __init__(self) -> None:
        self.saved: dict[str, RawContest] = {}
        self.purge_cutoffs: list[date] = []

    def delete_closed_before(self, cutoff: date) -> int:
        self.purge_cutoffs.append(cutoff)
        return 0

    def save(self, contest: RawContest, merge_key: str, now: datetime) -> SaveOutcome:
        if merge_key in self.saved:
            return SaveOutcome.MERGED
        self.saved[merge_key] = contest
        return SaveOutcome.INSERTED


class FakeSource:
    def __init__(self, name: str, contests: list[RawContest] | None = None) -> None:
        self.name = name
        self._contests = contests

    def fetch(self) -> list[RawContest]:
        if self._contests is None:
            raise ConnectionError("site down")
        return self._contests


@pytest.mark.feature("F2-1")
def test_failed_source_does_not_block_others() -> None:
    repo = FakeRepository()

    broken, ok = collect([FakeSource("broken"), FakeSource("ok", [_contest("ok")])], repo, TODAY)

    assert broken.error == "ConnectionError: site down"
    assert ok.error is None
    assert ok.outcomes[SaveOutcome.INSERTED] == 1
    assert len(repo.saved) == 1


@pytest.mark.feature("F2-2")
def test_same_contest_from_two_sources_is_merged() -> None:
    repo = FakeRepository()

    collect(
        [
            FakeSource("a", [_contest("a", "2026 AI 해커톤")]),
            FakeSource("b", [_contest("b", "[주최사] 2026 AI-해커톤!")]),
        ],
        repo,
        TODAY,
    )

    assert len(repo.saved) == 1


@pytest.mark.feature("F2-2")
def test_merge_key_normalizes_title_and_host() -> None:
    end = date(2026, 11, 1)
    assert merge_key("[링커리어] 2026 AI 해커톤", "주최 사", end) == merge_key(
        "2026 ai-해커톤", "주최사", end
    )
    assert merge_key("2026 AI 해커톤", "주최사", end) != merge_key(
        "2026 AI 해커톤", "주최사", date(2026, 11, 2)
    )


@pytest.mark.feature("F2-3")
def test_purge_deletes_contests_closed_more_than_three_days_ago() -> None:
    repo = FakeRepository()

    purge_closed(repo, date(2026, 10, 23))

    assert repo.purge_cutoffs == [date(2026, 10, 20)]


@pytest.mark.feature("F2-3")
def test_contests_already_past_purge_cutoff_are_not_saved() -> None:
    repo = FakeRepository()
    old = replace(_contest("a", "작년 대회"), apply_end=date(2026, 10, 19))
    recent = replace(_contest("a", "막 마감"), apply_end=date(2026, 10, 20))

    (result,) = collect([FakeSource("a", [old, recent])], repo, TODAY)

    assert result.skipped == 1
    assert [c.title for c in repo.saved.values()] == ["막 마감"]
