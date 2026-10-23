"""목록 조건 → MongoDB 필터 변환 (F2-3). 상태는 저장하지 않고 오늘 날짜로 판정한다."""

from datetime import date, datetime, time

import pytest

from app.contests.repository import build_filter
from app.contests.rules import ContestQuery, ContestStatus
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
    mongo_filter = build_filter(ContestQuery(), TODAY)

    assert mongo_filter == {"$or": [OPEN, UPCOMING]}


@pytest.mark.feature("F2-3")
def test_values_or_within_and_across_params() -> None:
    query = ContestQuery(
        statuses=frozenset({ContestStatus.CLOSED}), fields=("AI", "보안"), keyword="해커톤"
    )

    assert build_filter(query, TODAY) == {
        "$and": [
            {"$or": [CLOSED]},
            {
                "$or": [
                    {"field": {"$regex": "AI", "$options": "i"}},
                    {"field": {"$regex": "보안", "$options": "i"}},
                ]
            },
            {"title": {"$regex": "해커톤", "$options": "i"}},
        ]
    }


@pytest.mark.feature("F2-3")
def test_search_text_is_not_treated_as_regex() -> None:
    query = ContestQuery(statuses=frozenset(), keyword="C++ (.*)")

    assert build_filter(query, TODAY) == {"title": {"$regex": r"C\+\+\ \(\.\*\)", "$options": "i"}}
