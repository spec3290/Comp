"""자격 분류 규칙 (F3-1) 과 '자격 확인 필요' 판정 (F3-3). 실제 규칙 파일로 분류한다."""

import pytest

from app.contests.rules import (
    Dimension,
    EligibilityTags,
    accepted_values,
    classify,
    load_rules,
    needs_eligibility_check,
    parse_rules,
)

RULES = load_rules()


@pytest.mark.feature("F3-1")
def test_eligibility_text_is_tagged_by_keyword_rules() -> None:
    """참가 자격 원문에 키워드 규칙으로 태그를 붙인다."""
    tags = classify("참가 대상: 전국 대학생 및 대학원생 (3인 1팀 구성)", RULES)

    assert tags == EligibilityTags(
        education=frozenset({"대학생"}),
        team_type=frozenset({"팀"}),
        region=frozenset({"전국"}),
    )


@pytest.mark.feature("F3-1")
def test_keywords_ignore_spacing_and_case() -> None:
    """키워드는 띄어쓰기·대소문자를 무시한다."""
    assert classify("참가자격 제한없음", RULES).education == {"제한 없음"}
    assert classify("학력: 제한 없음", RULES).education == {"제한 없음"}
    assert classify("서울시민 누구나", RULES).region == {"서울"}


@pytest.mark.feature("F3-1")
def test_one_text_can_carry_several_values() -> None:
    """한 원문에 여러 값이 붙을 수 있다."""
    tags = classify("고등학생, 대학생 모두 개인 또는 팀으로 참가 가능", RULES)

    assert tags.education == {"고등학생", "대학생"}
    assert tags.team_type == {"개인", "팀"}


@pytest.mark.feature("F3-1")
def test_no_match_leaves_dimension_unclassified() -> None:
    """매칭이 없으면 미분류로 두고 비슷한 단어에 속지 않는다."""
    assert classify("", RULES) == EligibilityTags()
    # '개인정보', '운영팀', 개최 장소의 지역명, 인원 제한은 자격이 아니다
    tags = classify(
        "개인정보 수집 동의. 문의: 운영팀. 장소: 서울 코엑스. 참가 인원 제한 없음", RULES
    )
    assert tags == EligibilityTags()


@pytest.mark.feature("F3-1")
def test_rules_reject_values_outside_vocabulary() -> None:
    """어휘 밖 값·빈 키워드 규칙은 거절한다."""
    with pytest.raises(ValueError, match="어휘에 없는 값"):
        parse_rules({"rules": [{"dimension": "education", "value": "초등학생", "keywords": ["x"]}]})
    with pytest.raises(ValueError, match="빈 키워드"):
        parse_rules({"rules": [{"dimension": "team_type", "value": "팀", "keywords": [" "]}]})
    with pytest.raises(ValueError):
        parse_rules({"rules": [{"dimension": "field", "value": "AI", "keywords": ["AI"]}]})


@pytest.mark.feature("F3-3")
def test_wildcard_values_match_any_filter_in_their_dimension() -> None:
    """제한 없음·전국은 같은 차원의 어떤 필터에도 걸린다."""
    assert accepted_values(Dimension.EDUCATION, frozenset({"대학생"})) == {"대학생", "제한 없음"}
    assert accepted_values(Dimension.REGION, frozenset({"부산"})) == {"부산", "전국"}
    assert accepted_values(Dimension.TEAM_TYPE, frozenset({"팀"})) == {"팀"}


@pytest.mark.feature("F3-3")
def test_check_needed_when_a_filtered_dimension_is_unclassified() -> None:
    """필터한 차원이 미분류면 자격 확인이 필요하다."""
    tags = EligibilityTags(education=frozenset({"대학생"}))
    by_education = EligibilityTags(education=frozenset({"대학생"}))
    by_education_and_region = EligibilityTags(
        education=frozenset({"대학생"}), region=frozenset({"서울"})
    )

    assert needs_eligibility_check(tags, by_education) is False
    assert needs_eligibility_check(tags, by_education_and_region) is True


@pytest.mark.feature("F3-3")
def test_without_filter_only_fully_unclassified_needs_check() -> None:
    """필터가 없으면 모든 차원이 미분류일 때만 확인이 필요하다."""
    no_filter = EligibilityTags()

    assert needs_eligibility_check(EligibilityTags(), no_filter) is True
    assert needs_eligibility_check(EligibilityTags(region=frozenset({"전국"})), no_filter) is False
