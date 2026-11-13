"""크롤러 공통 계약 (F2-1).

사이트별 크롤러는 이 Protocol 을 구현한다. 정규화·분류는 service/rules 몫.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "ContestCollector/0.1 (+https://github.com/spec3290/Comp)"
_CONTEST_WORDS = re.compile(r"해커톤|hackathon|아이디어톤|ideathon|공모전|경진대회|(?<!학술)대회")


@dataclass(frozen=True)
class RawContest:
    """크롤러가 돌려주는 원시 대회."""

    title: str
    host: str
    field: str
    apply_start: date | None
    apply_end: date | None  # 접수 마감일. 소스에서 못 찾으면 None 이고 대체 값을 넣지 않는다 (Q-35)
    event_start: date | None
    event_end: date | None
    apply_url: str | None  # 공식 참가 URL. 못 찾으면 None 이고 대회는 그대로 수집한다 (Q-29)
    source: str
    source_url: str


class ContestSource(Protocol):
    """대회 수집 소스 경계. 사이트마다 구현체를 둔다."""

    name: str

    def fetch(self) -> list[RawContest]:
        """대회 목록을 가져온다. 실패 시 예외를 던지고, 호출자가 기존 데이터를 유지한다."""
        ...


def looks_like_contest(*texts: str) -> bool:
    """글에 대회 형식 단어가 있는지 본다. 세미나·교육이 섞인 행사 목록에서 대회만 고를 때 쓴다.

    분류 규칙(classification_rules.toml)의 형식 단어와 같은 축이다. '챌린지'는 형식이 제각각이고
    '학술대회'는 학회라 넣지 않는다. 공백·대소문자는 무시한다.
    """
    return bool(_CONTEST_WORDS.search(re.sub(r"\s+", "", " ".join(texts)).lower()))


def new_client() -> httpx.Client:
    """수집용 공유 HTTP 클라이언트를 만든다."""
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)


def convert_each(
    items: Iterable[dict[str, Any]],
    convert: Callable[[dict[str, Any]], RawContest | None],
    source: str,
) -> list[RawContest]:
    """항목별로 변환한다. 형식이 깨진 항목은 건너뛰고 나머지는 살린다. None 은 대회가 아님."""
    contests: list[RawContest] = []
    for item in items:
        try:
            contest = convert(item)
        except (AttributeError, KeyError, TypeError, ValueError):
            logger.warning("%s: 형식이 다른 항목을 건너뜀 (id=%s)", source, item.get("id"))
            continue
        if contest is not None:
            contests.append(contest)
    return contests
