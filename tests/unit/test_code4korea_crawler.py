"""코드포코리아 수집기 파싱·HTTP 동작 테스트 (F2-1). 실제 네트워크를 호출하지 않는다."""

import csv
import io
from datetime import date

import httpx
import pytest

from app.contests.crawlers.base import looks_like_contest
from app.contests.crawlers.code4korea import Code4KoreaSource

HEADER = ["타임스탬프", "행사 이름", "행사 일시", "행사 주제", "참가비", "장소", "홈페이지", "1열"]


def _row(name: str, when: str, topic: str, homepage: str) -> list[str]:
    """시트 한 줄."""
    return ["2026. 11. 08 오후 11:15:48", name, when, topic, "무료", "강남구", homepage, ""]


def _sheet(rows: list[list[str]]) -> str:
    """머리글을 붙인 시트 CSV."""
    buffer = io.StringIO()
    csv.writer(buffer).writerows([HEADER, *rows])
    return buffer.getvalue()


def _source(text: str, status: int = 200) -> Code4KoreaSource:
    """CSV 를 돌려주는 가짜 클라이언트를 쓰는 수집기."""
    transport = httpx.MockTransport(lambda _: httpx.Response(status, text=text))
    return Code4KoreaSource(httpx.Client(transport=transport))


@pytest.mark.feature("F2-1")
def test_code4korea_keeps_contests_and_uses_event_date_as_deadline() -> None:
    """대회성 행사만 남기고, 하나뿐인 행사 일시를 접수 마감일로 쓴다 (Q-26)."""
    text = _sheet(
        [
            _row(
                " 2026 K-조선 해커톤 ",
                "2026. 11. 9 오전 9:00:00",
                "조선, AI, 해커톤",
                "https://a.kr/1",
            ),
            _row(
                "Seoul dbt Meetup #11", "2026. 12. 09 오후 7:00:00", "dbt, 데이터", "https://b.kr"
            ),
            _row(
                "구름톤 아레나", "2026. 11. 21 오후 2:00:00", "알고리즘, 프로그래밍 대회", "c.kr/x"
            ),
            _row("홈페이지 없는 해커톤", "2026. 11. 21 오후 2:00:00", "해커톤", ""),
            _row("날짜가 깨진 해커톤", "미정", "해커톤", "https://d.kr"),
        ]
    )

    contests = _source(text).fetch()

    assert [c.title for c in contests] == ["2026 K-조선 해커톤", "구름톤 아레나"]
    contest = contests[0]
    assert contest.field == "조선, AI, 해커톤"
    assert contest.apply_start is None
    assert contest.apply_end == date(2026, 11, 9)
    assert (contest.event_start, contest.event_end) == (None, None)
    assert contest.apply_url == "https://a.kr/1"
    # 시트처럼 scheme 이 빠진 주소에는 https 를 붙인다
    assert contests[1].apply_url == "https://c.kr/x"


@pytest.mark.feature("F2-1")
@pytest.mark.parametrize(
    ("name", "topic", "expected"),
    [
        ("NASA Space Apps Challenge Seoul", "해커톤, 우주 데이터", True),
        ("Cursor Bot Hackathon Seoul #4", "Cursor, AI 봇", True),
        ("2026 AI 서비스 기획 공모전", "AI", True),
        ("SW 경진대회", "", True),
        ("YUMITHON 아이디어톤", "MCP", True),
        ("SKT AX Challenge", "AI 챌린지", False),
        ("한국구매조달학회 추계학술대회", "학술", False),
        ("PyLadies Asia Conference 2026", "Python, 커뮤니티", False),
    ],
)
def test_contest_words_pick_contests_only(name: str, topic: str, expected: bool) -> None:
    """이름·주제에 형식 단어가 있어야 대회로 본다. '챌린지'·'학술대회'는 넣지 않는다."""
    assert looks_like_contest(name, topic) is expected


@pytest.mark.feature("F2-1")
def test_code4korea_missing_column_raises() -> None:
    """시트 머리글이 바뀌어 필요한 열이 없으면 실패한다."""
    with pytest.raises(ValueError, match="열"):
        _source("이름,날짜\n해커톤,2026. 11. 9\n").fetch()


@pytest.mark.feature("F2-1")
def test_code4korea_http_error_raises() -> None:
    """HTTP 오류는 예외로 올린다."""
    with pytest.raises(httpx.HTTPStatusError):
        _source("", status=500).fetch()


@pytest.mark.feature("F2-1")
def test_code4korea_contests_sharing_homepage_get_distinct_source_urls() -> None:
    """홈페이지가 같은 두 대회도 원문 식별자가 달라 서로 덮어쓰지 않는다."""
    text = _sheet(
        [
            _row(
                "블록체인 해커톤 (예선)", "2026. 11. 1 오전 9:00:00", "해커톤", "https://hack.kr/"
            ),
            _row(
                "블록체인 해커톤 (본선)", "2026. 11. 6 오전 9:00:00", "해커톤", "https://hack.kr/"
            ),
        ]
    )

    contests = _source(text).fetch()

    assert [c.apply_url for c in contests] == ["https://hack.kr/", "https://hack.kr/"]
    assert len({c.source_url for c in contests}) == 2
    assert all(c.source_url.startswith("https://code4korea.org/#") for c in contests)
