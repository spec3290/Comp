"""대회 API: 목록 (F2-3), 상세 (F2-4), 저장 (F2-7).

목록·상세는 인증이 선택이고 (ADR-0004), 저장은 Access 토큰이 필요하다.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.auth.dependencies import CurrentUserId
from app.contests.rules import DEFAULT_STATUSES, ContestQuery, ContestStatus, SortOrder
from app.contests.schemas import BookmarkPageOut, ContestDetailOut, ContestPageOut
from app.contests.service import (
    BookmarkService,
    ContestQueryService,
    get_bookmark_service,
    get_query_service,
)
from app.core.errors import BadRequestError
from app.core.time import now_kst

router = APIRouter(prefix="/contests", tags=["contests"])
me_router = APIRouter(prefix="/users/me", tags=["bookmarks"])

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

Service = Annotated[ContestQueryService, Depends(get_query_service)]
Bookmarks = Annotated[BookmarkService, Depends(get_bookmark_service)]


def today_kst() -> date:
    return now_kst().date()


Today = Annotated[date, Depends(today_kst)]


def _split(value: str | None) -> tuple[str, ...]:
    """쉼표로 구분한 값 목록. 빈 값과 앞뒤 공백은 버린다."""
    return tuple(v.strip() for v in (value or "").split(",") if v.strip())


def _parse_statuses(value: str | None) -> frozenset[ContestStatus]:
    names = _split(value)
    if not names:
        return DEFAULT_STATUSES
    allowed = {s.value for s in ContestStatus}
    unknown = [n for n in names if n not in allowed]
    if unknown:
        raise BadRequestError(
            f"status: 허용 값은 {', '.join(sorted(allowed))} (받은 값: {unknown})"
        )
    return frozenset(ContestStatus(n) for n in names)


@router.get("", response_model=ContestPageOut)
def list_contests(
    service: Service,
    today: Today,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    sort: SortOrder = SortOrder.DEADLINE,
    status: Annotated[str | None, Query(description="open,upcoming,closed 중 쉼표 구분")] = None,
    field: Annotated[str | None, Query(description="분야 부분 일치, 쉼표 구분 시 OR")] = None,
    q: Annotated[str | None, Query(max_length=100, description="제목 검색")] = None,
) -> ContestPageOut:
    query = ContestQuery(
        statuses=_parse_statuses(status),
        fields=_split(field),
        keyword=q.strip() if q and q.strip() else None,
        sort=sort,
        page=page,
        size=size,
    )
    return ContestPageOut.from_page(service.list_contests(query, today))


@router.get("/{contest_id}", response_model=ContestDetailOut)
def get_contest(contest_id: str, service: Service, today: Today) -> ContestDetailOut:
    return ContestDetailOut.from_view(service.get_contest(contest_id, today))


@router.put("/{contest_id}/bookmark", status_code=status.HTTP_204_NO_CONTENT)
def bookmark_contest(
    contest_id: str, user_id: CurrentUserId, service: Bookmarks, today: Today
) -> Response:
    service.add(user_id, contest_id, today, now_kst())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{contest_id}/bookmark", status_code=status.HTTP_204_NO_CONTENT)
def unbookmark_contest(contest_id: str, user_id: CurrentUserId, service: Bookmarks) -> Response:
    service.remove(user_id, contest_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@me_router.get("/bookmarks", response_model=BookmarkPageOut)
def list_my_bookmarks(
    user_id: CurrentUserId,
    service: Bookmarks,
    today: Today,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> BookmarkPageOut:
    return BookmarkPageOut.from_page(service.list_bookmarks(user_id, today, page, size))
