"""수집 흐름 테스트: 소스 실패 격리, 병합, 빠진 출처 정리, 삭제 기준, 분류 (F2-1, F2-2, F3-1)."""

from dataclasses import replace
from datetime import date, datetime

import pytest

from app.contests.crawlers.base import RawContest
from app.contests.repository import ContestRepository, SaveOutcome
from app.contests.rules import ContestTags, Listing, belongs_to, load_rules
from app.contests.service import collect, purge_closed

TODAY = date(2026, 10, 23)
RULES = load_rules()


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
        apply_url="https://example.com",
        source=source,
        source_url=f"https://example.com/{title}",
    )


class FakeRepository(ContestRepository):
    """저장 내용을 메모리에 기록하는 가짜 대회 저장소. 같은 대회 판정은 실제 규칙을 쓴다."""

    def __init__(self) -> None:
        """대회 묶음·빠진 출처 정리 요청·삭제 기준일을 기록할 곳을 만든다."""
        self.bundles: list[list[Listing]] = []
        self.prunes: list[tuple[str, set[str]]] = []
        self.purge_cutoffs: list[date] = []

    @property
    def saved(self) -> list[Listing]:
        """묶음마다 처음 저장한 출처."""
        return [bundle[0] for bundle in self.bundles]

    def delete_closed_before(self, cutoff: date) -> int:
        """삭제 기준일만 기록한다."""
        self.purge_cutoffs.append(cutoff)
        return 0

    def save(self, listing: Listing, now: datetime) -> SaveOutcome:
        """같은 대회 묶음이 있으면 출처로 붙이고, 없으면 새 묶음으로 기록한다."""
        for bundle in self.bundles:
            if belongs_to(listing, bundle):
                bundle.append(listing)
                return SaveOutcome.MERGED
        self.bundles.append([listing])
        return SaveOutcome.INSERTED

    def prune_missing(self, source: str, seen_urls: set[str], now: datetime) -> int:
        """빠진 출처 정리 요청만 기록한다."""
        self.prunes.append((source, seen_urls))
        return 0


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
    # 실패한 소스는 빠진 출처를 정리하지 않는다 (F2-1 기존 데이터 유지)
    assert [source for source, _ in repo.prunes] == ["ok"]


@pytest.mark.feature("F2-2")
def test_same_contest_from_two_sources_is_merged() -> None:
    """두 소스의 같은 대회는 주최 표기가 달라도 하나로 병합된다."""
    repo = FakeRepository()

    collect(
        [
            FakeSource("a", [_contest("a", "2026 AI 해커톤")]),
            FakeSource("b", [replace(_contest("b", "[주최사] 2026 AI-해커톤!"), host="")]),
        ],
        repo,
        TODAY,
        RULES,
    )

    assert len(repo.saved) == 1


@pytest.mark.feature("F2-2")
def test_successful_collect_reports_every_fetched_source_url() -> None:
    """성공한 수집은 받은 원문 전부(삭제 기준을 넘겨 건너뛴 것 포함)를 넘겨 빠진 출처만 정리한다."""
    repo = FakeRepository()
    kept = replace(_contest("a", "열린 대회"), source_url="https://a.example.com/1")
    old = replace(kept, title="작년 대회", source_url="https://a.example.com/2")
    old = replace(old, apply_end=date(2026, 10, 19))

    collect([FakeSource("a", [kept, old])], repo, TODAY, RULES)

    assert repo.prunes == [("a", {"https://a.example.com/1", "https://a.example.com/2"})]


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
    assert [c.title for c in repo.saved] == ["막 마감"]


@pytest.mark.feature("F3-1")
def test_collected_contest_is_classified_before_save() -> None:
    """저장 전에 소스 태그·제목·소스 기본값으로 분야·형식 태그를 붙인다."""
    repo = FakeRepository()
    tagged = replace(_contest("dev_event", "대회 A 해커톤"), field="AI, 블록체인")
    untagged = replace(_contest("dev_event", "대회 B"), field="기술일반")
    by_default = replace(_contest("devpost", "Kiroween"), field="Open Ended")

    collect(
        [FakeSource("dev_event", [tagged, untagged]), FakeSource("devpost", [by_default])],
        repo,
        TODAY,
        RULES,
    )

    by_title = {listing.title: listing.tags for listing in repo.saved}
    assert by_title["대회 A 해커톤"] == ContestTags(
        field=frozenset({"AI", "개발"}), format=frozenset({"해커톤"})
    )
    assert by_title["대회 B"] == ContestTags()
    assert by_title["Kiroween"] == ContestTags(
        field=frozenset({"개발"}), format=frozenset({"해커톤"})
    )
