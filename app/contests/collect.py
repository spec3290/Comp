"""대회 수집 실행 진입점: `uv run python -m app.contests.collect`

주기 실행(예: 하루 1회)은 이 명령을 스케줄러에 등록한다.
"""

from __future__ import annotations

import logging

from app.contests.crawlers.base import ContestSource, new_client
from app.contests.crawlers.dacon import DaconSource
from app.contests.crawlers.dev_event import DevEventSource
from app.contests.crawlers.linkareer import LinkareerSource
from app.contests.repository import ContestRepository
from app.contests.service import CollectResult, collect
from app.core.db import close_client, get_database


def run() -> list[CollectResult]:
    repo = ContestRepository(get_database())
    repo.ensure_indexes()
    with new_client() as client:
        sources: list[ContestSource] = [
            DevEventSource(client),
            DaconSource(client),
            LinkareerSource(client),
        ]
        return collect(sources, repo)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        results = run()
    finally:
        close_client()
    return 1 if any(result.error for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
