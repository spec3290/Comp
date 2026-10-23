"""대회 조회 API: 목록 (F2-3), 상세 (F2-4). 인증은 아직 없다 (ADR-0004: 선택)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.contests.rules import DEFAULT_STATUSES, ContestQuery, ContestStatus, SortOrder
from app.contests.schemas import ContestDetailOut, ContestPageOut
from app.contests.service import ContestQueryService, get_query_service
from app.core.errors import BadRequestError
from app.core.time import now_kst

router = APIRouter(prefix="/contests", tags=["contests"])

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

Service = Annotated[ContestQueryService, Depends(get_query_service)]


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
