"""대회 저장소 (MongoDB `contests` 컬렉션, ADR-0008).

문서 하나가 병합된 대회 하나다. 날짜는 KST 자정 tz-aware datetime 으로 저장한다
(BSON 에 날짜 전용 타입이 없다). 수집 출처는 `sources` 배열에 쌓고, 처음 수집한 소스가
대표(`source`)가 되어 값을 갱신한다 (Q-2 기본값).
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum
from typing import Any

from pymongo import ASCENDING
from pymongo.database import Database

from app.contests.crawlers.base import RawContest
from app.core.db import Document
from app.core.time import KST

COLLECTION = "contests"


class SaveOutcome(StrEnum):
    INSERTED = "inserted"  # 새 대회
    UPDATED = "updated"  # 대표 소스가 다시 수집해 값을 갱신
    MERGED = "merged"  # 다른 소스의 같은 대회, 출처만 추가·갱신


def _to_datetime(value: date | None) -> datetime | None:
    return None if value is None else datetime.combine(value, time.min, tzinfo=KST)


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


class ContestRepository:
    def __init__(self, db: Database[Document]) -> None:
        self._contests = db[COLLECTION]

    def ensure_indexes(self) -> None:
        self._contests.create_index([("merge_key", ASCENDING)], unique=True)
        self._contests.create_index([("apply_end", ASCENDING)])

    def save(self, contest: RawContest, merge_key: str, now: datetime) -> SaveOutcome:
        """merge_key 기준 upsert. 같은 입력을 여러 번 저장해도 문서는 하나다 (F2-2 멱등)."""
        fields = _fields(contest)
        origin = {"source": contest.source, "source_url": contest.source_url, "fetched_at": now}
        inserted = self._contests.update_one(
            {"merge_key": merge_key},
            {
                "$setOnInsert": {
                    **fields,
                    "merge_key": merge_key,
                    "source": contest.source,
                    "sources": [origin],
                    "created_at": now,
                    "updated_at": now,
                }
            },
            upsert=True,
        )
        if inserted.upserted_id is not None:
            return SaveOutcome.INSERTED

        representative = self._contests.update_one(
            {"merge_key": merge_key, "source": contest.source},
            {"$set": {**fields, "updated_at": now}},
        )
        self._upsert_origin(merge_key, origin)
        return SaveOutcome.UPDATED if representative.matched_count else SaveOutcome.MERGED

    def _upsert_origin(self, merge_key: str, origin: dict[str, Any]) -> None:
        refreshed = self._contests.update_one(
            {"merge_key": merge_key, "sources.source": origin["source"]},
            {
                "$set": {
                    "sources.$.source_url": origin["source_url"],
                    "sources.$.fetched_at": origin["fetched_at"],
                }
            },
        )
        if refreshed.matched_count == 0:
            self._contests.update_one({"merge_key": merge_key}, {"$push": {"sources": origin}})
