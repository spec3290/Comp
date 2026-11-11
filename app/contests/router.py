"""대회 API: 목록 (F2-3, 분야·형식 필터 F3-3), 상세 (F2-4), 참가 바로가기 (F2-5), 저장 (F2-7).

목록·상세·바로가기는 인증이 선택이고 (ADR-0004), 저장은 Access 토큰이 필요하다.
목록의 `interests=true` 는 로그인 회원의 프로필 관심 분야로 분야를 거른다. 기본 목록에는
관심 분야를 적용하지 않는다 (Q-24).
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.auth.dependencies import CurrentUserId, OptionalUserId
from app.auth.service import UserService, get_user_service
from app.auth.tokens import TokenInvalidError
from app.contests.rules import (
    DEFAULT_STATUSES,
    ContestField,
    ContestFormat,
    ContestQuery,
    ContestStatus,
    ContestTags,
    SortOrder,
)
from app.contests.schemas import ApplyLinkOut, BookmarkPageOut, ContestDetailOut, ContestPageOut
from app.contests.service import (
    ApplyService,
    BookmarkService,
    ContestQueryService,
    get_apply_service,
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
Apply = Annotated[ApplyService, Depends(get_apply_service)]
Users = Annotated[UserService, Depends(get_user_service)]


def today_kst() -> date:
    """요청 시점의 KST 날짜. 테스트에서 고정할 수 있게 의존성으로 둔다."""
    return now_kst().date()


Today = Annotated[date, Depends(today_kst)]


def _split(value: str | None) -> tuple[str, ...]:
    """쉼표로 구분한 값 목록. 빈 값과 앞뒤 공백은 버린다."""
    return tuple(v.strip() for v in (value or "").split(",") if v.strip())


def _parse_choices(name: str, value: str | None, allowed: type[StrEnum]) -> frozenset[str]:
    """쉼표로 구분한 값이 모두 allowed 에 있어야 한다. 아니면 400."""
    values = _split(value)
    unknown = [v for v in values if v not in allowed]
    if unknown:
        choices = ", ".join(allowed)
        raise BadRequestError(f"{name}: 허용 값은 {choices} (받은 값: {unknown})")
    return frozenset(values)


def _parse_statuses(value: str | None) -> frozenset[ContestStatus]:
    """쉼표로 구분한 상태 값을 읽고 비어 있으면 기본 상태를 쓴다."""
    names = _parse_choices("status", value, ContestStatus)
    if not names:
        return DEFAULT_STATUSES
    return frozenset(ContestStatus(n) for n in names)


def _field_filter(
    field: str | None, interests: bool, user_id: int | None, users: UserService
) -> frozenset[str]:
    """분야 필터 값. interests 면 직접 고른 분야 대신 프로필 관심 분야를 쓴다 (Q-24)."""
    if not interests:
        return _parse_choices("field", field, ContestField)
    if _split(field):
        raise BadRequestError("field 와 interests 는 함께 쓸 수 없습니다.")
    if user_id is None:
        raise TokenInvalidError("인증이 필요합니다.")
    return frozenset(f.value for f in users.interest_fields(user_id))


@router.get("", response_model=ContestPageOut)
def list_contests(
    service: Service,
    today: Today,
    user_id: OptionalUserId,
    users: Users,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    sort: SortOrder = SortOrder.DEADLINE,
    status: Annotated[str | None, Query(description="open,upcoming,closed 중 쉼표 구분")] = None,
    field: Annotated[str | None, Query(description="개발,AI,보안,디자인,아이디어")] = None,
    contest_format: Annotated[
        str | None, Query(alias="format", description="해커톤,경진대회,공모전,아이디어톤")
    ] = None,
    interests: Annotated[
        bool, Query(description="true 면 내 프로필 관심 분야만 (로그인 필요, field 와 함께 못 씀)")
    ] = False,
    q: Annotated[str | None, Query(max_length=100, description="제목 검색")] = None,
) -> ContestPageOut:
    """필터·정렬·페이지 조건으로 대회 목록을 돌려준다."""
    query = ContestQuery(
        statuses=_parse_statuses(status),
        keyword=q.strip() if q and q.strip() else None,
        tags=ContestTags(
            field=_field_filter(field, interests, user_id, users),
            format=_parse_choices("format", contest_format, ContestFormat),
        ),
        sort=sort,
        page=page,
        size=size,
    )
    return ContestPageOut.from_page(service.list_contests(query, today))


@router.get("/{contest_id}", response_model=ContestDetailOut)
def get_contest(
    contest_id: str, service: Service, bookmarks: Bookmarks, today: Today, user_id: OptionalUserId
) -> ContestDetailOut:
    """대회 상세와 로그인 회원의 저장 여부를 돌려준다. 보낸 토큰이 만료·위조면 401 (ADR-0004)."""
    view = service.get_contest(contest_id, today)
    return ContestDetailOut.from_detail(view, bookmarks.is_bookmarked(user_id, contest_id))


@router.post("/{contest_id}/clicks", response_model=ApplyLinkOut)
def click_apply_link(
    contest_id: str, user_id: OptionalUserId, service: Apply, today: Today
) -> ApplyLinkOut:
    """참가 바로가기 클릭을 기록하고 공식 참가 URL 을 돌려준다."""
    return ApplyLinkOut(apply_url=service.click(contest_id, user_id, today, now_kst()))


@router.put("/{contest_id}/bookmark", status_code=status.HTTP_204_NO_CONTENT)
def bookmark_contest(
    contest_id: str, user_id: CurrentUserId, service: Bookmarks, today: Today
) -> Response:
    """대회를 저장한다."""
    service.add(user_id, contest_id, today, now_kst())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{contest_id}/bookmark", status_code=status.HTTP_204_NO_CONTENT)
def unbookmark_contest(contest_id: str, user_id: CurrentUserId, service: Bookmarks) -> Response:
    """저장한 대회를 지운다."""
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
    """내가 저장한 대회 목록을 돌려준다."""
    return BookmarkPageOut.from_page(service.list_bookmarks(user_id, today, page, size))
