"""이벤터스 크롤러. 검색 페이지에 박힌 검색 결과(`searchDataRaw`)에서 IT 대회만 고른다 (Q-25).

서버가 결과를 페이지에 실어 주는 것은 검색어만 준 첫 페이지(12건)뿐이다. 유형·분야 필터나
다음 페이지는 브라우저가 따로 검색 API 를 부르는데, 그 API 는 직접 호출하면 401 이라 쓰지 않는다.
그래서 대회성 검색어 여러 개로 첫 페이지씩 모으고 유형('대회/공모전')·분야('IT/프로그래밍')는
여기서 거른다. 시각은 UTC(`Z`)라 KST 날짜로 옮긴다. 공식 참가 URL 을 못 찾은 행사도
링크 없이 수집한다 (Q-29).
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from datetime import date, datetime
from typing import Any

import httpx

from app.contests.crawlers.base import RawContest, convert_each, looks_like_contest
from app.core.time import KST

logger = logging.getLogger(__name__)

NAME = "event_us"
SEARCH_URL = "https://event-us.kr/search"
EMPTY_RESULT_PATH = "/search/emptyresult"
EVENT_URL = "https://event-us.kr/{subdomain}/event/{event_id}"
CONTEST_TYPE = "대회/공모전"
IT_CATEGORY = "IT/프로그래밍"
QUERIES = (
    "해커톤",
    "아이디어톤",
    "경진대회",
    "공모전",
    "대회",
    "챌린지",
    "AI 공모전",
    "SW 공모전",
    "개발 공모전",
    "데이터 공모전",
    "코딩 대회",
    "프로그래밍 대회",
)
# 이벤터스 안에서 신청을 받으면 행사 페이지가 곧 공식 참가 페이지다
RECEPTION_EVENTUS = "eventus"
RECEPTION_EXTERNAL = "external"

_SEARCH_DATA = re.compile(r"^\s*searchDataRaw:\s*(.+?),\s*$", re.M)
_EXTERNAL_APPLY = re.compile(
    r'<ev-button\b[^>]*?\bhref="(https?://[^"]+)"[^>]*>(?:(?!</ev-button>).)*?신청하기\(외부\)',
    re.S,
)


class EventUsSource:
    """이벤터스 검색 페이지 수집기."""

    name = NAME

    def __init__(self, client: httpx.Client, delay_seconds: float = 1.0) -> None:
        """공유 HTTP 클라이언트와 요청 간 지연을 받는다."""
        self._client = client
        self._delay = delay_seconds

    def fetch(self) -> list[RawContest]:
        """검색어별 결과를 합쳐 IT 대회만 RawContest 로 바꾼다."""
        return convert_each(self._search_all(), self._to_contest, NAME)

    def _search_all(self) -> list[dict[str, Any]]:
        """모든 검색어의 첫 페이지를 가져와 행사 id 로 중복을 없앤다."""
        found: dict[str, dict[str, Any]] = {}
        for query in QUERIES:
            res = self._client.get(SEARCH_URL, params={"query": query})
            res.raise_for_status()
            time.sleep(self._delay)
            # 결과가 없으면 검색 결과 대신 빈 결과 안내 페이지로 리다이렉트된다
            if res.url.path == EMPTY_RESULT_PATH:
                continue
            for item in parse_search_page(res.text):
                found.setdefault(str(item.get("id")), item)
        return list(found.values())

    def _to_contest(self, item: dict[str, Any]) -> RawContest | None:
        """IT 대회 한 건을 RawContest 로 바꾸고, 대회가 아니면 None."""
        # 필터도 convert_each 의 항목별 오류 격리 안에서 돌려 깨진 항목 하나가 소스를 막지 않게 한다
        if not _is_it_contest(item):
            return None
        page_url = EVENT_URL.format(subdomain=item["subdomain"], event_id=item["id"])
        apply_url = self._apply_url(item["reception_type"], page_url)
        start = item.get("register_start_date")
        # 접수 마감이 없으면 행사 시작일을 접수 마감으로 본다 (Q-14와 같은 방식)
        due = item.get("register_due_date") or item["start_date"]
        return RawContest(
            title=item["title"].strip(),
            host=item["app_title"].strip(),
            field=", ".join(_field_tags(item)),
            apply_start=_kst_date(start) if start else None,
            apply_end=_kst_date(due),
            event_start=_kst_date(item["start_date"]),
            event_end=_kst_date(item["close_date"]),
            apply_url=apply_url,
            source=NAME,
            source_url=page_url,
        )

    def _apply_url(self, reception: str, page_url: str) -> str | None:
        """접수 방식에 맞는 공식 참가 URL. 외부 접수면 상세 페이지의 신청 링크를 읽는다.

        못 찾으면 None 이다 (Q-29). 상세 페이지 오류도 None 이지만, 저장소가 링크 없는 값으로
        이미 저장된 링크를 지우지 않으므로 기존 데이터는 그대로다 (F2-1).
        """
        if reception == RECEPTION_EVENTUS:
            return page_url
        if reception != RECEPTION_EXTERNAL:
            return None
        try:
            res = self._client.get(page_url)
            res.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("%s: 상세 페이지를 못 읽어 링크 없이 수집 (%s, %s)", NAME, page_url, exc)
            return None
        finally:
            time.sleep(self._delay)
        url = parse_external_apply_url(res.text)
        if url is None:
            logger.info("%s: 외부 신청 링크가 없어 링크 없이 수집 (%s)", NAME, page_url)
        return url


def parse_search_page(page: str) -> list[dict[str, Any]]:
    """검색 페이지의 searchDataRaw 를 {필드: 값} 목록으로 편다. 결과가 null 이면 빈 목록."""
    match = _SEARCH_DATA.search(page)
    if match is None:
        raise ValueError("event_us: searchDataRaw 를 찾지 못했다")
    data = json.loads(match.group(1))
    if data is None:
        return []
    return [
        {key: value.get("raw") if isinstance(value, dict) else value for key, value in r.items()}
        for r in data["results"]
    ]


def parse_external_apply_url(page: str) -> str | None:
    """상세 페이지에서 '신청하기(외부)' 버튼의 링크를 찾는다. 없으면 None."""
    match = _EXTERNAL_APPLY.search(page)
    return html.unescape(match.group(1)) if match else None


def _is_it_contest(item: dict[str, Any]) -> bool:
    """유형이 대회/공모전, 분야가 IT/프로그래밍이고 제목·태그에 대회 형식 단어가 있는 행사 (Q-25).

    유형은 주최자가 고르는 값이라 교육 과정 광고에도 붙어 있어 형식 단어를 한 번 더 본다.
    """
    if item.get("event_type") != CONTEST_TYPE or item.get("category") != IT_CATEGORY:
        return False
    return looks_like_contest(item.get("title") or "", *(item.get("tags") or []))


def _field_tags(item: dict[str, Any]) -> list[str]:
    """세부 분야(category2)와 태그를 순서대로 중복 없이 모은다. 분야 분류의 소스 태그가 된다."""
    tags = [item.get("category2"), *(item.get("tags") or [])]
    return list(dict.fromkeys(tag.strip() for tag in tags if tag and tag.strip()))


def _kst_date(text: str) -> date:
    """UTC ISO 8601 시각을 KST 날짜로 바꾼다."""
    return datetime.fromisoformat(text).astimezone(KST).date()
