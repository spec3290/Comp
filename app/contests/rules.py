"""대회 도메인 규칙 (순수 파이썬). 상태·D-day는 저장하지 않고 날짜로부터 계산한다.

자격 분류 (F3-1) 의 키워드는 코드가 아니라 `qualification_rules.toml` 에 둔다.

프레임워크·DB·HTTP 를 import 하지 않는다 (AC-1).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

_LEADING_BRACKET = re.compile(r"^\s*\[[^\]]*\]\s*")
_NON_WORD = re.compile(r"[\W_]+")

# 접수 마감 후 이 일수가 지나면 삭제한다 (Q-3)
RETENTION_DAYS_AFTER_CLOSE = 3


class ContestStatus(StrEnum):
    """API 에 그대로 쓰는 값. 화면 표기는 예정 / 접수 중 / 마감."""

    UPCOMING = "upcoming"
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True)
class ApplyPeriod:
    start: date | None  # 없으면 이미 접수 중으로 본다 (Q-6)
    end: date

    def status(self, today: date) -> ContestStatus:
        if self.start is not None and today < self.start:
            return ContestStatus.UPCOMING
        if today > self.end:
            return ContestStatus.CLOSED
        return ContestStatus.OPEN

    def d_day(self, today: date) -> int:
        """접수 마감까지 남은 일수. 마감 당일 0, 지나면 음수."""
        return (self.end - today).days


class SortOrder(StrEnum):
    DEADLINE = "deadline"  # 마감 임박순
    LATEST = "latest"  # 최근 수집순


DEFAULT_STATUSES = frozenset({ContestStatus.OPEN, ContestStatus.UPCOMING})


class Dimension(StrEnum):
    """자격 태그 차원 (F3-1). 값은 API 쿼리 파라미터·저장 키로 그대로 쓴다.

    분야는 키워드로 정확히 가르기 어려워 1차에서는 태그로 분류하지 않는다 (Q-19).
    """

    EDUCATION = "education"
    TEAM_TYPE = "team_type"
    REGION = "region"


class Education(StrEnum):
    HIGH_SCHOOL = "고등학생"
    UNIVERSITY = "대학생"
    ANY = "제한 없음"


class TeamType(StrEnum):
    SOLO = "개인"
    TEAM = "팀"


class Region(StrEnum):
    NATIONWIDE = "전국"
    SEOUL = "서울"
    BUSAN = "부산"
    DAEGU = "대구"
    INCHEON = "인천"
    GWANGJU = "광주"
    DAEJEON = "대전"
    ULSAN = "울산"
    SEJONG = "세종"
    GYEONGGI = "경기"
    GANGWON = "강원"
    CHUNGBUK = "충북"
    CHUNGNAM = "충남"
    JEONBUK = "전북"
    JEONNAM = "전남"
    GYEONGBUK = "경북"
    GYEONGNAM = "경남"
    JEJU = "제주"


# 차원별 허용 값. 응답의 태그 순서도 이 정의 순서를 따른다.
VOCABULARY: dict[Dimension, tuple[str, ...]] = {
    Dimension.EDUCATION: tuple(Education),
    Dimension.TEAM_TYPE: tuple(TeamType),
    Dimension.REGION: tuple(Region),
}

# 이 값이 붙은 대회는 같은 차원의 어떤 필터 값에도 걸린다 (누구나 참가 가능, 지역 무관)
WILDCARDS: dict[Dimension, str] = {
    Dimension.EDUCATION: Education.ANY,
    Dimension.REGION: Region.NATIONWIDE,
}

RULES_PATH = Path(__file__).with_name("qualification_rules.toml")


@dataclass(frozen=True)
class EligibilityTags:
    """차원별 태그 값. 대회에 붙은 태그이기도 하고 목록 조회의 자격 필터이기도 하다.

    한 차원에 값이 없으면 그 차원은 미분류(필터라면 조건 없음)다.
    """

    education: frozenset[str] = frozenset()
    team_type: frozenset[str] = frozenset()
    region: frozenset[str] = frozenset()

    def get(self, dimension: Dimension) -> frozenset[str]:
        values: frozenset[str] = getattr(self, dimension.value)
        return values

    def ordered(self, dimension: Dimension) -> list[str]:
        values = self.get(dimension)
        return [v for v in VOCABULARY[dimension] if v in values]

    def dimensions(self) -> list[Dimension]:
        """값이 있는 차원."""
        return [d for d in Dimension if self.get(d)]


# 태그가 하나도 없음: 완전히 미분류인 대회, 또는 자격 필터 없음
NO_TAGS = EligibilityTags()


@dataclass(frozen=True)
class KeywordRule:
    dimension: Dimension
    value: str
    keywords: tuple[str, ...]


def _compact(text: str) -> str:
    """키워드 비교용: 공백을 지우고 소문자로 ('제한 없음' == '제한없음')."""
    return "".join(text.split()).lower()


def parse_rules(data: dict[str, Any]) -> tuple[KeywordRule, ...]:
    """설정 파일 내용을 규칙으로 바꾼다. 어휘 밖의 차원·값이나 빈 키워드는 ValueError."""
    rules: list[KeywordRule] = []
    for raw in data.get("rules", []):
        dimension = Dimension(raw["dimension"])
        value = raw["value"]
        if value not in VOCABULARY[dimension]:
            raise ValueError(f"{dimension}: 어휘에 없는 값 {value!r}")
        keywords = tuple(_compact(k) for k in raw["keywords"])
        if not keywords or not all(keywords):
            raise ValueError(f"{dimension}={value}: 빈 키워드")
        rules.append(KeywordRule(dimension=dimension, value=value, keywords=keywords))
    return tuple(rules)


def load_rules(path: Path = RULES_PATH) -> tuple[KeywordRule, ...]:
    with path.open("rb") as f:
        return parse_rules(tomllib.load(f))


def classify(text: str, rules: tuple[KeywordRule, ...]) -> EligibilityTags:
    """키워드가 하나라도 들어 있으면 그 규칙의 태그를 붙인다 (F3-1). 없으면 그 차원은 미분류."""
    compact = _compact(text)
    found: dict[Dimension, set[str]] = {d: set() for d in Dimension}
    for rule in rules:
        if any(keyword in compact for keyword in rule.keywords):
            found[rule.dimension].add(rule.value)
    return EligibilityTags(
        education=frozenset(found[Dimension.EDUCATION]),
        team_type=frozenset(found[Dimension.TEAM_TYPE]),
        region=frozenset(found[Dimension.REGION]),
    )


def accepted_values(dimension: Dimension, requested: frozenset[str]) -> frozenset[str]:
    """필터 값으로 걸리는 태그 값: 요청 값 + 그 차원의 와일드카드."""
    wildcard = WILDCARDS.get(dimension)
    return requested if wildcard is None else requested | {wildcard}


def needs_eligibility_check(tags: EligibilityTags, requested: EligibilityTags) -> bool:
    """'자격 확인 필요' (F3-3).

    자격 필터가 있으면 필터한 차원 중 하나라도 미분류여서 조건 확인 없이 걸린 경우,
    필터가 없으면 어느 차원에도 태그가 없는(완전히 미분류인) 경우다.
    """
    dimensions = requested.dimensions()
    if not dimensions:
        return not tags.dimensions()
    return any(not tags.get(d) for d in dimensions)


@dataclass(frozen=True)
class ContestQuery:
    """목록 조회 조건 (F2-3, F3-3). 같은 항목 안의 값은 OR, 항목 사이는 AND (Q-9)."""

    statuses: frozenset[ContestStatus] = DEFAULT_STATUSES
    fields: tuple[str, ...] = ()
    keyword: str | None = None
    eligibility: EligibilityTags = NO_TAGS
    sort: SortOrder = SortOrder.DEADLINE
    page: int = 1
    size: int = 20


def normalize_text(text: str) -> str:
    """중복 비교용 정규화: 맨 앞 [접두어] 제거, 공백·특수문자 제거, 소문자 (Q-2 기본값)."""
    return _NON_WORD.sub("", _LEADING_BRACKET.sub("", text)).lower()


def merge_key(title: str, host: str, apply_end: date) -> str:
    """F2-2 병합 기준: 제목 + 주최 + 마감일."""
    return f"{normalize_text(title)}|{normalize_text(host)}|{apply_end.isoformat()}"


def purge_cutoff(today: date) -> date:
    """접수 마감일이 이 날짜보다 이르면 삭제 대상이다. 마감 뒤 3일은 '마감'으로 남는다 (Q-3)."""
    return today - timedelta(days=RETENTION_DAYS_AFTER_CLOSE)
