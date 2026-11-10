"""수집 흐름 테스트: 소스 실패 격리, 병합, 삭제 기준, 분류 (F2-1, F2-2, F3-1)."""

from dataclasses import replace
from datetime import date, datetime

import pytest

from app.contests.crawlers.base import RawContest
from app.contests.repository import ContestRepository, SaveOutcome
from app.contests.rules import Dimension, EligibilityTags, KeywordRule, merge_key
from app.contests.service import collect, purge_closed

TODAY = date(2026, 10, 23)
RULES = (KeywordRule(Dimension.EDUCATION, "대학생", ("대학생",)),)


def _contest(source: str, title: str = "2026 AI 해커톤") -> RawContest:
    """제목만 바꿔 쓰는 테스트용 대회."""
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
    """저장 내용을 메모리에 기록하는 가짜 대회 저장소."""

    def __init__(self) -> None:
        """저장한 대회·태그·삭제 기준일을 기록할 곳을 만든다."""
        self.saved: dict[str, RawContest] = {}
        self.tags: dict[str, EligibilityTags] = {}
        self.purge_cutoffs: list[date] = []

    def delete_closed_before(self, cutoff: date) -> int:
        """삭제 기준일만 기록한다."""
        self.purge_cutoffs.append(cutoff)
        return 0

    def save(
        self, contest: RawContest, merge_key: str, now: datetime, *, tags: EligibilityTags
    ) -> SaveOutcome:
        """병합 키가 이미 있으면 병합, 없으면 새로 저장한 것으로 기록한다."""
        if merge_key in self.saved:
            return SaveOutcome.MERGED
        self.saved[merge_key] = contest
        self.tags[merge_key] = tags
        return SaveOutcome.INSERTED


class FakeSource:
    """고정 목록을 돌려주거나 실패하는 가짜 수집 소스."""

    def __init__(self, name: str, contests: list[RawContest] | None = None) -> None:
        """목록이 None 이면 fetch 가 실패한다."""
        self.name = name
        self._contests = contests

    def fetch(self) -> list[RawContest]:
        """목록을 돌려주거나 연결 오류를 던진다."""
        if self._contests is None:
            raise ConnectionError("site down")
        return self._contests


@pytest.mark.feature("F2-1")
def test_failed_source_does_not_block_others() -> None:
    """한 소스가 실패해도 다른 소스는 저장되고 실패는 결과에 남는다."""
    repo = FakeRepository()

    broken, ok = collect(
        [FakeSource("broken"), FakeSource("ok", [_contest("ok")])], repo, TODAY, RULES
    )

    assert broken.error == "ConnectionError: site down"
    assert ok.error is None
    assert ok.outcomes[SaveOutcome.INSERTED] == 1
    assert len(repo.saved) == 1


@pytest.mark.feature("F2-2")
def test_same_contest_from_two_sources_is_merged() -> None:
    """두 소스의 같은 대회는 하나로 병합된다."""
    repo = FakeRepository()

    collect(
        [
            FakeSource("a", [_contest("a", "2026 AI 해커톤")]),
            FakeSource("b", [_contest("b", "[주최사] 2026 AI-해커톤!")]),
        ],
        repo,
        TODAY,
        RULES,
    )

    assert len(repo.saved) == 1


@pytest.mark.feature("F2-2")
def test_merge_key_normalizes_title_and_host() -> None:
    """병합 키는 제목 머리말·공백·대소문자를 정규화하고 마감일을 구분한다."""
    end = date(2026, 11, 1)
    assert merge_key("[링커리어] 2026 AI 해커톤", "주최 사", end) == merge_key(
        "2026 ai-해커톤", "주최사", end
    )
    assert merge_key("2026 AI 해커톤", "주최사", end) != merge_key(
        "2026 AI 해커톤", "주최사", date(2026, 11, 2)
    )


@pytest.mark.feature("F2-3")
def test_purge_deletes_contests_closed_more_than_three_days_ago() -> None:
    """마감 3일이 지난 대회를 지우는 기준일을 계산한다."""
    repo = FakeRepository()

    purge_closed(repo, date(2026, 10, 23))

    assert repo.purge_cutoffs == [date(2026, 10, 20)]


@pytest.mark.feature("F2-3")
def test_contests_already_past_purge_cutoff_are_not_saved() -> None:
    """이미 삭제 기준을 넘긴 대회는 저장하지 않고 건너뛴 수로 센다."""
    repo = FakeRepository()
    old = replace(_contest("a", "작년 대회"), apply_end=date(2026, 10, 19))
    recent = replace(_contest("a", "막 마감"), apply_end=date(2026, 10, 20))

    (result,) = collect([FakeSource("a", [old, recent])], repo, TODAY, RULES)

    assert result.skipped == 1
    assert [c.title for c in repo.saved.values()] == ["막 마감"]


@pytest.mark.feature("F3-1")
def test_collected_contest_is_classified_before_save() -> None:
    """저장 전에 참가 자격 원문으로 자격 태그를 붙인다."""
    repo = FakeRepository()
    tagged = replace(_contest("a", "대회 A"), eligibility_text="참가 대상: 대학생")
    untagged = _contest("a", "대회 B")

    collect([FakeSource("a", [tagged, untagged])], repo, TODAY, RULES)

    by_title = {repo.saved[key].title: tags for key, tags in repo.tags.items()}
    assert by_title["대회 A"] == EligibilityTags(education=frozenset({"대학생"}))
    assert by_title["대회 B"] == EligibilityTags()
