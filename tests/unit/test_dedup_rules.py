"""같은 대회 판정과 보여 줄 값 고르기 규칙 테스트 (F2-2, Q-2).

사례는 2026-11-12 실데이터에서 merge_key(제목+주최+마감일)로 병합되지 않던 중복 묶음이다.
"""

from dataclasses import replace
from datetime import date
from itertools import permutations

import pytest

from app.contests.rules import (
    ContestTags,
    Listing,
    belongs_to,
    fill_competition_deadline,
    refresh,
    resolve,
    same_contest,
    source_rank,
    titles_overlap,
    url_key,
)

END = date(2026, 11, 14)


def _listing(source: str, title: str, **changes: object) -> Listing:
    """소스·제목만 정하고 나머지는 기본값인 출처를 만든다."""
    base = Listing(
        source=source,
        source_url=f"https://{source}.example.com/{title}",
        title=title,
        host="",
        field="",
        apply_start=None,
        apply_end=END,
        event_start=None,
        event_end=None,
        apply_url=None,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


@pytest.mark.feature("F2-2")
def test_same_title_and_deadline_merge_even_if_host_differs() -> None:
    """주최는 사이트마다 달라 비교하지 않는다 (코드포코리아는 주최가 빈 문자열)."""
    dev_event = _listing(
        "dev_event",
        "2026 NASA International Space Apps Challenge Seoul",
        host="NASA Space Apps Seoul",
    )
    code4korea = _listing("code4korea", "2026 NASA International Space Apps Challenge Seoul")

    assert same_contest(dev_event, code4korea)


@pytest.mark.feature("F2-2")
def test_same_apply_url_with_contained_title_merges_despite_different_deadline() -> None:
    """공식 참가 URL 이 같고 한쪽 제목이 다른 쪽을 포함하면 마감일이 달라도 같은 대회다."""
    url = "https://event-us.kr/tug/event/139112"
    dev_event = _listing(
        "dev_event",
        "2026 AI 활용 전국 대학생 창업 해커톤",
        apply_url=url,
        apply_end=date(2026, 11, 11),
    )
    code4korea = _listing(
        "code4korea",
        "2026 AI 활용 전국 대학생 창업 해커톤 대회",
        apply_url=f"{url}/",
        apply_end=date(2026, 11, 19),
    )

    assert same_contest(dev_event, code4korea)


@pytest.mark.feature("F2-2")
def test_title_with_trailing_note_merges_on_same_deadline() -> None:
    """제목 뒤에 일정 메모가 붙어도 같은 마감일이면 같은 대회다."""
    a = _listing("dev_event", "Korea Local Marketing Hackathon 2026")
    b = _listing("code4korea", "Korea Local Marketing Hackathon 2026 (11/21 토 서울)")

    assert same_contest(a, b)


@pytest.mark.feature("F2-2")
def test_same_homepage_does_not_merge_different_rounds() -> None:
    """예선·본선처럼 홈페이지를 같이 쓰는 다른 대회는 제목이 서로를 포함하지 않아 합치지 않는다."""
    url = "https://example.com/contest"
    qualifier = _listing(
        "dev_event", "2026 코딩 챔피언십 예선", apply_url=url, apply_end=date(2026, 11, 1)
    )
    final = _listing(
        "code4korea", "2026 코딩 챔피언십 본선", apply_url=url, apply_end=date(2026, 12, 1)
    )

    assert not same_contest(qualifier, final)


@pytest.mark.feature("F2-2")
def test_different_url_and_deadline_stay_apart() -> None:
    """참가 URL 도 마감일도 다르면 제목이 겹쳐도 합치지 않는다 (잘못 합치면 대회가 사라진다)."""
    a = _listing(
        "event_us",
        "제5회 2026 AI 스마트워크톤 | 총상금 600만원",
        apply_url="https://a.kr",
        apply_end=date(2026, 11, 15),
    )
    b = _listing(
        "code4korea",
        "제5회 2026 AI 스마트워크톤",
        apply_url="https://b.kr",
        apply_end=date(2026, 11, 20),
    )

    assert not same_contest(a, b)


@pytest.mark.feature("F2-2")
def test_short_titles_must_match_exactly() -> None:
    """짧은 제목은 포함 관계로 합치지 않는다."""
    assert titles_overlap("해커톤", "해커톤")
    assert not titles_overlap("AI 해커톤", "2026 AI 해커톤 본선 진출자 발표")
    assert titles_overlap("[공모전] 2026 AI-해커톤!", "2026 ai 해커톤")


@pytest.mark.feature("F2-2")
def test_same_site_matches_only_its_own_source_url() -> None:
    """같은 사이트 안의 다른 원문은 제목·마감일이 같아도 다른 대회다."""
    a = _listing("dev_event", "2026 AI 해커톤", source_url="https://dev-event.example.com/1")
    other = replace(a, source_url="https://dev-event.example.com/2")

    assert same_contest(a, replace(a, title="제목이 바뀐 2026 AI 해커톤"))
    assert not same_contest(a, other)
    # 묶음에 같은 사이트의 다른 원문이 있으면 그 묶음에 들어가지 않는다
    assert not belongs_to(other, [a, _listing("code4korea", "2026 AI 해커톤")])


@pytest.mark.feature("F2-2")
def test_url_key_ignores_scheme_www_slash_and_fragment() -> None:
    """참가 URL 비교는 스킴·www·끝 슬래시·프래그먼트를 무시하고 쿼리는 구분한다."""
    assert url_key("https://www.Hackon.kr/e/002e1704/#top") == url_key(
        "http://hackon.kr/e/002e1704"
    )
    assert url_key("https://a.kr/event?id=1") != url_key("https://a.kr/event?id=2")
    assert url_key(None) is None
    assert url_key("") is None


@pytest.mark.feature("F2-2")
def test_resolve_takes_priority_source_values_and_fills_blanks() -> None:
    """제목·접수 기간은 우선순위 출처의 값, 빈 주최·링크·대회 기간은 다음 출처로 채운다."""
    dev_event = _listing(
        "dev_event",
        "2026 AI 해커톤",
        host="주최사",
        apply_end=date(2026, 11, 11),
        apply_url="https://apply.example.com",
        event_start=date(2026, 11, 20),
        event_end=date(2026, 11, 21),
    )
    event_us = _listing("event_us", "2026 AI 해커톤 대회", apply_end=date(2026, 11, 19))

    shown = resolve([dev_event, event_us])

    assert (shown.source, shown.title, shown.apply_end) == (
        "event_us",
        "2026 AI 해커톤 대회",
        date(2026, 11, 19),
    )
    assert (shown.host, shown.apply_url) == ("주최사", "https://apply.example.com")
    assert (shown.event_start, shown.event_end) == (date(2026, 11, 20), date(2026, 11, 21))


@pytest.mark.feature("F3-1")
def test_resolve_unions_tags_of_all_sources() -> None:
    """분류 태그는 모든 출처의 합집합이다."""
    a = _listing(
        "devpost", "X", tags=ContestTags(field=frozenset({"개발"}), format=frozenset({"해커톤"}))
    )
    b = _listing("dev_event", "X", tags=ContestTags(field=frozenset({"AI"})))

    assert resolve([a, b]).tags == ContestTags(
        field=frozenset({"개발", "AI"}), format=frozenset({"해커톤"})
    )


@pytest.mark.feature("F2-2")
def test_resolve_does_not_depend_on_collect_order() -> None:
    """출처가 들어온 순서와 상관없이 같은 값을 고른다."""
    bundle = [
        _listing("code4korea", "대회", apply_end=date(2026, 11, 1)),
        _listing("dev_event", "대회", host="주최사", apply_end=date(2026, 11, 2)),
        _listing("dacon", "대회", apply_end=date(2026, 11, 3)),
    ]

    assert len({resolve(list(order)) for order in permutations(bundle)}) == 1
    assert resolve(bundle).source == "dacon"


@pytest.mark.feature("F2-2")
def test_unknown_source_ranks_after_known_ones() -> None:
    """우선순위 목록에 없는 소스는 맨 뒤, 그 안에서는 이름순이다."""
    ranked = sorted(["b", "code4korea", "a", "dacon"], key=source_rank)

    assert ranked == ["dacon", "code4korea", "a", "b"]


@pytest.mark.feature("F2-1")
def test_refresh_keeps_found_apply_url_when_recollect_misses_it() -> None:
    """다시 수집할 때 링크를 못 찾으면 전에 찾은 링크를 지우지 않는다 (Q-30)."""
    old = _listing("event_us", "대회", apply_url="https://apply.example.com")
    missed = replace(old, title="바뀐 제목", apply_url=None)

    assert refresh(old, missed) == replace(missed, apply_url="https://apply.example.com")
    assert refresh(None, missed) == missed
    assert (
        refresh(old, replace(old, apply_url="https://new.example.com")).apply_url
        == "https://new.example.com"
    )


@pytest.mark.feature("F2-2")
def test_unknown_deadlines_are_not_the_same_deadline() -> None:
    """마감일을 모르는 출처끼리는 제목이 같아도 마감일로 합치지 않는다 (Q-35)."""
    a = _listing("dev_event", "AI 해커톤", apply_end=None)
    b = _listing("code4korea", "AI 해커톤", apply_end=None)
    assert not same_contest(a, b)
    # 참가 URL 이 같으면 마감일을 몰라도 같은 대회다
    url = "https://ai-hackathon.kr"
    assert same_contest(replace(a, apply_url=url), replace(b, apply_url=url))


@pytest.mark.feature("F2-2")
def test_apply_period_comes_from_first_source_that_found_a_deadline() -> None:
    """대표가 마감일을 못 찾았으면 마감일을 찾은 다음 출처의 접수 기간을 쓴다 (Q-35)."""
    top = _listing("event_us", "AI 해커톤", apply_end=None)
    other = _listing("dev_event", "AI 해커톤", apply_start=date(2026, 11, 1))
    for bundle in permutations([top, other]):
        shown = resolve(bundle)
        assert (shown.source, shown.title) == ("event_us", "AI 해커톤")
        assert (shown.apply_start, shown.apply_end) == (date(2026, 11, 1), END)


@pytest.mark.feature("F2-2")
def test_apply_period_stays_unknown_when_no_source_found_it() -> None:
    """아무 출처도 마감일을 못 찾았으면 미확인으로 둔다."""
    shown = resolve(
        [_listing("event_us", "A", apply_end=None), _listing("dev_event", "A", apply_end=None)]
    )
    assert shown.apply_end is None


@pytest.mark.feature("F2-1")
def test_competition_without_deadline_uses_event_end() -> None:
    """마감일을 못 찾은 경진대회는 대회 기간 중 참가할 수 있어 대회 마감일을 쓴다 (Q-35)."""
    competition = _listing(
        "event_us",
        "데이터 경진대회",
        apply_end=None,
        event_start=date(2026, 11, 1),
        event_end=date(2026, 11, 30),
        tags=ContestTags(format=frozenset({"경진대회"})),
    )
    filled = fill_competition_deadline(competition)
    assert (filled.apply_start, filled.apply_end) == (date(2026, 11, 1), date(2026, 11, 30))


@pytest.mark.feature("F2-1")
def test_only_competitions_without_deadline_are_filled() -> None:
    """다른 형식·마감일을 찾은 대회·대회 마감일이 없는 경진대회는 그대로 둔다."""
    competition = ContestTags(format=frozenset({"경진대회"}))
    unchanged = [
        _listing("event_us", "A 해커톤", apply_end=None, event_end=date(2026, 11, 30)),
        _listing("event_us", "B", event_end=date(2026, 11, 30), tags=competition),
        _listing("event_us", "C", apply_end=None, tags=competition),
    ]
    for listing in unchanged:
        assert fill_competition_deadline(listing) == listing
