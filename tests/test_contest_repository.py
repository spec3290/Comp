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
from app.contests.rules import merge_key
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


def _contest(source: str, field: str = "AI") -> RawContest:
    return RawContest(
        title="2026 AI 해커톤",
        host="주최사",
        field=field,
        apply_start=date(2026, 10, 1),
        apply_end=date(2026, 11, 1),
        event_start=None,
        event_end=None,
        eligibility_text="",
        apply_url=f"https://{source}.example.com",
        source=source,
        source_url=f"https://{source}.example.com",
    )


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
