"""대회 저장소 (MongoDB `contests` 컬렉션) + 북마크 저장소 (PostgreSQL `bookmarks`, ADR-0008).

문서 하나가 병합된 대회 하나다. 날짜는 KST 자정 tz-aware datetime 으로 저장한다
(BSON 에 날짜 전용 타입이 없다). 수집 출처는 `sources` 배열에 쌓고, 처음 수집한 소스가
대표(`source`)가 되어 값을 갱신한다 (Q-2 기본값).

동시에 여러 수집이 같은 대회를 저장해도 문서·출처가 겹치지 않게, 갱신은 '확인 후 쓰기'
대신 조건을 건 단일 연산과 merge_key UNIQUE 인덱스에 맡긴다.
"""

from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass
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

from app.contests.crawlers.base import RawContest
from app.contests.models import Bookmark
from app.contests.rules import ContestQuery, ContestStatus, SortOrder
from app.core.db import Document
from app.core.time import KST

COLLECTION = "contests"


class SaveOutcome(StrEnum):
    INSERTED = "inserted"  # 새 대회
    UPDATED = "updated"  # 대표 소스가 다시 수집해 값을 갱신
    MERGED = "merged"  # 다른 소스의 같은 대회, 출처만 추가·갱신


@dataclass(frozen=True)
class ContestRecord:
    id: str
    title: str
    host: str
    field: str
    apply_start: date | None
    apply_end: date
    event_start: date | None
    event_end: date | None
    eligibility_text: str
    apply_url: str
    source_url: str
    sources: tuple[str, ...]


_SORT_KEYS: dict[SortOrder, list[tuple[str, int]]] = {
    SortOrder.DEADLINE: [("apply_end", ASCENDING), ("_id", ASCENDING)],
    SortOrder.LATEST: [("created_at", DESCENDING), ("_id", DESCENDING)],
}


def _to_datetime(value: date | None) -> datetime | None:
    return None if value is None else datetime.combine(value, time.min, tzinfo=KST)


def _to_date(value: datetime | None) -> date | None:
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


def build_filter(query: ContestQuery, today: date) -> dict[str, Any]:
    midnight = datetime.combine(today, time.min, tzinfo=KST)
    conditions: list[dict[str, Any]] = []
    if query.statuses:
        statuses = sorted(query.statuses)
        conditions.append({"$or": [_status_filter(s, midnight) for s in statuses]})
    if query.fields:
        conditions.append({"$or": [{"field": _contains(f)} for f in query.fields]})
    if query.keyword:
        conditions.append({"title": _contains(query.keyword)})
    if not conditions:
        return {}
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def _fields(contest: RawContest) -> dict[str, Any]:
    return {
        "title": contest.title,
        "host": contest.host,
        "field": contest.field,
        "apply_start": _to_datetime(contest.apply_start),
        "apply_end": _to_datetime(contest.apply_end),
        "event_start": _to_datetime(contest.event_start),
        "event_end": _to_datetime(contest.event_end),
        "eligibility_text": contest.eligibility_text,
        "apply_url": contest.apply_url,
        "source_url": contest.source_url,
    }


def _to_record(doc: Document) -> ContestRecord:
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
        eligibility_text=doc.get("eligibility_text", ""),
        apply_url=doc["apply_url"],
        source_url=doc["source_url"],
        sources=tuple(s["source"] for s in doc.get("sources", [])),
    )


class ContestRepository:
    def __init__(self, db: Database[Document]) -> None:
        self._contests = db[COLLECTION]

    def ensure_indexes(self) -> None:
        self._contests.create_index([("merge_key", ASCENDING)], unique=True)
        self._contests.create_index([("apply_end", ASCENDING)])
        self._contests.create_index([("source", ASCENDING), ("source_url", ASCENDING)])

    def save(self, contest: RawContest, merge_key: str, now: datetime) -> SaveOutcome:
        """merge_key 기준 upsert. 같은 입력을 여러 번 저장해도 문서는 하나다 (F2-2 멱등)."""
        self._rekey(contest, merge_key)
        fields = _fields(contest)
        origin = {"source": contest.source, "source_url": contest.source_url, "fetched_at": now}
        if self._insert_if_absent(merge_key, fields, origin, now):
            return SaveOutcome.INSERTED

        representative = self._contests.update_one(
            {"merge_key": merge_key, "source": contest.source},
            {"$set": {**fields, "updated_at": now}},
        )
        self._upsert_origin(merge_key, origin)
        return SaveOutcome.UPDATED if representative.matched_count else SaveOutcome.MERGED

    def _rekey(self, contest: RawContest, merge_key: str) -> None:
        """대표 소스가 마감일·제목을 바꿔 merge_key 가 달라지면 기존 문서의 키를 옮긴다.

        같은 원문 URL(source + source_url)을 같은 대회로 본다. 새 키의 문서가 이미 있으면
        (다른 소스와 병합됐거나 동시에 다른 작업이 만든 경우) UNIQUE 인덱스가 막으므로
        옮기지 않는다. 미리 확인하지 않고 시도해서 확인과 갱신 사이의 경합을 없앤다.
        """
        with suppress(DuplicateKeyError):
            self._contests.update_one(
                {
                    "source": contest.source,
                    "source_url": contest.source_url,
                    "merge_key": {"$ne": merge_key},
                },
                {"$set": {"merge_key": merge_key}},
            )

    def _insert_if_absent(
        self, merge_key: str, fields: dict[str, Any], origin: dict[str, Any], now: datetime
    ) -> bool:
        """없으면 새 문서를 만들고 True. 이미 있거나 동시에 다른 작업이 먼저 만들었으면 False."""
        try:
            result = self._contests.update_one(
                {"merge_key": merge_key},
                {
                    "$setOnInsert": {
                        **fields,
                        "merge_key": merge_key,
                        "source": origin["source"],
                        "sources": [origin],
                        "created_at": now,
                        "updated_at": now,
                    }
                },
                upsert=True,
            )
        except DuplicateKeyError:
            # 같은 merge_key 를 동시에 upsert 하면 한쪽이 UNIQUE 인덱스에 걸린다.
            # 먼저 만든 쪽을 따르고 이후 갱신·병합 단계로 넘어간다.
            return False
        return result.upserted_id is not None

    def _upsert_origin(self, merge_key: str, origin: dict[str, Any]) -> None:
        """출처를 추가하거나 갱신한다.

        '없으면 추가'를 `sources.source $ne` 조건을 건 단일 $push 로 해서, 동시에 불려도 같은
        소스가 두 번 들어가지 않는다. 이미 있으면 그 항목만 갱신한다.
        """
        pushed = self._contests.update_one(
            {"merge_key": merge_key, "sources.source": {"$ne": origin["source"]}},
            {"$push": {"sources": origin}},
        )
        if pushed.matched_count:
            return
        self._contests.update_one(
            {"merge_key": merge_key, "sources.source": origin["source"]},
            {
                "$set": {
                    "sources.$.source_url": origin["source_url"],
                    "sources.$.fetched_at": origin["fetched_at"],
                }
            },
        )

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
    contest_id: str
    created_at: datetime


class BookmarkRepository:
    def __init__(self, session: Session) -> None:
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
