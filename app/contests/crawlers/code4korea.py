"""코드포코리아 크롤러. code4korea.org '개발자 행사 목록'의 공개 Google 시트를 CSV 로 받는다.

세미나·밋업까지 담은 행사 목록이라 이름·주제에 형식 단어가 있는 행사만 대회로 고른다 (Q-26).
행사 일시는 하나뿐이고 '행사 시작시간 또는 모집 마감시간'이라 접수 마감일로 간주한다.
주최 열이 없어 주최는 비워 둔다. 페이지에 박힌 Sheets API 키는 쓰지 않고 공개 내보내기 주소를 쓴다.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from typing import Any

import httpx

from app.contests.crawlers.base import RawContest, convert_each, looks_like_contest

NAME = "code4korea"
SHEET_ID = "1KMB44o1X3UrHKQarL-C6gE75-_jgobz8-ySizAX2s8U"
URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export"
COL_NAME = "행사 이름"
COL_DATE = "행사 일시"
COL_TOPIC = "행사 주제"
COL_HOMEPAGE = "홈페이지"
REQUIRED_COLUMNS = (COL_NAME, COL_DATE, COL_TOPIC, COL_HOMEPAGE)

# 예: "2026. 11. 9 오전 9:00:00", "2026. 11. 16 오후 11:59:00"
_DATE = re.compile(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})")


class Code4KoreaSource:
    """코드포코리아 개발자 행사 시트 수집기."""

    name = NAME

    def __init__(self, client: httpx.Client) -> None:
        """공유 HTTP 클라이언트를 받는다."""
        self._client = client

    def fetch(self) -> list[RawContest]:
        """시트를 CSV 로 받아 대회만 RawContest 로 바꾼다."""
        res = self._client.get(URL, params={"format": "csv"})
        res.raise_for_status()
        return parse_sheet(res.text)


def parse_sheet(text: str) -> list[RawContest]:
    """CSV 를 머리글 이름으로 읽는다. 필요한 열이 없으면 ValueError."""
    reader = csv.DictReader(io.StringIO(text))
    missing = [col for col in REQUIRED_COLUMNS if col not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"code4korea: 시트에 필요한 열이 없다: {missing}")
    # 로그에서 건너뛴 줄을 찾을 수 있게 머리글 다음 줄부터 2로 번호를 단다
    rows: list[dict[str, Any]] = [{"id": n, **row} for n, row in enumerate(reader, start=2)]
    return convert_each(rows, _to_contest, NAME)


def parse_date(text: str) -> date:
    """시트의 '2026. 11. 9 오전 9:00:00' 같은 일시에서 날짜만 읽는다. 형식이 다르면 ValueError."""
    match = _DATE.search(text)
    if match is None:
        raise ValueError(f"code4korea: 일시 형식이 다르다: {text!r}")
    year, month, day = (int(g) for g in match.groups())
    return date(year, month, day)


def _to_contest(row: dict[str, Any]) -> RawContest | None:
    """대회이고 홈페이지가 있는 줄만 RawContest 로 바꾼다 (Q-20)."""
    name, topic, homepage = row[COL_NAME].strip(), row[COL_TOPIC].strip(), row[COL_HOMEPAGE].strip()
    if not homepage or not looks_like_contest(name, topic):
        return None
    url = homepage if homepage.startswith(("http://", "https://")) else f"https://{homepage}"
    return RawContest(
        title=name,
        host="",
        field=topic,
        apply_start=None,
        apply_end=parse_date(row[COL_DATE]),
        event_start=None,
        event_end=None,
        apply_url=url,
        source=NAME,
        source_url=url,
    )
