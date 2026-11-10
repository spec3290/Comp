"""실제 MongoDB 통합 테스트. 임시 DB 는 conftest 의 mongo_db 가 만들고 지운다."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from pymongo.database import Database

from app.contests.crawlers.base import RawContest
from app.contests.repository import COLLECTION, ContestRepository, SaveOutcome
from app.contests.rules import (
    NO_TAGS,
    ContestQuery,
    ContestStatus,
    ContestTags,
    SortOrder,
    merge_key,
)
from app.core.db import Document
from app.core.time import now_kst


def _contest(
    source: str,
    field: str = "AI",
    title: str = "2026 AI 해커톤",
    apply_start: date | None = date(2026, 10, 1),
    apply_end: date = date(2026, 11, 1),
) -> RawContest:
    """필드 값을 바꿔 가며 쓰는 테스트용 대회를 만든다."""
    return RawContest(
        title=title,
        host="주최사",
        field=field,
        apply_start=apply_start,
        apply_end=apply_end,
        event_start=None,
        event_end=None,
        apply_url=f"https://{source}.example.com",
        source=source,
        source_url=f"https://{source}.example.com/{title}",
    )


def _save(repo: ContestRepository, contest: RawContest, tags: ContestTags = NO_TAGS) -> SaveOutcome:
    """병합 키를 계산해 대회를 저장한다."""
    key = merge_key(contest.title, contest.host, contest.apply_end)
    return repo.save(contest, key, now_kst(), tags=tags)


@pytest.mark.feature("F2-2")
def test_save_is_idempotent_and_merges_sources(mongo_db: Database[Document]) -> None:
    """같은 대회를 다시 저장하면 갱신되고 다른 소스는 출처로 병합된다."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    key = merge_key("2026 AI 해커톤", "주최사", date(2026, 11, 1))

    assert repo.save(_contest("a"), key, now_kst(), tags=NO_TAGS) is SaveOutcome.INSERTED
    assert (
        repo.save(_contest("a", field="AI, 데이터"), key, now_kst(), tags=NO_TAGS)
        is SaveOutcome.UPDATED
    )
    assert (
        repo.save(_contest("b", field="무시됨"), key, now_kst(), tags=NO_TAGS) is SaveOutcome.MERGED
    )
    assert repo.save(_contest("b"), key, now_kst(), tags=NO_TAGS) is SaveOutcome.MERGED

    docs = list(mongo_db[COLLECTION].find())
    assert len(docs) == 1
    doc = docs[0]
    assert doc["source"] == "a"
    assert doc["field"] == "AI, 데이터"
    assert [s["source"] for s in doc["sources"]] == ["a", "b"]
    assert doc["apply_end"].date() == date(2026, 11, 1)
    assert doc["apply_end"].utcoffset() == now_kst().utcoffset()


TODAY = date(2026, 10, 23)


@pytest.fixture
def seeded(mongo_db: Database[Document]) -> ContestRepository:
    """상태가 다양한 대회 다섯 건을 넣어 둔 저장소."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    _save(repo, _contest("a", title="접수중 AI", apply_end=date(2026, 10, 30)))
    _save(repo, _contest("a", title="시작일 없음", apply_start=None, apply_end=TODAY))
    _save(repo, _contest("a", title="예정 보안", field="보안", apply_start=date(2026, 11, 1)))
    _save(repo, _contest("a", title="마감 디자인", field="디자인", apply_end=date(2026, 10, 22)))
    _save(repo, _contest("a", title="오래된 마감", apply_end=date(2026, 10, 19)))
    return repo


def _titles(repo: ContestRepository, query: ContestQuery) -> list[str]:
    """조회 결과의 제목 목록."""
    records, _ = repo.find(query, TODAY)
    return [r.title for r in records]


@pytest.mark.feature("F2-3")
def test_find_filters_by_computed_status_and_sorts_by_deadline(seeded: ContestRepository) -> None:
    """날짜로 계산한 상태로 거르고 마감 임박순으로 정렬한다."""
    assert _titles(seeded, ContestQuery()) == ["시작일 없음", "접수중 AI", "예정 보안"]
    closed = ContestQuery(statuses=frozenset({ContestStatus.CLOSED}))
    assert _titles(seeded, closed) == ["오래된 마감", "마감 디자인"]


@pytest.mark.feature("F2-3")
def test_find_keyword_ignores_case(seeded: ContestRepository) -> None:
    """제목 검색은 대소문자 무시 부분 일치다."""
    assert _titles(seeded, ContestQuery(keyword="ai")) == ["접수중 AI"]


@pytest.mark.feature("F2-3")
def test_find_paginates_with_total(seeded: ContestRepository) -> None:
    """페이지를 나누고 전체 건수를 함께 돌려준다."""
    query = ContestQuery(statuses=frozenset(ContestStatus), page=2, size=2)
    records, total = seeded.find(query, TODAY)
    assert total == 5
    assert [r.title for r in records] == ["시작일 없음", "접수중 AI"]
    latest = ContestQuery(statuses=frozenset(ContestStatus), sort=SortOrder.LATEST, size=1)
    assert _titles(seeded, latest) == ["오래된 마감"]


@pytest.mark.feature("F2-4")
def test_get_by_id_and_unknown_ids(seeded: ContestRepository) -> None:
    """ID 로 찾고, 없거나 잘못된 ID 는 None 이다."""
    first, _ = seeded.find(ContestQuery(size=1), TODAY)
    record = seeded.get(first[0].id)
    assert record is not None
    assert record.sources == ("a",)
    assert seeded.get("not-an-object-id") is None
    assert seeded.get("0" * 24) is None


@pytest.mark.feature("F2-7")
def test_get_many_skips_unknown_and_invalid_ids(seeded: ContestRepository) -> None:
    """여러 ID 조회에서 없거나 잘못된 ID 는 건너뛴다."""
    records, _ = seeded.find(ContestQuery(size=2), TODAY)
    ids = [r.id for r in records]

    found = seeded.get_many([*ids, "0" * 24, "not-an-object-id"])

    assert sorted(found) == sorted(ids)
    assert seeded.get_many([]) == {}


@pytest.mark.feature("F2-3")
def test_delete_closed_before_keeps_three_days(seeded: ContestRepository) -> None:
    """마감 후 3일이 지난 대회만 지운다."""
    assert seeded.delete_closed_before(date(2026, 10, 20)) == 1  # 10-19 마감만 삭제
    closed = ContestQuery(statuses=frozenset({ContestStatus.CLOSED}))
    assert _titles(seeded, closed) == ["마감 디자인"]


@pytest.mark.feature("F2-2")
def test_changed_deadline_moves_existing_document(mongo_db: Database[Document]) -> None:
    """대표 소스의 마감일이 바뀌면 새 문서를 만들지 않고 기존 문서를 옮긴다."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    _save(repo, _contest("dacon", apply_end=date(2026, 11, 2)))

    assert _save(repo, _contest("dacon", apply_end=date(2026, 11, 1))) is SaveOutcome.UPDATED

    docs = list(mongo_db[COLLECTION].find())
    assert len(docs) == 1
    assert docs[0]["apply_end"].date() == date(2026, 11, 1)


@pytest.mark.feature("F2-2")
def test_concurrent_saves_keep_one_document_and_unique_sources(
    mongo_db: Database[Document],
) -> None:
    """동시에 저장해도 문서 하나와 중복 없는 출처만 남는다."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    key = merge_key("2026 AI 해커톤", "주최사", date(2026, 11, 1))
    contests = [_contest(source) for source in ("a", "b", "c") for _ in range(8)]
    barrier = threading.Barrier(len(contests))

    def save(contest: RawContest) -> SaveOutcome:
        """배리어에서 함께 출발해 저장한다."""
        barrier.wait()  # 최대한 동시에 출발시킨다
        return repo.save(contest, key, now_kst(), tags=NO_TAGS)

    with ThreadPoolExecutor(max_workers=len(contests)) as pool:
        outcomes = list(pool.map(save, contests))

    docs = list(mongo_db[COLLECTION].find())
    assert len(docs) == 1
    sources = [s["source"] for s in docs[0]["sources"]]
    assert sorted(sources) == ["a", "b", "c"]
    assert outcomes.count(SaveOutcome.INSERTED) == 1


@pytest.mark.feature("F2-2")
def test_concurrent_saves_with_changed_deadline_keep_one_document(
    mongo_db: Database[Document],
) -> None:
    """마감일이 바뀐 동시 저장에서도 문서가 하나만 남는다."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    _save(repo, _contest("dacon", apply_end=date(2026, 11, 2)))
    changed = [_contest("dacon", apply_end=date(2026, 11, 1)) for _ in range(16)]
    barrier = threading.Barrier(len(changed))

    def save(contest: RawContest) -> SaveOutcome:
        """배리어에서 함께 출발해 저장한다."""
        barrier.wait()
        return _save(repo, contest)

    with ThreadPoolExecutor(max_workers=len(changed)) as pool:
        outcomes = list(pool.map(save, changed))

    docs = list(mongo_db[COLLECTION].find())
    assert len(docs) == 1
    assert docs[0]["apply_end"].date() == date(2026, 11, 1)
    assert SaveOutcome.INSERTED not in outcomes


@pytest.mark.feature("F3-1")
def test_tags_follow_representative_source(mongo_db: Database[Document]) -> None:
    """분류 태그는 대표 소스의 값을 따른다."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    _save(repo, _contest("a"), ContestTags(field=frozenset({"AI"})))
    _save(repo, _contest("b"), ContestTags(field=frozenset({"개발"})))

    (record,), _ = repo.find(ContestQuery(), TODAY)

    assert record.tags == ContestTags(field=frozenset({"AI"}))


@pytest.mark.feature("F3-3")
def test_find_by_tags_keeps_unclassified(mongo_db: Database[Document]) -> None:
    """분야·형식 필터는 값 안은 OR, 차원 사이는 AND 이고 미분류 대회를 빼지 않는다."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    _save(
        repo,
        _contest("a", title="AI 해커톤"),
        ContestTags(field=frozenset({"AI"}), format=frozenset({"해커톤"})),
    )
    _save(
        repo,
        _contest("a", title="보안 공모전"),
        ContestTags(field=frozenset({"보안"}), format=frozenset({"공모전"})),
    )
    _save(repo, _contest("a", title="형식 미분류"), ContestTags(field=frozenset({"AI"})))
    _save(repo, _contest("a", title="미분류"))
    mongo_db[COLLECTION].update_one({"title": "미분류"}, {"$unset": {"tags": ""}})  # 분류 전 문서

    ai = ContestQuery(tags=ContestTags(field=frozenset({"AI"})))
    assert sorted(_titles(repo, ai)) == ["AI 해커톤", "미분류", "형식 미분류"]

    ai_or_security_hackathon = ContestQuery(
        tags=ContestTags(field=frozenset({"AI", "보안"}), format=frozenset({"해커톤"}))
    )
    assert sorted(_titles(repo, ai_or_security_hackathon)) == ["AI 해커톤", "미분류", "형식 미분류"]
