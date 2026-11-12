"""F2-2 중복 제거를 실제 MongoDB 에서 확인한다.

수집 진입점(service.collect)부터 저장까지 거쳐, 여러 사이트에 올라온 같은 대회가 `contests`
컬렉션에 문서 하나로 남는지 본다. 임시 DB 는 conftest 의 mongo_db 가 만들고 지운다.
"""

from dataclasses import replace
from datetime import date

import pytest
from pymongo.database import Database

from app.contests.crawlers.base import RawContest
from app.contests.repository import COLLECTION, ContestRepository, SaveOutcome
from app.contests.rules import load_rules
from app.contests.service import collect
from app.core.db import Document

TODAY = date(2026, 10, 23)
RULES = load_rules()


def _contest(source: str, title: str = "2026 AI 해커톤", host: str = "주최사") -> RawContest:
    """소스만 다르고 나머지는 같은 테스트용 대회를 만든다."""
    return RawContest(
        title=title,
        host=host,
        field="AI",
        apply_start=date(2026, 10, 1),
        apply_end=date(2026, 11, 1),
        event_start=None,
        event_end=None,
        apply_url=f"https://{source}.example.com/apply",
        source=source,
        source_url=f"https://{source}.example.com/contests/1",
    )


class FakeSource:
    """고정된 대회 목록을 돌려주는 가짜 수집 소스."""

    def __init__(self, name: str, contests: list[RawContest]) -> None:
        """소스 이름과 돌려줄 대회 목록을 받는다."""
        self.name = name
        self._contests = contests

    def fetch(self) -> list[RawContest]:
        """받아 둔 대회 목록을 그대로 돌려준다."""
        return self._contests


# 사이트마다 표기가 조금씩 다른 같은 대회 (제목 접두어·대소문자·특수문자, 주최 표기·빈 주최)
SAME_CONTEST_SOURCES = [
    FakeSource("dev_event", [_contest("dev_event", title="[공모전] 2026 AI 해커톤")]),
    FakeSource("dacon", [_contest("dacon", title="2026 ai 해커톤", host="데이콘")]),
    FakeSource("code4korea", [_contest("code4korea", title="2026 AI-해커톤!", host="")]),
]


@pytest.fixture
def repo(mongo_db: Database[Document]) -> ContestRepository:
    """인덱스를 만든 대회 저장소."""
    repository = ContestRepository(mongo_db)
    repository.ensure_indexes()
    return repository


@pytest.mark.feature("F2-2")
def test_same_contest_from_several_sites_is_stored_once(
    mongo_db: Database[Document], repo: ContestRepository
) -> None:
    """여러 사이트의 같은 대회가 주최 표기가 달라도 한 문서로 저장되고 출처가 모두 남는다."""
    results = collect(SAME_CONTEST_SOURCES, repo, TODAY, RULES)

    assert [r.error for r in results] == [None, None, None]
    docs = list(mongo_db[COLLECTION].find())
    assert len(docs) == 1
    # 수집 순서가 아니라 소스 우선순위로 대표를 고른다 (Q-2)
    assert docs[0]["source"] == "dacon"
    assert (docs[0]["title"], docs[0]["host"]) == ("2026 ai 해커톤", "데이콘")
    assert [s["source"] for s in docs[0]["sources"]] == ["dev_event", "dacon", "code4korea"]


@pytest.mark.feature("F2-2")
def test_recollecting_creates_no_duplicates(
    mongo_db: Database[Document], repo: ContestRepository
) -> None:
    """다시 수집해도 문서가 늘지 않는다."""
    collect(SAME_CONTEST_SOURCES, repo, TODAY, RULES)

    results = collect(SAME_CONTEST_SOURCES, repo, TODAY, RULES)

    assert [r.error for r in results] == [None, None, None]
    assert [dict(r.outcomes) for r in results] == [{SaveOutcome.UPDATED: 1}] * 3
    docs = list(mongo_db[COLLECTION].find())
    assert len(docs) == 1
    assert [s["source"] for s in docs[0]["sources"]] == ["dev_event", "dacon", "code4korea"]


@pytest.mark.feature("F2-2")
def test_collect_order_does_not_change_shown_values(
    mongo_db: Database[Document], repo: ContestRepository
) -> None:
    """소스를 어떤 순서로 수집해도 보여 주는 값은 같다."""
    shown = ("title", "host", "source", "apply_end", "apply_url", "tags")
    collect(SAME_CONTEST_SOURCES, repo, TODAY, RULES)
    forward = mongo_db[COLLECTION].find_one({})
    mongo_db[COLLECTION].delete_many({})

    collect(SAME_CONTEST_SOURCES[::-1], repo, TODAY, RULES)
    backward = mongo_db[COLLECTION].find_one({})

    assert forward is not None and backward is not None
    assert {k: forward[k] for k in shown} == {k: backward[k] for k in shown}


@pytest.mark.feature("F2-2")
def test_different_contests_are_kept_apart(
    mongo_db: Database[Document], repo: ContestRepository
) -> None:
    """제목이 겹치지 않거나, 참가 URL 과 마감일이 모두 다르면 다른 대회로 저장한다."""
    base = _contest("dacon")
    contests = [
        FakeSource("dacon", [base]),
        FakeSource("dev_event", [_contest("dev_event", title="2026 보안 해커톤")]),
        FakeSource("code4korea", [replace(_contest("code4korea"), apply_end=date(2026, 11, 2))]),
    ]

    collect(contests, repo, TODAY, RULES)

    assert mongo_db[COLLECTION].count_documents({}) == 3


@pytest.mark.feature("F2-2")
def test_dropped_listing_leaves_bundle_after_successful_collect(
    mongo_db: Database[Document], repo: ContestRepository
) -> None:
    """소스가 대회를 내리면 다음 성공한 수집에서 그 출처가 빠지고 남은 출처 값으로 바뀐다."""
    collect(SAME_CONTEST_SOURCES, repo, TODAY, RULES)

    collect([FakeSource("dacon", [])], repo, TODAY, RULES)

    doc = mongo_db[COLLECTION].find_one({})
    assert doc is not None
    assert [s["source"] for s in doc["sources"]] == ["dev_event", "code4korea"]
    assert (doc["source"], doc["title"]) == ("dev_event", "[공모전] 2026 AI 해커톤")
