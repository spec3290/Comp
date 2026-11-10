"""대회 API 응답 스키마 (ADR-0004 페이지네이션: {items, page, size, total})."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from app.contests.rules import ContestStatus, Dimension, EligibilityTags
from app.contests.service import BookmarkPage, BookmarkView, ContestPage, ContestView


class EligibilityTagsOut(BaseModel):
    """분류된 자격 태그 (F3-1). 빈 목록은 그 차원이 미분류라는 뜻이다."""

    education: list[str]
    team_type: list[str]
    region: list[str]

    @classmethod
    def from_tags(cls, tags: EligibilityTags) -> EligibilityTagsOut:
        """자격 태그로 응답을 만든다."""
        return cls(**{d.value: tags.ordered(d) for d in Dimension})


class ContestSummaryOut(BaseModel):
    """대회 목록의 한 항목 응답."""

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
    eligibility_tags: EligibilityTagsOut
    eligibility_check_needed: bool

    @classmethod
    def from_view(cls, view: ContestView) -> ContestSummaryOut:
        """조회 결과로 응답을 만든다."""
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
            eligibility_tags=EligibilityTagsOut.from_tags(r.tags),
            eligibility_check_needed=view.eligibility_check_needed,
        )


class ContestDetailOut(ContestSummaryOut):
    """대회 상세 응답. 자격 원문과 출처를 더한다."""

    eligibility_text: str
    sources: list[str]

    @classmethod
    def from_view(cls, view: ContestView) -> ContestDetailOut:
        """조회 결과로 응답을 만든다."""
        summary = ContestSummaryOut.from_view(view)
        return cls(
            **summary.model_dump(),
            eligibility_text=view.record.eligibility_text,
            sources=list(view.record.sources),
        )


class ContestPageOut(BaseModel):
    """대회 목록 페이지 응답."""

    items: list[ContestSummaryOut]
    page: int
    size: int
    total: int

    @classmethod
    def from_page(cls, page: ContestPage) -> ContestPageOut:
        """조회 결과 페이지로 응답을 만든다."""
        return cls(
            items=[ContestSummaryOut.from_view(v) for v in page.items],
            page=page.page,
            size=page.size,
            total=page.total,
        )


class BookmarkedContestOut(ContestSummaryOut):
    """저장한 대회 한 항목 응답. 저장 시각을 더한다."""

    bookmarked_at: datetime

    @classmethod
    def from_bookmark(cls, view: BookmarkView) -> BookmarkedContestOut:
        """저장 조회 결과로 응답을 만든다."""
        summary = ContestSummaryOut.from_view(view.contest)
        return cls(**summary.model_dump(), bookmarked_at=view.bookmarked_at)


class BookmarkPageOut(BaseModel):
    """저장한 대회 목록 페이지 응답."""

    items: list[BookmarkedContestOut]
    page: int
    size: int
    total: int

    @classmethod
    def from_page(cls, page: BookmarkPage) -> BookmarkPageOut:
        """저장 조회 결과 페이지로 응답을 만든다."""
        return cls(
            items=[BookmarkedContestOut.from_bookmark(v) for v in page.items],
            page=page.page,
            size=page.size,
            total=page.total,
        )
