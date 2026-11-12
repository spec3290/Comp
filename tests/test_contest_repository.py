"""실제 MongoDB 통합 테스트. 임시 DB 는 conftest 의 mongo_db 가 만들고 지운다."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import pytest
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.contests.repository import COLLECTION, ContestOrigin, ContestRepository, SaveOutcome
from app.contests.rules import (
    NO_TAGS,
    ContestQuery,
    ContestStatus,
    ContestTags,
    Listing,
    SortOrder,
)
from app.core.db import Document
from app.core.time import KST, now_kst


def _contest(
    source: str,
    field: str = "AI",
    title: str = "2026 AI 해커톤",
    apply_start: date | None = date(2026, 10, 1),
    apply_end: date = date(2026, 11, 1),
    apply_url: str | None = "",
    tags: ContestTags = NO_TAGS,
) -> Listing:
    """필드 값을 바꿔 가며 쓰는 테스트용 출처를 만든다."""
    return Listing(
        title=title,
        host="주최사",
        field=field,
        apply_start=apply_start,
        apply_end=apply_end,
        event_start=None,
        event_end=None,
        # 빈 문자열이면 소스별 기본 링크, None 이면 링크를 못 찾은 대회 (Q-29)
        apply_url=f"https://{source}.example.com" if apply_url == "" else apply_url,
        source=source,
        source_url=f"https://{source}.example.com/{title}",
        tags=tags,
    )


def _save(repo: ContestRepository, contest: Listing) -> SaveOutcome:
    """출처 하나를 저장한다."""
    return repo.save(contest, now_kst())


@pytest.mark.feature("F2-2")
def test_save_is_idempotent_and_merges_sources(mongo_db: Database[Document]) -> None:
    """같은 출처를 다시 저장하면 갱신되고 다른 소스는 출처로 병합된다."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()

    assert _save(repo, _contest("a")) is SaveOutcome.INSERTED
    assert _save(repo, _contest("a", field="AI, 데이터")) is SaveOutcome.UPDATED
    assert _save(repo, _contest("b", field="뒤 순위 값")) is SaveOutcome.MERGED
    assert _save(repo, _contest("b")) is SaveOutcome.UPDATED

    docs = list(mongo_db[COLLECTION].find())
    assert len(docs) == 1
    doc = docs[0]
    assert doc["source"] == "a"  # 우선순위 목록 밖 소스끼리는 이름순 (Q-2)
    assert doc["field"] == "AI, 데이터"
    assert [s["source"] for s in doc["sources"]] == ["a", "b"]
    assert [s["field"] for s in doc["sources"]] == ["AI, 데이터", "AI"]  # 출처별 원래 값
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
    assert [o.source for o in record.sources] == ["a"]
    assert seeded.get("not-an-object-id") is None
    assert seeded.get("0" * 24) is None


@pytest.mark.feature("F2-4")
def test_get_returns_source_links_with_representative_and_updated_at(
    mongo_db: Database[Document],
) -> None:
    """상세용 대회에는 사이트별 원문 링크·대표 여부와 정보 갱신 시각이 담긴다 (Q-29)."""
    repo = ContestRepository(mongo_db)
    _save(repo, _contest("a"))
    _save(repo, _contest("b"))
    doc = mongo_db[COLLECTION].find_one({})
    assert doc is not None

    record = repo.get(str(doc["_id"]))

    assert record is not None
    assert record.sources == (
        ContestOrigin("a", "https://a.example.com/2026 AI 해커톤", representative=True),
        ContestOrigin("b", "https://b.example.com/2026 AI 해커톤", representative=False),
    )
    # 대표(a)가 값을 쓴 시각이다. 보여 주는 값을 바꾸지 않는 다른 출처의 병합은 이 시각을 두고 간다.
    assert record.updated_at == doc["updated_at"]
    first = doc["updated_at"]
    _save(repo, _contest("b"))
    again = mongo_db[COLLECTION].find_one({})
    assert again is not None and again["updated_at"] == first


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
    """같은 원문의 마감일이 바뀌면 새 문서를 만들지 않고 기존 문서를 고친다."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    _save(repo, _contest("dacon", apply_end=date(2026, 11, 2)))

    assert _save(repo, _contest("dacon", apply_end=date(2026, 11, 1))) is SaveOutcome.UPDATED

    docs = list(mongo_db[COLLECTION].find())
    assert len(docs) == 1
    assert docs[0]["apply_end"].date() == date(2026, 11, 1)


@pytest.mark.feature("F2-2")
def test_concurrent_saves_of_same_listing_keep_one_document(mongo_db: Database[Document]) -> None:
    """같은 원문을 동시에 저장해도 문서 하나만 남는다 (출처 UNIQUE 인덱스)."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    contests = [_contest("a") for _ in range(16)]
    barrier = threading.Barrier(len(contests))

    def save(contest: Listing) -> SaveOutcome:
        """배리어에서 함께 출발해 저장한다."""
        barrier.wait()  # 최대한 동시에 출발시킨다
        return _save(repo, contest)

    with ThreadPoolExecutor(max_workers=len(contests)) as pool:
        outcomes = list(pool.map(save, contests))

    assert mongo_db[COLLECTION].count_documents({}) == 1
    assert outcomes.count(SaveOutcome.INSERTED) == 1


@pytest.mark.feature("F2-2")
def test_concurrent_merges_into_existing_contest_keep_every_source_once(
    mongo_db: Database[Document],
) -> None:
    """이미 있는 대회에 여러 사이트가 동시에 붙어도 출처가 빠지거나 겹치지 않는다 (rev 재시도).

    다른 사이트의 같은 대회가 동시에 새로 만들어지는 일은 수집이 한 번에 하나만 돌아 생기지 않는다.
    (생기면 다음 저장 때 합친다.)
    """
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    _save(repo, _contest("a"))
    contests = [_contest(source) for source in ("b", "c", "d") for _ in range(4)]
    barrier = threading.Barrier(len(contests))

    def save(contest: Listing) -> SaveOutcome:
        """배리어에서 함께 출발해 저장한다."""
        barrier.wait()
        return _save(repo, contest)

    with ThreadPoolExecutor(max_workers=len(contests)) as pool:
        outcomes = list(pool.map(save, contests))

    docs = list(mongo_db[COLLECTION].find())
    assert len(docs) == 1
    assert sorted(s["source"] for s in docs[0]["sources"]) == ["a", "b", "c", "d"]
    assert SaveOutcome.INSERTED not in outcomes


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

    def save(contest: Listing) -> SaveOutcome:
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
def test_tags_are_union_of_all_sources(mongo_db: Database[Document]) -> None:
    """분류 태그는 모든 출처 태그의 합집합이다 (Q-2)."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    _save(repo, _contest("a", tags=ContestTags(field=frozenset({"AI"}))))
    _save(repo, _contest("b", tags=ContestTags(field=frozenset({"개발"}))))

    (record,), _ = repo.find(ContestQuery(), TODAY)

    assert record.tags == ContestTags(field=frozenset({"AI", "개발"}))


@pytest.mark.feature("F3-3")
def test_find_by_tags_keeps_unclassified(mongo_db: Database[Document]) -> None:
    """분야·형식 필터는 값 안은 OR, 차원 사이는 AND 이고 미분류 대회를 빼지 않는다."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    _save(
        repo,
        _contest(
            "a",
            title="AI 해커톤",
            tags=ContestTags(field=frozenset({"AI"}), format=frozenset({"해커톤"})),
        ),
    )
    _save(
        repo,
        _contest(
            "a",
            title="보안 공모전",
            tags=ContestTags(field=frozenset({"보안"}), format=frozenset({"공모전"})),
        ),
    )
    _save(repo, _contest("a", title="형식 미분류", tags=ContestTags(field=frozenset({"AI"}))))
    _save(repo, _contest("a", title="미분류"))
    mongo_db[COLLECTION].update_one({"title": "미분류"}, {"$unset": {"tags": ""}})  # 분류 전 문서

    ai = ContestQuery(tags=ContestTags(field=frozenset({"AI"})))
    assert sorted(_titles(repo, ai)) == ["AI 해커톤", "미분류", "형식 미분류"]

    ai_or_security_hackathon = ContestQuery(
        tags=ContestTags(field=frozenset({"AI", "보안"}), format=frozenset({"해커톤"}))
    )
    assert sorted(_titles(repo, ai_or_security_hackathon)) == ["AI 해커톤", "미분류", "형식 미분류"]


@pytest.mark.feature("F2-5")
def test_contest_without_apply_url_is_saved_with_null_link(mongo_db: Database[Document]) -> None:
    """참가 링크를 못 찾은 대회도 저장하고 링크는 None 으로 읽힌다 (Q-29)."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    _save(repo, _contest("a", apply_url=None))

    (record,), _ = repo.find(ContestQuery(), TODAY)

    assert record.apply_url is None


@pytest.mark.feature("F2-5")
def test_recollect_without_link_keeps_saved_apply_url(mongo_db: Database[Document]) -> None:
    """다시 수집할 때 링크를 못 찾아도 이미 저장된 링크는 지우지 않는다 (F2-1 기존 데이터 유지)."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    _save(repo, _contest("a"))

    assert _save(repo, _contest("a", field="AI, 데이터", apply_url=None)) is SaveOutcome.UPDATED

    (record,), _ = repo.find(ContestQuery(), TODAY)
    assert (record.field, record.apply_url) == ("AI, 데이터", "https://a.example.com")


@pytest.mark.feature("F2-5")
def test_merged_source_fills_missing_apply_url(mongo_db: Database[Document]) -> None:
    """대표 소스에 링크가 없으면 같은 대회를 수집한 다른 소스의 링크로 채운다 (Q-29)."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    _save(repo, _contest("a", apply_url=None))
    _save(repo, _contest("b"))
    _save(repo, _contest("c"))

    (record,), _ = repo.find(ContestQuery(), TODAY)

    # 링크가 있는 출처 중 앞 순위(b)를 따르고, 대표가 다시 링크 없이 수집해도 남는다
    _save(repo, _contest("a", apply_url=None))
    (again,), _ = repo.find(ContestQuery(), TODAY)
    assert record.apply_url == again.apply_url == "https://b.example.com"


def _legacy_doc(source: str, others: list[str], title: str = "2026 AI 해커톤") -> Document:
    """이전 구조(merge_key·대표 소스 값만 저장) 문서."""
    when = now_kst()
    return {
        "merge_key": f"{source}|{title}",
        "title": title,
        "host": "",
        "field": "AI",
        "apply_start": None,
        "apply_end": datetime(2026, 11, 1, tzinfo=KST),
        "event_start": None,
        "event_end": None,
        "apply_url": f"https://{source}.example.com",
        "source_url": f"https://{source}.example.com/{title}",
        "source": source,
        "sources": [
            {"source": s, "source_url": f"https://{s}.example.com/{title}", "fetched_at": when}
            for s in [source, *others]
        ],
        "tags": {"field": ["AI"], "format": []},
        "created_at": when,
        "updated_at": when,
    }


@pytest.mark.feature("F2-2")
def test_ensure_indexes_migrates_legacy_documents(mongo_db: Database[Document]) -> None:
    """이전 구조 문서는 출처마다 값을 채우고 merge_key 와 그 인덱스를 없앤다."""
    mongo_db[COLLECTION].create_index("merge_key", unique=True)
    mongo_db[COLLECTION].insert_one(_legacy_doc("a", ["b"]))

    ContestRepository(mongo_db).ensure_indexes()

    doc = mongo_db[COLLECTION].find_one({})
    assert doc is not None
    assert "merge_key" not in doc
    assert "merge_key_1" not in mongo_db[COLLECTION].index_information()
    assert [(s["source"], s["title"], s["field"]) for s in doc["sources"]] == [
        ("a", "2026 AI 해커톤", "AI"),
        ("b", "2026 AI 해커톤", "AI"),
    ]


@pytest.mark.feature("F2-2")
def test_save_merges_duplicate_documents_left_by_old_merge_key(
    mongo_db: Database[Document],
) -> None:
    """주최가 달라 따로 저장돼 있던 같은 대회는 다음 저장 때 한 문서로 합친다."""
    mongo_db[COLLECTION].insert_many([_legacy_doc("dev_event", []), _legacy_doc("code4korea", [])])
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    kept = mongo_db[COLLECTION].find_one({"source": "dev_event"})
    assert kept is not None

    assert _save(repo, _contest("dev_event")) is SaveOutcome.UPDATED

    docs = list(mongo_db[COLLECTION].find())
    assert len(docs) == 1
    assert docs[0]["_id"] == kept["_id"]  # 다시 수집한 출처의 문서를 남긴다
    assert sorted(s["source"] for s in docs[0]["sources"]) == ["code4korea", "dev_event"]


@pytest.mark.feature("F2-2")
def test_listing_is_unique_across_documents(mongo_db: Database[Document]) -> None:
    """DB 가 한 사이트의 한 원문이 두 문서에 들어가는 것을 막는다."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    _save(repo, _contest("a"))
    doc = mongo_db[COLLECTION].find_one({})
    assert doc is not None

    with pytest.raises(DuplicateKeyError):
        mongo_db[COLLECTION].insert_one({"sources": [doc["sources"][0]]})


@pytest.mark.feature("F2-2")
def test_prune_missing_drops_source_and_recomputes_values(mongo_db: Database[Document]) -> None:
    """소스가 내린 출처는 묶음에서 빠지고 남은 출처로 값을 다시 고른다. 하나뿐인 출처는 둔다."""
    repo = ContestRepository(mongo_db)
    repo.ensure_indexes()
    _save(repo, _contest("dacon", title="2026 AI 해커톤"))
    _save(repo, _contest("dev_event", title="2026 AI 해커톤 대회"))
    _save(repo, _contest("dacon", title="혼자 남은 대회"))

    assert repo.prune_missing("dacon", set(), now_kst()) == 1

    merged = mongo_db[COLLECTION].find_one({"sources.source": "dev_event"})
    assert merged is not None
    assert [s["source"] for s in merged["sources"]] == ["dev_event"]
    assert (merged["source"], merged["title"]) == ("dev_event", "2026 AI 해커톤 대회")
    assert mongo_db[COLLECTION].count_documents({"title": "혼자 남은 대회"}) == 1
