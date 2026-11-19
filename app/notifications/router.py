"""알림 API (F4-1): 내 알림 목록·읽음 처리, 알림 설정. 모두 Access 토큰이 필요하다.

알림은 서비스 안 알림 목록으로만 보낸다 (Q-34). 알림을 만드는 일은 API 가 아니라 하루 한 번
실행하는 `dispatch.py` 가 한다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.auth.dependencies import CurrentUserId
from app.core.time import now_kst
from app.notifications.schemas import NotificationPageOut, NotificationSettingsBody
from app.notifications.service import NotificationService, get_notification_service

router = APIRouter(prefix="/users/me", tags=["notifications"])

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

Service = Annotated[NotificationService, Depends(get_notification_service)]


@router.get("/notifications", response_model=NotificationPageOut)
def list_my_notifications(
    user_id: CurrentUserId,
    service: Service,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> NotificationPageOut:
    """내 알림 목록을 최근 보낸순으로 돌려준다."""
    return NotificationPageOut.from_page(service.list_notifications(user_id, page, size))


@router.post("/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
def read_all_notifications(user_id: CurrentUserId, service: Service) -> Response:
    """안 읽은 알림을 모두 읽음으로 표시한다."""
    service.mark_all_read(user_id, now_kst())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def read_notification(notification_id: int, user_id: CurrentUserId, service: Service) -> Response:
    """알림 하나를 읽음으로 표시한다 (멱등). 내 알림이 아니면 404."""
    service.mark_read(user_id, notification_id, now_kst())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/notification-settings", response_model=NotificationSettingsBody)
def get_notification_settings(user_id: CurrentUserId, service: Service) -> NotificationSettingsBody:
    """알림 종류별 켜기·끄기 설정을 돌려준다."""
    return NotificationSettingsBody.from_enabled(service.enabled_kinds(user_id))


@router.put("/notification-settings", response_model=NotificationSettingsBody)
def update_notification_settings(
    body: NotificationSettingsBody, user_id: CurrentUserId, service: Service
) -> NotificationSettingsBody:
    """알림 설정을 통째로 바꾸고 저장한 값을 돌려준다."""
    return NotificationSettingsBody.from_enabled(
        service.update_enabled_kinds(user_id, body.enabled())
    )
