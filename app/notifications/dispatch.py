"""알림 발급 실행 진입점 (F4-1): `uv run python -m app.notifications.dispatch`

저장한 대회의 오늘 알림을 서비스 안 알림 목록에 넣는다(Q-34). PostgreSQL(북마크·알림)과
MongoDB(대회 날짜)가 모두 필요하다. 하루 한 번, 최신 날짜를 쓰도록 대회 수집 뒤에 실행한다.
같은 날 다시 실행해도 같은 알림은 다시 만들지 않는다. 운영 스케줄 연결은 Q-36.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.contests.repository import ContestRepository
from app.core.db import close_client, get_database
from app.core.sql import dispose_engine, get_engine
from app.core.time import now_kst
from app.notifications.repository import NotificationRepository
from app.notifications.service import NotificationDispatcher


def run() -> int:
    """오늘 알림을 만들고 새로 만든 건수를 돌려준다."""
    now = now_kst()
    with Session(get_engine(), expire_on_commit=False) as session:
        dispatcher = NotificationDispatcher(
            NotificationRepository(session), ContestRepository(get_database())
        )
        return dispatcher.dispatch(now.date(), now)


def main() -> int:
    """알림 발급을 실행한다 (CLI 진입점). 실패하면 예외가 그대로 올라가 1 로 끝난다."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        run()
    finally:
        close_client()
        dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
