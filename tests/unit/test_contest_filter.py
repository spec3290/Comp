"""목록 조건 → MongoDB 필터 변환 (F2-3). 상태는 저장하지 않고 오늘 날짜로 판정한다."""

from datetime import date, datetime, time

import pytest

from app.contests.repository import build_filter
from app.contests.rules import ContestQuery, ContestStatus, ContestTags
from app.core.time import KST

TODAY = date(2026, 10, 23)
MIDNIGHT = datetime.combine(TODAY, time.min, tzinfo=KST)
OPEN = {
    "$and": [
        {"$or": [{"apply_start": None}, {"apply_start": {"$lte": MIDNIGHT}}]},
        {"apply_end": {"$gte": MIDNIGHT}},
    ]
}
UPCOMING = {"apply_start": {"$gt": MIDNIGHT}}
CLOSED = {"apply_end": {"$lt": MIDNIGHT}}


@pytest.mark.feature("F2-3")
def test_default_query_excludes_closed() -> None:
    """기본 조건은 마감 대회를 뺀다."""
    mongo_filter = build_filter(ContestQuery(), TODAY)

    assert mongo_filter == {"$or": [OPEN, UPCOMING]}


@pytest.mark.feature("F2-3")
def test_values_or_within_and_across_params() -> None:
    """한 파라미터 안은 OR, 파라미터끼리는 AND 다."""
    query = ContestQuery(
        statuses=frozenset({ContestStatus.CLOSED, ContestStatus.UPCOMING}), keyword="해커톤"
    )

    assert build_filter(query, TODAY) == {
        "$and": [
            {"$or": [CLOSED, UPCOMING]},
            {"title": {"$regex": "해커톤", "$options": "i"}},
        ]
    }


@pytest.mark.feature("F3-3")
def test_tag_filter_keeps_unclassified() -> None:
    """분야·형식 필터는 요청 값 태그가 있거나 그 차원이 미분류인 대회를 고른다."""
    query = ContestQuery(
        statuses=frozenset(),
        tags=ContestTags(field=frozenset({"AI", "보안"}), format=frozenset({"해커톤"})),
    )

    assert build_filter(query, TODAY) == {
        "$and": [
            {
                "$or": [
                    {"tags.field": {"$in": ["AI", "보안"]}},
                    {"tags.field.0": {"$exists": False}},
                ]
            },
            {
                "$or": [
                    {"tags.format": {"$in": ["해커톤"]}},
                    {"tags.format.0": {"$exists": False}},
                ]
            },
        ]
    }


@pytest.mark.feature("F2-3")
def test_search_text_is_not_treated_as_regex() -> None:
    """검색어의 정규식 특수 문자는 그대로 이스케이프한다."""
    query = ContestQuery(statuses=frozenset(), keyword="C++ (.*)")

    assert build_filter(query, TODAY) == {"title": {"$regex": r"C\+\+\ \(\.\*\)", "$options": "i"}}
