"""대회 수집 (F2-1) + 중복 제거 (F2-2).

소스마다 독립적으로 수집한다. 한 소스가 실패해도 기존 데이터는 그대로 두고 로그만 남기며,
다른 소스는 계속 진행한다.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.contests.crawlers.base import ContestSource
from app.contests.repository import ContestRepository, SaveOutcome
from app.contests.rules import merge_key
from app.core.time import now_kst

logger = logging.getLogger(__name__)


@dataclass
class CollectResult:
    source: str
    fetched: int = 0
    outcomes: Counter[SaveOutcome] = field(default_factory=Counter)
    error: str | None = None


def collect(sources: Iterable[ContestSource], repo: ContestRepository) -> list[CollectResult]:
    return [_collect_one(source, repo) for source in sources]


def _collect_one(source: ContestSource, repo: ContestRepository) -> CollectResult:
    result = CollectResult(source=source.name)
    try:
        contests = source.fetch()
        result.fetched = len(contests)
        for contest in contests:
            key = merge_key(contest.title, contest.host, contest.apply_end)
            result.outcomes[repo.save(contest, key, now_kst())] += 1
    except Exception as exc:
        logger.exception("수집 실패: %s", source.name)
        result.error = f"{type(exc).__name__}: {exc}"
        return result
    counts = {outcome.value: n for outcome, n in result.outcomes.items()}
    logger.info("수집 완료: %s fetched=%d %s", source.name, result.fetched, counts)
    return result
