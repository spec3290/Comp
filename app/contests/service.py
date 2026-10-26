"""대회 수집 (F2-1) + 중복 제거 (F2-2) + 조회 (F2-3, F2-4) + 저장 (F2-7).

수집: 소스마다 독립적으로 수집한다. 한 소스가 실패해도 기존 데이터는 그대로 두고 로그만
남기며, 다른 소스는 계속 진행한다. 마감 3일이 지난 대회는 지운다 (Q-3).
조회: 상태·D-day 는 저장하지 않고 요청 시점의 KST 날짜로 계산한다.
저장: 마감된 대회는 새로 저장할 수 없다 (Q-18). 목록에서는 삭제된 대회를 건너뛴다.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.contests.crawlers.base import ContestSource
from app.contests.repository import (
    BookmarkRepository,
    ContestRecord,
    ContestRepository,
    SaveOutcome,
)
from app.contests.rules import ApplyPeriod, ContestQuery, ContestStatus, merge_key, purge_cutoff
from app.core.db import get_database
from app.core.errors import ContestClosedError, NotFoundError
from app.core.sql import get_session
from app.core.time import now_kst

logger = logging.getLogger(__name__)


@dataclass
class CollectResult:
    source: str
    fetched: int = 0
    skipped: int = 0  # 이미 삭제 기준을 넘긴 마감 대회 (Q-3)
    outcomes: Counter[SaveOutcome] = field(default_factory=Counter)
    error: str | None = None


def collect(
    sources: Iterable[ContestSource], repo: ContestRepository, today: date
) -> list[CollectResult]:
    return [_collect_one(source, repo, today) for source in sources]


def _collect_one(source: ContestSource, repo: ContestRepository, today: date) -> CollectResult:
    result = CollectResult(source=source.name)
    cutoff = purge_cutoff(today)
    try:
        contests = source.fetch()
        result.fetched = len(contests)
        for contest in contests:
            if contest.apply_end < cutoff:
                result.skipped += 1
                continue
            key = merge_key(contest.title, contest.host, contest.apply_end)
            result.outcomes[repo.save(contest, key, now_kst())] += 1
    except Exception as exc:
        logger.exception("수집 실패: %s", source.name)
        result.error = f"{type(exc).__name__}: {exc}"
        return result
    counts = {outcome.value: n for outcome, n in result.outcomes.items()}
    logger.info(
        "수집 완료: %s fetched=%d skipped=%d %s",
        source.name,
        result.fetched,
        result.skipped,
        counts,
    )
    return result


def purge_closed(repo: ContestRepository, today: date) -> int:
    deleted = repo.delete_closed_before(purge_cutoff(today))
    logger.info("마감 대회 삭제: %d건 (마감일 < %s)", deleted, purge_cutoff(today))
    return deleted


@dataclass(frozen=True)
class ContestView:
    record: ContestRecord
    status: ContestStatus
    d_day: int


@dataclass(frozen=True)
class ContestPage:
    items: list[ContestView]
    page: int
    size: int
    total: int


def _view(record: ContestRecord, today: date) -> ContestView:
    period = ApplyPeriod(start=record.apply_start, end=record.apply_end)
    return ContestView(record=record, status=period.status(today), d_day=period.d_day(today))


class ContestQueryService:
    def __init__(self, repo: ContestRepository) -> None:
        self._repo = repo

    def list_contests(self, query: ContestQuery, today: date) -> ContestPage:
        records, total = self._repo.find(query, today)
        items = [_view(record, today) for record in records]
        return ContestPage(items=items, page=query.page, size=query.size, total=total)

    def get_contest(self, contest_id: str, today: date) -> ContestView:
        record = self._repo.get(contest_id)
        if record is None:
            raise NotFoundError("대회를 찾을 수 없습니다.")
        return _view(record, today)


def get_query_service() -> ContestQueryService:
    return ContestQueryService(ContestRepository(get_database()))


@dataclass(frozen=True)
class BookmarkView:
    contest: ContestView
    bookmarked_at: datetime


@dataclass(frozen=True)
class BookmarkPage:
    items: list[BookmarkView]
    page: int
    size: int
    total: int


class BookmarkService:
    def __init__(self, bookmarks: BookmarkRepository, contests: ContestRepository) -> None:
        self._bookmarks = bookmarks
        self._contests = contests

    def add(self, user_id: int, contest_id: str, today: date, now: datetime) -> None:
        record = self._contests.get(contest_id)
        if record is None:
            raise NotFoundError("대회를 찾을 수 없습니다.")
        if _view(record, today).status is ContestStatus.CLOSED:
            raise ContestClosedError("접수가 마감된 대회는 저장할 수 없습니다.")
        self._bookmarks.add(user_id, contest_id, now)

    def remove(self, user_id: int, contest_id: str) -> None:
        """저장하지 않았거나 이미 삭제된 대회여도 성공으로 본다 (멱등)."""
        self._bookmarks.remove(user_id, contest_id)

    def list_bookmarks(self, user_id: int, today: date, page: int, size: int) -> BookmarkPage:
        """최근 저장순. 삭제된 대회(Q-3)는 건너뛰고 total 에서도 뺀다."""
        bookmarks = self._bookmarks.list_for_user(user_id)
        records = self._contests.get_many([b.contest_id for b in bookmarks])
        views = [
            BookmarkView(contest=_view(records[b.contest_id], today), bookmarked_at=b.created_at)
            for b in bookmarks
            if b.contest_id in records
        ]
        start = (page - 1) * size
        return BookmarkPage(
            items=views[start : start + size], page=page, size=size, total=len(views)
        )


def get_bookmark_service(session: Annotated[Session, Depends(get_session)]) -> BookmarkService:
    return BookmarkService(BookmarkRepository(session), ContestRepository(get_database()))
