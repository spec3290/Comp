"""F2-2 중복 제거를 실제 MongoDB 에서 확인한다.

수집 진입점(service.collect)부터 저장까지 거쳐, 여러 사이트에 올라온 같은 대회가 `contests`
컬렉션에 문서 하나로 남는지 본다. 임시 DB 는 conftest 의 mongo_db 가 만들고 지운다.
"""

from dataclasses import replace
from datetime import date

import pytest
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.contests.crawlers.base import RawContest
from app.contests.repository import COLLECTION, ContestRepository, SaveOutcome
from app.contests.service import collect
from app.core.db import Document

TODAY = date(2026, 10, 23)


def _contest(source: str, title: str = "2026 AI 해커톤", host: str = "주최사") -> RawContest:
    return RawContest(
        title=title,
        host=host,
        field="AI",
        apply_start=date(2026, 10, 1),
        apply_end=date(2026, 11, 1),
        event_start=None,
        event_end=None,
        eligibility_text="",
        apply_url=f"https://{source}.example.com/apply",
        source=source,
        source_url=f"https://{source}.example.com/contests/1",
    )


class FakeSource:
    def __init__(self, name: str, contests: list[RawContest]) -> None:
        self.name = name
        self._contests = contests

    def fetch(self) -> list[RawContest]:
        return self._contests


# 사이트마다 표기가 조금씩 다른 같은 대회 (제목 접두어·대소문자·공백·특수문자, 주최 공백)
SAME_CONTEST_SOURCES = [
    FakeSource("dev_event", [_contest("dev_event", title="[공모전] 2026 AI 해커톤")]),
    FakeSource("dacon", [_contest("dacon", title="2026 ai 해커톤", host="주최 사")]),
    FakeSource("linkareer", [_contest("linkareer", title="2026 AI-해커톤!")]),
]


@pytest.fixture
def repo(mongo_db: Database[Document]) -> ContestRepository:
    repository = ContestRepository(mongo_db)
    repository.ensure_indexes()
    return repository


@pytest.mark.feature("F2-2")
def test_same_contest_from_several_sites_is_stored_once(
    mongo_db: Database[Document], repo: ContestRepository
) -> None:
    results = collect(SAME_CONTEST_SOURCES, repo, TODAY)

    assert [r.error for r in results] == [None, None, None]
    docs = list(mongo_db[COLLECTION].find())
    assert len(docs) == 1
    assert docs[0]["source"] == "dev_event"  # 처음 수집한 소스가 대표 (Q-2)
    assert docs[0]["title"] == "[공모전] 2026 AI 해커톤"
    assert [s["source"] for s in docs[0]["sources"]] == ["dev_event", "dacon", "linkareer"]


@pytest.mark.feature("F2-2")
def test_recollecting_creates_no_duplicates(
    mongo_db: Database[Document], repo: ContestRepository
) -> None:
    collect(SAME_CONTEST_SOURCES, repo, TODAY)

    results = collect(SAME_CONTEST_SOURCES, repo, TODAY)

    assert [r.error for r in results] == [None, None, None]
    # 대표 소스는 값을 갱신하고, 나머지는 출처만 갱신한다. 새로 만든 문서는 없다
    assert [dict(r.outcomes) for r in results] == [
        {SaveOutcome.UPDATED: 1},
        {SaveOutcome.MERGED: 1},
        {SaveOutcome.MERGED: 1},
    ]
    docs = list(mongo_db[COLLECTION].find())
    assert len(docs) == 1
    assert [s["source"] for s in docs[0]["sources"]] == ["dev_event", "dacon", "linkareer"]


@pytest.mark.feature("F2-2")
def test_contests_differing_in_title_host_or_deadline_are_kept_apart(
    mongo_db: Database[Document], repo: ContestRepository
) -> None:
    base = _contest("dacon")
    contests = [
        base,
        replace(base, title="2026 AI 해커톤 본선", source_url="https://dacon.example.com/2"),
        replace(base, host="다른 주최사", source_url="https://dacon.example.com/3"),
        replace(base, apply_end=date(2026, 11, 2), source_url="https://dacon.example.com/4"),
    ]

    collect([FakeSource("dacon", contests)], repo, TODAY)

    assert mongo_db[COLLECTION].count_documents({}) == 4


@pytest.mark.feature("F2-2")
def test_merge_key_is_unique_in_database(
    mongo_db: Database[Document], repo: ContestRepository
) -> None:
    collect(SAME_CONTEST_SOURCES[:1], repo, TODAY)
    doc = mongo_db[COLLECTION].find_one()
    assert doc is not None

    with pytest.raises(DuplicateKeyError):
        mongo_db[COLLECTION].insert_one({"merge_key": doc["merge_key"]})
