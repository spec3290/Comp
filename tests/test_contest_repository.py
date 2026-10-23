"""실제 MongoDB 통합 테스트. MONGODB_TEST_URI 가 있을 때만 돈다 (임시 DB 를 만들고 지운다)."""

import os
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from pymongo import MongoClient
from pymongo.database import Database

from app.contests.crawlers.base import RawContest
from app.contests.repository import COLLECTION, ContestRepository, SaveOutcome
from app.contests.rules import ContestQuery, ContestStatus, SortOrder, merge_key
from app.core.db import Document
from app.core.time import KST, now_kst

TEST_URI = os.environ.get("MONGODB_TEST_URI")
pytestmark = pytest.mark.skipif(not TEST_URI, reason="MONGODB_TEST_URI 미설정")


@pytest.fixture
def db() -> Iterator[Database[Document]]:
    client: MongoClient[Document] = MongoClient(TEST_URI, tz_aware=True, tzinfo=KST)
    name = f"test_{uuid.uuid4().hex[:12]}"
    database = client[name]
    try:
        yield database
    finally:
        # Atlas 사용자에게 dropDatabase 권한이 없을 수 있어 컬렉션을 지운다 (빈 DB 는 사라진다)
        for collection in database.list_collection_names():
            database.drop_collection(collection)
        client.close()


def _contest(
    source: str,
    field: str = "AI",
    title: str = "2026 AI 해커톤",
    apply_start: date | None = date(2026, 10, 1),
    apply_end: date = date(2026, 11, 1),
) -> RawContest:
    return RawContest(
        title=title,
        host="주최사",
        field=field,
        apply_start=apply_start,
        apply_end=apply_end,
        event_start=None,
        event_end=None,
        eligibility_text="",
        apply_url=f"https://{source}.example.com",
        source=source,
        source_url=f"https://{source}.example.com/{title}",
    )


def _save(repo: ContestRepository, contest: RawContest) -> SaveOutcome:
    return repo.save(contest, merge_key(contest.title, contest.host, contest.apply_end), now_kst())


@pytest.mark.feature("F2-2")
def test_save_is_idempotent_and_merges_sources(db: Database[Document]) -> None:
    repo = ContestRepository(db)
    repo.ensure_indexes()
    key = merge_key("2026 AI 해커톤", "주최사", date(2026, 11, 1))

    assert repo.save(_contest("a"), key, now_kst()) is SaveOutcome.INSERTED
    assert repo.save(_contest("a", field="AI, 데이터"), key, now_kst()) is SaveOutcome.UPDATED
    assert repo.save(_contest("b", field="무시됨"), key, now_kst()) is SaveOutcome.MERGED
    assert repo.save(_contest("b"), key, now_kst()) is SaveOutcome.MERGED

    docs = list(db[COLLECTION].find())
    assert len(docs) == 1
    doc = docs[0]
    assert doc["source"] == "a"
    assert doc["field"] == "AI, 데이터"
    assert [s["source"] for s in doc["sources"]] == ["a", "b"]
    assert doc["apply_end"].date() == date(2026, 11, 1)
    assert doc["apply_end"].utcoffset() == now_kst().utcoffset()


TODAY = date(2026, 10, 23)


@pytest.fixture
def seeded(db: Database[Document]) -> ContestRepository:
    repo = ContestRepository(db)
    repo.ensure_indexes()
    _save(repo, _contest("a", title="접수중 AI", apply_end=date(2026, 10, 30)))
    _save(repo, _contest("a", title="시작일 없음", apply_start=None, apply_end=TODAY))
    _save(repo, _contest("a", title="예정 보안", field="보안", apply_start=date(2026, 11, 1)))
    _save(repo, _contest("a", title="마감 디자인", field="디자인", apply_end=date(2026, 10, 22)))
    _save(repo, _contest("a", title="오래된 마감", apply_end=date(2026, 10, 19)))
    return repo


def _titles(repo: ContestRepository, query: ContestQuery) -> list[str]:
    records, _ = repo.find(query, TODAY)
    return [r.title for r in records]


@pytest.mark.feature("F2-3")
def test_find_filters_by_computed_status_and_sorts_by_deadline(seeded: ContestRepository) -> None:
    assert _titles(seeded, ContestQuery()) == ["시작일 없음", "접수중 AI", "예정 보안"]
    closed = ContestQuery(statuses=frozenset({ContestStatus.CLOSED}))
    assert _titles(seeded, closed) == ["오래된 마감", "마감 디자인"]


@pytest.mark.feature("F2-3")
def test_find_field_or_and_keyword(seeded: ContestRepository) -> None:
    query = ContestQuery(statuses=frozenset(ContestStatus), fields=("보안", "디자인"))
    assert sorted(_titles(seeded, query)) == ["마감 디자인", "예정 보안"]
    assert _titles(seeded, ContestQuery(keyword="ai")) == ["접수중 AI"]


@pytest.mark.feature("F2-3")
def test_find_paginates_with_total(seeded: ContestRepository) -> None:
    query = ContestQuery(statuses=frozenset(ContestStatus), page=2, size=2)
    records, total = seeded.find(query, TODAY)
    assert total == 5
    assert [r.title for r in records] == ["시작일 없음", "접수중 AI"]
    latest = ContestQuery(statuses=frozenset(ContestStatus), sort=SortOrder.LATEST, size=1)
    assert _titles(seeded, latest) == ["오래된 마감"]


@pytest.mark.feature("F2-4")
def test_get_by_id_and_unknown_ids(seeded: ContestRepository) -> None:
    first, _ = seeded.find(ContestQuery(size=1), TODAY)
    record = seeded.get(first[0].id)
    assert record is not None
    assert record.sources == ("a",)
    assert seeded.get("not-an-object-id") is None
    assert seeded.get("0" * 24) is None


@pytest.mark.feature("F2-3")
def test_delete_closed_before_keeps_three_days(seeded: ContestRepository) -> None:
    assert seeded.delete_closed_before(date(2026, 10, 20)) == 1  # 10-19 마감만 삭제
    closed = ContestQuery(statuses=frozenset({ContestStatus.CLOSED}))
    assert _titles(seeded, closed) == ["마감 디자인"]


@pytest.mark.feature("F2-2")
def test_changed_deadline_moves_existing_document(db: Database[Document]) -> None:
    repo = ContestRepository(db)
    repo.ensure_indexes()
    _save(repo, _contest("dacon", apply_end=date(2026, 11, 2)))

    assert _save(repo, _contest("dacon", apply_end=date(2026, 11, 1))) is SaveOutcome.UPDATED

    docs = list(db[COLLECTION].find())
    assert len(docs) == 1
    assert docs[0]["apply_end"].date() == date(2026, 11, 1)


@pytest.mark.feature("F2-2")
def test_concurrent_saves_keep_one_document_and_unique_sources(db: Database[Document]) -> None:
    repo = ContestRepository(db)
    repo.ensure_indexes()
    key = merge_key("2026 AI 해커톤", "주최사", date(2026, 11, 1))
    contests = [_contest(source) for source in ("a", "b", "c") for _ in range(8)]
    barrier = threading.Barrier(len(contests))

    def save(contest: RawContest) -> SaveOutcome:
        barrier.wait()  # 최대한 동시에 출발시킨다
        return repo.save(contest, key, now_kst())

    with ThreadPoolExecutor(max_workers=len(contests)) as pool:
        outcomes = list(pool.map(save, contests))

    docs = list(db[COLLECTION].find())
    assert len(docs) == 1
    sources = [s["source"] for s in docs[0]["sources"]]
    assert sorted(sources) == ["a", "b", "c"]
    assert outcomes.count(SaveOutcome.INSERTED) == 1
