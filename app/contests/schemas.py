"""대회 API 응답 스키마 (ADR-0004 페이지네이션: {items, page, size, total})."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from app.contests.rules import ContestStatus
from app.contests.service import BookmarkPage, BookmarkView, ContestPage, ContestView


class ContestSummaryOut(BaseModel):
    id: str
    title: str
    host: str
    field: str
    status: ContestStatus
    d_day: int
    apply_start: date | None
    apply_end: date
    event_start: date | None
    event_end: date | None
    apply_url: str
    source_url: str

    @classmethod
    def from_view(cls, view: ContestView) -> ContestSummaryOut:
        r = view.record
        return cls(
            id=r.id,
            title=r.title,
            host=r.host,
            field=r.field,
            status=view.status,
            d_day=view.d_day,
            apply_start=r.apply_start,
            apply_end=r.apply_end,
            event_start=r.event_start,
            event_end=r.event_end,
            apply_url=r.apply_url,
            source_url=r.source_url,
        )


class ContestDetailOut(ContestSummaryOut):
    eligibility_text: str
    sources: list[str]

    @classmethod
    def from_view(cls, view: ContestView) -> ContestDetailOut:
        summary = ContestSummaryOut.from_view(view)
        return cls(
            **summary.model_dump(),
            eligibility_text=view.record.eligibility_text,
            sources=list(view.record.sources),
        )


class ContestPageOut(BaseModel):
    items: list[ContestSummaryOut]
    page: int
    size: int
    total: int

    @classmethod
    def from_page(cls, page: ContestPage) -> ContestPageOut:
        return cls(
            items=[ContestSummaryOut.from_view(v) for v in page.items],
            page=page.page,
            size=page.size,
            total=page.total,
        )


class BookmarkedContestOut(ContestSummaryOut):
    bookmarked_at: datetime

    @classmethod
    def from_bookmark(cls, view: BookmarkView) -> BookmarkedContestOut:
        summary = ContestSummaryOut.from_view(view.contest)
        return cls(**summary.model_dump(), bookmarked_at=view.bookmarked_at)


class BookmarkPageOut(BaseModel):
    items: list[BookmarkedContestOut]
    page: int
    size: int
    total: int

    @classmethod
    def from_page(cls, page: BookmarkPage) -> BookmarkPageOut:
        return cls(
            items=[BookmarkedContestOut.from_bookmark(v) for v in page.items],
            page=page.page,
            size=page.size,
            total=page.total,
        )
