"""대회 수집 실행 진입점: `uv run python -m app.contests.collect`

수집한 대회는 중복 제거(F2-2)와 자격 분류(F3-1, 규칙은 `qualification_rules.toml`)를 거쳐
저장한다. 수집이 끝나면 마감 3일이 지난 대회를 지운다 (Q-3).
하루 1회 GitHub Actions(`.github/workflows/collect.yml`)가 이 명령을 실행한다 (Q-21).
"""

from __future__ import annotations

import logging

from app.contests.crawlers.base import ContestSource, new_client
from app.contests.crawlers.dacon import DaconSource
from app.contests.crawlers.dev_event import DevEventSource
from app.contests.crawlers.devpost import DevpostSource
from app.contests.repository import ContestRepository
from app.contests.rules import load_rules
from app.contests.service import CollectResult, collect, purge_closed
from app.core.db import close_client, get_database
from app.core.time import now_kst


def run() -> list[CollectResult]:
    """모든 소스에서 수집하고 오래된 마감 대회를 지운다."""
    repo = ContestRepository(get_database())
    repo.ensure_indexes()
    today = now_kst().date()
    rules = load_rules()
    with new_client() as client:
        sources: list[ContestSource] = [
            DevEventSource(client),
            DaconSource(client),
            DevpostSource(client),
        ]
        results = collect(sources, repo, today, rules)
    purge_closed(repo, today)
    return results


def main() -> int:
    """수집을 실행하고 실패한 소스가 있으면 1 을 돌려준다 (CLI 진입점)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        results = run()
    finally:
        close_client()
    return 1 if any(result.error for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
