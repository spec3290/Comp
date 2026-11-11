"""대회 API 응답 스키마 (ADR-0004 페이지네이션: {items, page, size, total})."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from app.contests.repository import ContestOrigin
from app.contests.rules import ContestStatus, ContestTags, Dimension
from app.contests.service import BookmarkPage, BookmarkView, ContestPage, ContestView


class ContestTagsOut(BaseModel):
    """분류된 분야·형식 태그 (F3-1). 빈 목록은 그 차원이 미분류라는 뜻이다."""

    field: list[str]
    format: list[str]

    @classmethod
    def from_tags(cls, tags: ContestTags) -> ContestTagsOut:
        """분류 태그로 응답을 만든다."""
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
    apply_url: str | None  # null 이면 프론트가 '직접 검색해서 찾아 주세요' 안내를 띄운다 (Q-29)
    source_url: str
    tags: ContestTagsOut
    tag_check_needed: bool

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
            tags=ContestTagsOut.from_tags(r.tags),
            tag_check_needed=view.tag_check_needed,
        )


class ContestSourceOut(BaseModel):
    """대회를 수집한 사이트와 그 사이트의 원문 링크. 같은 대회를 여러 사이트에서 볼 수 있다."""

    source: str
    source_url: str
    is_representative: bool

    @classmethod
    def from_origin(cls, origin: ContestOrigin) -> ContestSourceOut:
        """수집 출처로 응답을 만든다."""
        return cls(
            source=origin.source,
            source_url=origin.source_url,
            is_representative=origin.representative,
        )


class ContestDetailOut(ContestSummaryOut):
    """대회 상세 응답. 사이트별 원문 링크·정보 갱신 시각·저장 여부를 더한다 (Q-29)."""

    sources: list[ContestSourceOut]
    updated_at: datetime
    bookmarked: bool | None  # 비로그인이면 null

    @classmethod
    def from_detail(cls, view: ContestView, bookmarked: bool | None) -> ContestDetailOut:
        """조회 결과와 저장 여부로 응답을 만든다."""
        summary = ContestSummaryOut.from_view(view)
        return cls(
            **summary.model_dump(),
            sources=[ContestSourceOut.from_origin(o) for o in view.record.sources],
            updated_at=view.record.updated_at,
            bookmarked=bookmarked,
        )


class ApplyLinkOut(BaseModel):
    """참가 바로가기 응답 (F2-5). 프론트가 이 주소를 새 창으로 연다."""

    apply_url: str


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
