"""분야·형식 분류 규칙 (F3-1) 과 '분류 확인 필요' 판정 (F3-3). 실제 규칙 파일로 분류한다."""

import pytest

from app.auth.rules import InterestField
from app.contests.crawlers import dacon, dev_event, devpost
from app.contests.rules import (
    ContestField,
    ContestTags,
    classify,
    load_rules,
    needs_tag_check,
    parse_rules,
    split_source_tags,
)

RULES = load_rules()


@pytest.mark.feature("F3-1")
def test_field_comes_from_source_tags_via_mapping() -> None:
    """소스가 붙인 태그를 대응표로 공통 분야에 옮긴다."""
    tags = classify("Some Hack", ["Machine Learning/AI", "Cybersecurity", "Web"], "x", RULES)

    assert tags.field == {"AI", "보안"}


@pytest.mark.feature("F3-1")
def test_source_tag_must_match_whole_ignoring_case_and_spacing() -> None:
    """태그는 통째로 같아야 하고 공백·대소문자는 무시한다. 부분 일치로는 붙이지 않는다."""
    assert classify("x", ["machine learning/ai"], "x", RULES).field == {"AI"}
    assert classify("x", ["분석아이디어", "분석 아이디어"], "x", RULES).field == {"아이디어"}
    # 'AI 헬스케어' 는 'AI' 태그가 아니다
    assert classify("x", ["AI 헬스케어", "기술일반"], "x", RULES).field == frozenset()


@pytest.mark.feature("F3-1")
def test_format_comes_from_title_words() -> None:
    """제목의 형식 단어로 형식을 정하고, 여러 개면 모두 붙인다."""
    assert classify("2026 청도 바이브코딩 해커톤", [], "dev_event", RULES).format == {"해커톤"}
    assert classify("TRILLION AGENT AI HACKATHON", [], "dev_event", RULES).format == {"해커톤"}
    assert classify("AI 혁신 아이디어 경진대회", [], "dev_event", RULES).format == {"경진대회"}
    assert classify("AI 에이전트 공모전", [], "dev_event", RULES).format == {"공모전"}
    assert classify("2026 아이디어톤", [], "dev_event", RULES).format == {"아이디어톤"}


@pytest.mark.feature("F3-1")
def test_source_defaults_fill_field_always_and_format_only_without_title_word() -> None:
    """소스 기본값: 분야는 항상 더하고, 형식은 제목에 형식 단어가 없을 때만 쓴다."""
    dacon_plain = classify("투구 예측 AI", ["아이디어"], "dacon", RULES)
    dacon_hackathon = classify("Aimers 9기 AI 해커톤", [], "dacon", RULES)
    devpost_plain = classify("Kiroween 2026", ["Open Ended"], "devpost", RULES)

    assert dacon_plain == ContestTags(
        field=frozenset({"AI", "아이디어"}), format=frozenset({"경진대회"})
    )
    assert dacon_hackathon.format == {"해커톤"}
    assert devpost_plain == ContestTags(field=frozenset({"개발"}), format=frozenset({"해커톤"}))


@pytest.mark.feature("F3-1")
def test_no_evidence_leaves_dimension_unclassified() -> None:
    """근거가 없으면 미분류로 두고 '챌린지' 같은 애매한 단어로는 형식을 붙이지 않는다."""
    tags = classify("LOTTE AX CHALLENGE 2026", ["기술일반"], "dev_event", RULES)

    assert tags == ContestTags()


@pytest.mark.feature("F3-1")
def test_rules_reject_values_outside_vocabulary() -> None:
    """어휘 밖 값·빈 태그·빈 키워드 규칙은 거절한다."""
    with pytest.raises(ValueError, match="어휘에 없는 값"):
        parse_rules({"field": [{"value": "게임", "source_tags": ["Gaming"]}]})
    with pytest.raises(ValueError, match="어휘에 없는 값"):
        parse_rules({"sources": {"x": {"format": "챌린지"}}})
    with pytest.raises(ValueError, match="빈 태그·키워드"):
        parse_rules({"format": [{"value": "해커톤", "keywords": [" "]}]})


@pytest.mark.feature("F3-1")
def test_rule_file_sources_are_real_crawler_names() -> None:
    """규칙 파일의 소스 기본값 키는 실제 크롤러 이름이어야 한다 (오타면 기본값이 조용히 빠진다)."""
    assert set(RULES.sources) <= {dacon.NAME, dev_event.NAME, devpost.NAME}


@pytest.mark.feature("F3-1")
def test_field_vocabulary_matches_profile_interests() -> None:
    """분야 어휘는 프로필 관심 분야와 같아야 프로필을 필터 기본값으로 쓸 수 있다 (F1-7)."""
    assert [f.value for f in ContestField] == [f.value for f in InterestField]


@pytest.mark.feature("F3-1")
def test_split_source_tags_drops_blanks() -> None:
    """쉼표로 이은 소스 태그를 나누고 빈 값을 버린다."""
    assert split_source_tags("AI,  블록체인 ,,") == ["AI", "블록체인"]
    assert split_source_tags("") == []


@pytest.mark.feature("F3-3")
def test_check_needed_when_a_filtered_dimension_is_unclassified() -> None:
    """필터한 차원이 미분류면 분류 확인이 필요하다."""
    tags = ContestTags(field=frozenset({"AI"}))
    by_field = ContestTags(field=frozenset({"AI"}))
    by_field_and_format = ContestTags(field=frozenset({"AI"}), format=frozenset({"해커톤"}))

    assert needs_tag_check(tags, by_field) is False
    assert needs_tag_check(tags, by_field_and_format) is True


@pytest.mark.feature("F3-3")
def test_without_filter_only_fully_unclassified_needs_check() -> None:
    """필터가 없으면 모든 차원이 미분류일 때만 확인이 필요하다."""
    no_filter = ContestTags()

    assert needs_tag_check(ContestTags(), no_filter) is True
    assert needs_tag_check(ContestTags(format=frozenset({"공모전"})), no_filter) is False
