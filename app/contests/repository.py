"""대회 저장소 (MongoDB `contests`) + 북마크·클릭 저장소 (PostgreSQL, ADR-0008).

문서 하나가 병합된 대회 하나다. 날짜는 KST 자정 tz-aware datetime 으로 저장한다
(BSON 에 날짜 전용 타입이 없다). 사이트별로 수집한 원래 값은 `sources` 배열에 출처마다 두고,
최상위 값은 저장할 때마다 `rules.resolve` 로 다시 고른다 (Q-2).
같은 대회 판정은 `rules.same_contest` 다.

한 사이트의 원문(source + source_url)은 문서 하나에만 들어가도록 UNIQUE 인덱스가 막는다.
문서 갱신은 `rev` 를 조건으로 걸어, 그 사이 다른 저장이 문서를 바꿨으면 처음부터 다시 한다.
수집은 한 번에 하나만 돈다(collect.yml concurrency, 소스는 차례로). 그래도 다른 사이트의 같은
대회가 동시에 새 문서로 만들어지면, 다음에 그 대회를 저장할 때 한 문서로 합친다.
"""

from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contests.models import Bookmark, ContestClick
from app.contests.rules import (
    NO_TAGS,
    ContestQuery,
    ContestStatus,
    ContestTags,
    Dimension,
    Listing,
    SortOrder,
    belongs_to,
    refresh,
    resolve,
    url_key,
)
from app.core.db import Document
from app.core.time import KST

COLLECTION = "contests"
# 이전 구조(merge_key·대표 소스 고정)의 인덱스. 새 구조로 옮길 때 지운다.
LEGACY_INDEXES = ("merge_key_1", "source_1_source_url_1")
# 경합하면 매번 한 저장은 성공하므로, 같은 문서를 동시에 고치는 저장 수보다 넉넉하면 된다
SAVE_ATTEMPTS = 50


class SaveOutcome(StrEnum):
    """대회 저장 결과 종류."""

    INSERTED = "inserted"  # 새 대회
    UPDATED = "updated"  # 이미 있던 출처를 다시 수집
    MERGED = "merged"  # 다른 사이트가 먼저 올린 같은 대회에 출처로 붙음


@dataclass(frozen=True)
class ContestOrigin:
    """대회를 수집한 사이트와 그 사이트의 원문 링크. 대표는 우선순위가 가장 높은 출처 (Q-2)."""

    source: str
    source_url: str
    representative: bool


@dataclass(frozen=True)
class ContestRecord:
    """MongoDB 에서 읽은 대회 한 건."""

    id: str
    title: str
    host: str
    field: str
    apply_start: date | None
    apply_end: date
    event_start: date | None
    event_end: date | None
    apply_url: str | None  # 못 가져온 대회는 None (Q-29)
    source_url: str
    sources: tuple[ContestOrigin, ...]
    updated_at: datetime  # 보여 주는 값이 바뀌었거나 대표 출처가 다시 수집된 시각 (Q-29)
    tags: ContestTags = NO_TAGS


_SORT_KEYS: dict[SortOrder, list[tuple[str, int]]] = {
    SortOrder.DEADLINE: [("apply_end", ASCENDING), ("_id", ASCENDING)],
    SortOrder.LATEST: [("created_at", DESCENDING), ("_id", DESCENDING)],
}


def _to_datetime(value: date | None) -> datetime | None:
    """날짜를 KST 자정 datetime 으로 바꾼다 (MongoDB 는 date 를 저장하지 못한다)."""
    return None if value is None else datetime.combine(value, time.min, tzinfo=KST)


def _to_date(value: datetime | None) -> date | None:
    """저장된 datetime 을 KST 날짜로 되돌린다."""
    return None if value is None else value.astimezone(KST).date()


def _contains(text: str) -> dict[str, str]:
    """대소문자 무시 부분 일치. 사용자 입력은 정규식으로 해석하지 않는다."""
    return {"$regex": re.escape(text), "$options": "i"}


def _status_filter(status: ContestStatus, today: datetime) -> dict[str, Any]:
    """rules.ApplyPeriod.status 와 같은 판정을 쿼리로 옮긴 것. 상태는 저장하지 않는다."""
    if status is ContestStatus.UPCOMING:
        return {"apply_start": {"$gt": today}}
    if status is ContestStatus.CLOSED:
        return {"apply_end": {"$lt": today}}
    started = {"$or": [{"apply_start": None}, {"apply_start": {"$lte": today}}]}
    return {"$and": [started, {"apply_end": {"$gte": today}}]}


def _tag_filter(dimension: Dimension, requested: frozenset[str]) -> dict[str, Any]:
    """요청 값 태그가 있거나, 그 차원이 미분류(태그 없음)면 통과한다 (F3-3)."""
    key = f"tags.{dimension.value}"
    return {
        "$or": [
            {key: {"$in": sorted(requested)}},
            {f"{key}.0": {"$exists": False}},
        ]
    }


def build_filter(query: ContestQuery, today: date) -> dict[str, Any]:
    """목록 조회 조건을 MongoDB 필터로 바꾼다."""
    midnight = datetime.combine(today, time.min, tzinfo=KST)
    conditions: list[dict[str, Any]] = []
    if query.statuses:
        statuses = sorted(query.statuses)
        conditions.append({"$or": [_status_filter(s, midnight) for s in statuses]})
    if query.keyword:
        conditions.append({"title": _contains(query.keyword)})
    for dimension in query.tags.dimensions():
        conditions.append(_tag_filter(dimension, query.tags.get(dimension)))
    if not conditions:
        return {}
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


class _Conflict(Exception):
    """읽은 뒤 다른 저장이 문서를 바꿨다. 저장을 처음부터 다시 한다."""


def _listing_fields(listing: Listing) -> dict[str, Any]:
    """출처 값(태그 제외)을 저장할 필드 문서로 바꾼다."""
    return {
        "title": listing.title,
        "host": listing.host,
        "field": listing.field,
        "apply_start": _to_datetime(listing.apply_start),
        "apply_end": _to_datetime(listing.apply_end),
        "event_start": _to_datetime(listing.event_start),
        "event_end": _to_datetime(listing.event_end),
        "apply_url": listing.apply_url,
        "source_url": listing.source_url,
    }


def _source_doc(listing: Listing, fetched_at: datetime) -> dict[str, Any]:
    """`sources` 배열 항목. 같은 대회 후보를 찾는 `url_key` 를 함께 둔다."""
    return {
        "source": listing.source,
        **_listing_fields(listing),
        "url_key": url_key(listing.apply_url),
        "tags": _tags_doc(listing.tags),
        "fetched_at": fetched_at,
    }


def _tags_doc(tags: ContestTags) -> dict[str, list[str]]:
    """분류 태그를 차원별 정렬된 목록 문서로 바꾼다."""
    return {d.value: tags.ordered(d) for d in Dimension}


def _to_tags(doc: dict[str, list[str]]) -> ContestTags:
    """분류 전에 저장된 문서는 tags 가 없다. 미분류로 본다."""
    return ContestTags(**{d.value: frozenset(doc.get(d.value, [])) for d in Dimension})


def _to_record(doc: Document) -> ContestRecord:
    """MongoDB 문서를 ContestRecord 로 바꾼다."""
    apply_end = _to_date(doc["apply_end"])
    if apply_end is None:
        raise ValueError(f"apply_end 가 없는 대회 문서: {doc['_id']}")
    return ContestRecord(
        id=str(doc["_id"]),
        title=doc["title"],
        host=doc["host"],
        field=doc["field"],
        apply_start=_to_date(doc.get("apply_start")),
        apply_end=apply_end,
        event_start=_to_date(doc.get("event_start")),
        event_end=_to_date(doc.get("event_end")),
        apply_url=doc.get("apply_url"),
        source_url=doc["source_url"],
        sources=tuple(
            ContestOrigin(
                source=s["source"],
                source_url=s["source_url"],
                representative=s["source"] == doc["source"],
            )
            for s in doc.get("sources", [])
        ),
        updated_at=doc["updated_at"],
        tags=_to_tags(doc.get("tags", {})),
    )


def _to_listing(doc: Document) -> Listing:
    """`sources` 항목(또는 같은 모양의 문서)을 Listing 으로 바꾼다."""
    apply_end = _to_date(doc["apply_end"])
    if apply_end is None:
        raise ValueError(f"apply_end 가 없는 출처: {doc.get('source')} {doc.get('source_url')}")
    return Listing(
        source=doc["source"],
        source_url=doc["source_url"],
        title=doc["title"],
        host=doc["host"],
        field=doc["field"],
        apply_start=_to_date(doc.get("apply_start")),
        apply_end=apply_end,
        event_start=_to_date(doc.get("event_start")),
        event_end=_to_date(doc.get("event_end")),
        apply_url=doc.get("apply_url"),
        tags=_to_tags(doc.get("tags", {})),
    )


def _top_fields(sources: list[dict[str, Any]]) -> dict[str, Any]:
    """출처들에서 고른 최상위 값 (목록·상세·필터가 읽는 값)."""
    shown = resolve([_to_listing(s) for s in sources])
    return {**_listing_fields(shown), "source": shown.source, "tags": _tags_doc(shown.tags)}


def _listings(doc: Document) -> list[Listing]:
    """문서에 묶인 출처들."""
    return [_to_listing(s) for s in doc["sources"]]


def _absorb(base: Document, others: list[Document]) -> tuple[list[dict[str, Any]], list[Document]]:
    """같은 대회의 다른 문서 출처를 base 로 모은다. 같은 사이트 출처가 겹치면 합치지 않는다."""
    sources = list(base["sources"])
    absorbed: list[Document] = []
    for doc in others:
        if {s["source"] for s in doc["sources"]} & {s["source"] for s in sources}:
            continue
        sources += doc["sources"]
        absorbed.append(doc)
    return sources, absorbed


def _with_listing(
    sources: list[dict[str, Any]], listing: Listing, now: datetime
) -> list[dict[str, Any]]:
    """출처 배열에 새로 수집한 값을 넣는다. 이미 있는 출처는 갈아 끼운다 (rules.refresh)."""
    for i, s in enumerate(sources):
        if s["source"] == listing.source and s["source_url"] == listing.source_url:
            fresh = _source_doc(refresh(_to_listing(s), listing), now)
            return [*sources[:i], fresh, *sources[i + 1 :]]
    return [*sources, _source_doc(listing, now)]


def _candidate_filter(listing: Listing) -> dict[str, Any]:
    """같은 대회일 수 있는 문서: 같은 원문, 같은 참가 URL, 같은 마감일 (rules.same_contest)."""
    same_listing = {"source": listing.source, "source_url": listing.source_url}
    conditions: list[dict[str, Any]] = [
        {"sources": {"$elemMatch": same_listing}},
        {"sources.apply_end": _to_datetime(listing.apply_end)},
    ]
    key = url_key(listing.apply_url)
    if key is not None:
        conditions.append({"sources.url_key": key})
    return {"$or": conditions}


def _has_listing(doc: Document, listing: Listing) -> bool:
    """문서에 이 사이트의 이 원문이 이미 있다."""
    return any(
        s["source"] == listing.source and s["source_url"] == listing.source_url
        for s in doc["sources"]
    )


class ContestRepository:
    """MongoDB 대회 컬렉션 저장소."""

    def __init__(self, db: Database[Document]) -> None:
        """대회 컬렉션을 잡는다."""
        self._contests = db[COLLECTION]

    def ensure_indexes(self) -> None:
        """이전 구조 문서를 옮기고, 출처 유일 인덱스와 조회·후보 검색용 인덱스를 만든다."""
        existing = self._contests.index_information()
        for name in LEGACY_INDEXES:
            if name in existing:
                self._contests.drop_index(name)
        self._migrate_legacy()
        self._drop_duplicate_listings()
        self._contests.create_index(
            [("sources.source", ASCENDING), ("sources.source_url", ASCENDING)], unique=True
        )
        self._contests.create_index([("sources.url_key", ASCENDING)])
        self._contests.create_index([("sources.apply_end", ASCENDING)])
        self._contests.create_index([("apply_end", ASCENDING)])

    def _migrate_legacy(self) -> None:
        """출처에 원래 값이 없는 이전 문서는 최상위 값을 출처마다 채운다 (멱등).

        이전에는 대표 소스의 값만 남겼다. 다른 출처는 같은 실행에서 다시 수집하면 제 값으로 바뀐다.
        """
        for doc in self._contests.find({"sources.title": {"$exists": False}}):
            shown = _to_listing(doc)
            sources = [
                _source_doc(
                    replace(shown, source=s["source"], source_url=s["source_url"]),
                    s["fetched_at"],
                )
                for s in doc.get("sources", [])
            ]
            self._contests.update_one(
                {"_id": doc["_id"]},
                {"$set": {"sources": sources, "rev": 0}, "$unset": {"merge_key": ""}},
            )

    def _drop_duplicate_listings(self) -> None:
        """여러 문서에 들어 있는 같은 출처는 가장 오래된 문서에만 남긴다 (출처 UNIQUE 인덱스 전제).

        이전 구조는 키를 옮기다 막히면 같은 출처를 다른 문서에 붙일 수 있었다. 출처가 모두 빠진
        문서는 지우고, 나머지는 남은 출처로 값을 다시 고른다.
        """
        groups = self._contests.aggregate(
            [
                {"$unwind": "$sources"},
                {
                    "$group": {
                        "_id": {"source": "$sources.source", "source_url": "$sources.source_url"},
                        "docs": {"$addToSet": "$_id"},
                    }
                },
                {"$match": {"docs.1": {"$exists": True}}},
            ]
        )
        now = datetime.now(KST)
        for group in groups:
            source, source_url = group["_id"]["source"], group["_id"]["source_url"]
            for doc_id in sorted(group["docs"])[1:]:
                doc = self._contests.find_one({"_id": doc_id})
                if doc is None:
                    continue
                kept = [
                    s
                    for s in doc["sources"]
                    if (s["source"], s["source_url"]) != (source, source_url)
                ]
                if kept:
                    self._write(doc, kept, now, saved="")
                else:
                    self._contests.delete_one({"_id": doc_id})

    def save(self, listing: Listing, now: datetime) -> SaveOutcome:
        """출처 하나를 같은 대회 문서에 넣는다. 여러 번 저장해도 결과가 같다 (F2-2 멱등).

        같은 대회 문서가 여럿이면(따로 저장돼 있던 중복) 하나로 합친다. 경합하면 다시 한다.
        """
        for _ in range(SAVE_ATTEMPTS):
            try:
                return self._save_once(listing, now)
            except _Conflict:
                continue
        raise RuntimeError(f"대회 저장 경합이 계속된다: {listing.source} {listing.source_url}")

    def _save_once(self, listing: Listing, now: datetime) -> SaveOutcome:
        """후보 문서를 읽고 출처를 붙여 한 번 저장한다. 그 사이 문서가 바뀌었으면 _Conflict."""
        docs = list(self._contests.find(_candidate_filter(listing)).sort("_id", ASCENDING))
        own = next((d for d in docs if _has_listing(d, listing)), None)
        matches = [d for d in docs if d is not own and belongs_to(listing, _listings(d))]
        if own is None and not matches:
            self._insert(listing, now)
            return SaveOutcome.INSERTED
        base = own or matches[0]
        sources, absorbed = _absorb(base, [d for d in matches if d is not base])
        self._merge(base, absorbed, _with_listing(sources, listing, now), now, listing.source)
        return SaveOutcome.UPDATED if own is not None else SaveOutcome.MERGED

    def _merge(
        self,
        base: Document,
        absorbed: list[Document],
        sources: list[dict[str, Any]],
        now: datetime,
        saved: str,
    ) -> None:
        """중복 문서를 지우고 합친 출처를 base 에 쓴다. 중간에 경합하면 지운 문서를 되살린다.

        출처 UNIQUE 인덱스 때문에 base 를 먼저 쓸 수 없다. CI·Atlas 무료 등급의 단일 노드에서도
        돌도록 트랜잭션 대신 보상 처리로 원래 문서(같은 _id)를 복원한다.
        """
        deleted: list[Document] = []
        try:
            for doc in absorbed:
                if not self._contests.delete_one(
                    {"_id": doc["_id"], "rev": doc["rev"]}
                ).deleted_count:
                    raise _Conflict
                deleted.append(doc)
            self._write(base, sources, now, saved=saved)
        except _Conflict:
            for doc in deleted:
                # 그 사이 같은 출처가 다른 문서에 들어갔으면 복원하지 않는다 (다음 수집이 채운다)
                with suppress(DuplicateKeyError):
                    self._contests.insert_one(doc)
            raise

    def _insert(self, listing: Listing, now: datetime) -> None:
        """새 대회 문서를 만든다. 같은 원문을 동시에 넣으면 UNIQUE 인덱스에 걸려 다시 한다."""
        sources = [_source_doc(listing, now)]
        doc = {**_top_fields(sources), "sources": sources, "rev": 0}
        try:
            self._contests.insert_one({**doc, "created_at": now, "updated_at": now})
        except DuplicateKeyError as exc:
            raise _Conflict from exc

    def _write(
        self, base: Document, sources: list[dict[str, Any]], now: datetime, *, saved: str
    ) -> None:
        """출처 배열과 다시 고른 최상위 값을 쓴다.

        보여 주는 값이 바뀌었거나 대표 출처(saved)를 다시 수집했으면 갱신 시각을 올린다 (Q-29).
        """
        top = _top_fields(sources)
        fields: dict[str, Any] = {**top, "sources": sources}
        if top["source"] == saved or any(base.get(k) != v for k, v in top.items()):
            fields["updated_at"] = now
        result = self._contests.update_one(
            {"_id": base["_id"], "rev": base.get("rev", 0)},
            {"$set": fields, "$inc": {"rev": 1}},
        )
        if not result.matched_count:
            raise _Conflict

    def prune_missing(self, source: str, seen_urls: set[str], now: datetime) -> int:
        """성공한 수집에서 빠진 출처를 묶음에서 뺀다. 출처가 하나뿐이면 마감 삭제(Q-3)까지 둔다.

        뺀 출처 수를 돌려준다. 그 사이 다른 저장이 문서를 바꿨으면 그 문서는 다음 수집으로 미룬다.
        """
        stale = {"source": source, "source_url": {"$nin": sorted(seen_urls)}}
        removed = 0
        query = {"sources": {"$elemMatch": stale}, "sources.1": {"$exists": True}}
        for doc in self._contests.find(query):
            kept = [
                s for s in doc["sources"] if s["source"] != source or s["source_url"] in seen_urls
            ]
            try:
                self._write(doc, kept, now, saved="")
            except _Conflict:
                continue
            removed += len(doc["sources"]) - len(kept)
        return removed

    def find(self, query: ContestQuery, today: date) -> tuple[list[ContestRecord], int]:
        """조건에 맞는 한 페이지와 전체 건수."""
        mongo_filter = build_filter(query, today)
        total = self._contests.count_documents(mongo_filter)
        cursor = (
            self._contests.find(mongo_filter)
            .sort(_SORT_KEYS[query.sort])
            .skip((query.page - 1) * query.size)
            .limit(query.size)
        )
        return [_to_record(doc) for doc in cursor], total

    def get(self, contest_id: str) -> ContestRecord | None:
        """ID 로 대회를 찾는다. 잘못된 ID 면 None."""
        if not ObjectId.is_valid(contest_id):
            return None
        doc = self._contests.find_one({"_id": ObjectId(contest_id)})
        return None if doc is None else _to_record(doc)

    def get_many(self, contest_ids: list[str]) -> dict[str, ContestRecord]:
        """id 별 대회. 없는(삭제된) 대회는 결과에서 빠진다."""
        object_ids = [ObjectId(i) for i in contest_ids if ObjectId.is_valid(i)]
        if not object_ids:
            return {}
        records = map(_to_record, self._contests.find({"_id": {"$in": object_ids}}))
        return {record.id: record for record in records}

    def delete_closed_before(self, cutoff: date) -> int:
        """접수 마감일이 cutoff 보다 이른 대회를 지운다 (Q-3). 지운 건수를 돌려준다."""
        result = self._contests.delete_many({"apply_end": {"$lt": _to_datetime(cutoff)}})
        return result.deleted_count


@dataclass(frozen=True)
class BookmarkRecord:
    """회원이 저장한 대회 한 건."""

    contest_id: str
    created_at: datetime


class BookmarkRepository:
    """PostgreSQL 대회 저장(북마크) 저장소."""

    def __init__(self, session: Session) -> None:
        """요청 단위 세션을 받는다."""
        self._session = session

    def add(self, user_id: int, contest_id: str, now: datetime) -> None:
        """이미 저장돼 있으면 처음 저장한 시각을 그대로 둔다 (멱등)."""
        if self._session.get(Bookmark, (user_id, contest_id)) is not None:
            return
        self._session.add(Bookmark(user_id=user_id, contest_id=contest_id, created_at=now))
        try:
            self._session.commit()
        except IntegrityError:
            # 같은 요청이 동시에 들어오면 PK 가 한쪽을 막는다. 먼저 저장한 쪽을 따른다.
            self._session.rollback()

    def remove(self, user_id: int, contest_id: str) -> None:
        """저장한 대회를 지운다. 없어도 오류가 아니다."""
        self._session.execute(
            delete(Bookmark).where(Bookmark.user_id == user_id, Bookmark.contest_id == contest_id)
        )
        self._session.commit()

    def list_for_user(self, user_id: int) -> list[BookmarkRecord]:
        """최근 저장순 전체 목록."""
        rows = self._session.scalars(
            select(Bookmark)
            .where(Bookmark.user_id == user_id)
            .order_by(Bookmark.created_at.desc(), Bookmark.contest_id)
        )
        return [BookmarkRecord(contest_id=r.contest_id, created_at=r.created_at) for r in rows]

    def exists(self, user_id: int, contest_id: str) -> bool:
        """회원이 이 대회를 저장했는지 본다."""
        return self._session.get(Bookmark, (user_id, contest_id)) is not None


class ClickRepository:
    """PostgreSQL 참가 바로가기 클릭 저장소 (F2-5). 클릭 수는 행을 세어 집계한다."""

    def __init__(self, session: Session) -> None:
        """요청 단위 세션을 받는다."""
        self._session = session

    def add(self, contest_id: str, user_id: int | None, now: datetime) -> None:
        """클릭 한 건을 남긴다. 같은 회원이 여러 번 눌러도 매번 센다."""
        self._session.add(ContestClick(contest_id=contest_id, user_id=user_id, clicked_at=now))
        self._session.commit()
