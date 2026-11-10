"""대회 도메인 규칙 (순수 파이썬). 상태·D-day는 저장하지 않고 날짜로부터 계산한다.

분야·형식 분류 (F3-1) 의 대응표·키워드는 코드가 아니라 `classification_rules.toml` 에 둔다.

프레임워크·DB·HTTP 를 import 하지 않는다 (AC-1).
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
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
    """접수 기간. 상태와 D-day 를 날짜로 계산한다."""

    start: date | None  # 없으면 이미 접수 중으로 본다 (Q-6)
    end: date

    def status(self, today: date) -> ContestStatus:
        """기준 날짜의 접수 상태를 계산한다."""
        if self.start is not None and today < self.start:
            return ContestStatus.UPCOMING
        if today > self.end:
            return ContestStatus.CLOSED
        return ContestStatus.OPEN

    def d_day(self, today: date) -> int:
        """접수 마감까지 남은 일수. 마감 당일 0, 지나면 음수."""
        return (self.end - today).days


class SortOrder(StrEnum):
    """목록 정렬 기준."""

    DEADLINE = "deadline"  # 마감 임박순
    LATEST = "latest"  # 최근 수집순


DEFAULT_STATUSES = frozenset({ContestStatus.OPEN, ContestStatus.UPCOMING})


class Dimension(StrEnum):
    """분류 태그 차원 (F3-1). 값은 API 쿼리 파라미터·저장 키로 그대로 쓴다.

    참가 자격(학력·참가 형태·지역)은 근거 원문을 주는 소스가 없어 분류하지 않는다 (Q-23).
    """

    FIELD = "field"
    FORMAT = "format"


class ContestField(StrEnum):
    """분야 값. 프로필 관심 분야(`app.auth.rules.InterestField`)와 같은 어휘다 (Q-5)."""

    DEV = "개발"
    AI = "AI"
    SECURITY = "보안"
    DESIGN = "디자인"
    IDEA = "아이디어"


class ContestFormat(StrEnum):
    """대회 형식 값."""

    HACKATHON = "해커톤"
    COMPETITION = "경진대회"
    CONTEST = "공모전"
    IDEATHON = "아이디어톤"


# 차원별 허용 값. 응답의 태그 순서도 이 정의 순서를 따른다.
VOCABULARY: dict[Dimension, tuple[str, ...]] = {
    Dimension.FIELD: tuple(ContestField),
    Dimension.FORMAT: tuple(ContestFormat),
}

RULES_PATH = Path(__file__).with_name("classification_rules.toml")


@dataclass(frozen=True)
class ContestTags:
    """차원별 태그 값. 대회에 붙은 태그이기도 하고 목록 조회의 태그 필터이기도 하다.

    한 차원에 값이 없으면 그 차원은 미분류(필터라면 조건 없음)다.
    """

    field: frozenset[str] = frozenset()
    format: frozenset[str] = frozenset()

    def get(self, dimension: Dimension) -> frozenset[str]:
        """차원 하나의 태그 집합을 돌려준다."""
        values: frozenset[str] = getattr(self, dimension.value)
        return values

    def ordered(self, dimension: Dimension) -> list[str]:
        """차원 하나의 태그를 어휘 순서대로 돌려준다."""
        values = self.get(dimension)
        return [v for v in VOCABULARY[dimension] if v in values]

    def dimensions(self) -> list[Dimension]:
        """값이 있는 차원."""
        return [d for d in Dimension if self.get(d)]


# 태그가 하나도 없음: 완전히 미분류인 대회, 또는 태그 필터 없음
NO_TAGS = ContestTags()


@dataclass(frozen=True)
class SourceDefaults:
    """소스 자체가 정해 주는 값. 분야는 항상 더하고, 형식은 제목에 형식 단어가 없을 때만 쓴다."""

    fields: frozenset[str] = frozenset()
    format: str | None = None


@dataclass(frozen=True)
class ClassificationRules:
    """분류 규칙 (F3-1, Q-23).

    분야는 소스가 붙인 태그를 대응표로 옮기고(추측 없음), 형식은 제목의 형식 단어로 정한다.
    """

    field_by_tag: Mapping[str, frozenset[str]]  # 정규화한 소스 태그 → 분야 값
    format_keywords: tuple[tuple[str, tuple[str, ...]], ...]  # (형식 값, 정규화한 키워드)
    sources: Mapping[str, SourceDefaults]


def _compact(text: str) -> str:
    """비교용: 공백을 지우고 소문자로 ('Machine Learning/AI' == 'machinelearning/ai')."""
    return "".join(text.split()).lower()


def _checked(dimension: Dimension, value: str) -> str:
    """어휘 밖 값이면 ValueError."""
    if value not in VOCABULARY[dimension]:
        raise ValueError(f"{dimension}: 어휘에 없는 값 {value!r}")
    return value


def _compact_all(where: str, values: list[str]) -> tuple[str, ...]:
    """정규화한 목록. 비었거나 빈 항목이 있으면 ValueError."""
    compacted = tuple(_compact(v) for v in values)
    if not compacted or not all(compacted):
        raise ValueError(f"{where}: 빈 태그·키워드")
    return compacted


def _parse_field_rules(raws: list[dict[str, Any]]) -> dict[str, frozenset[str]]:
    """분야 대응표를 '소스 태그 → 분야 값들' 로 뒤집는다."""
    by_tag: dict[str, set[str]] = {}
    for raw in raws:
        value = _checked(Dimension.FIELD, raw["value"])
        for tag in _compact_all(f"field={value}", raw["source_tags"]):
            by_tag.setdefault(tag, set()).add(value)
    return {tag: frozenset(values) for tag, values in by_tag.items()}


def _parse_source(raw: dict[str, Any]) -> SourceDefaults:
    """소스 기본값 한 개를 읽는다."""
    fmt = raw.get("format")
    return SourceDefaults(
        fields=frozenset(_checked(Dimension.FIELD, v) for v in raw.get("fields", [])),
        format=None if fmt is None else _checked(Dimension.FORMAT, fmt),
    )


def parse_rules(data: dict[str, Any]) -> ClassificationRules:
    """설정 파일 내용을 규칙으로 바꾼다. 어휘 밖 값이나 빈 태그·키워드는 ValueError."""
    format_keywords = tuple(
        (
            _checked(Dimension.FORMAT, raw["value"]),
            _compact_all(f"format={raw['value']}", raw["keywords"]),
        )
        for raw in data.get("format", [])
    )
    return ClassificationRules(
        field_by_tag=_parse_field_rules(data.get("field", [])),
        format_keywords=format_keywords,
        sources={name: _parse_source(raw) for name, raw in data.get("sources", {}).items()},
    )


def load_rules(path: Path = RULES_PATH) -> ClassificationRules:
    """TOML 파일에서 분류 규칙을 읽는다."""
    with path.open("rb") as f:
        return parse_rules(tomllib.load(f))


def split_source_tags(field: str) -> list[str]:
    """크롤러가 쉼표로 이어 붙인 소스 태그(`RawContest.field`)를 나눈다."""
    return [tag.strip() for tag in field.split(",") if tag.strip()]


def classify(
    title: str, source_tags: list[str], source: str, rules: ClassificationRules
) -> ContestTags:
    """대회 하나에 분야·형식 태그를 붙인다 (F3-1). 근거가 없는 차원은 미분류로 둔다."""
    defaults = rules.sources.get(source, SourceDefaults())
    fields = set(defaults.fields)
    for tag in source_tags:
        fields |= rules.field_by_tag.get(_compact(tag), frozenset())
    compact_title = _compact(title)
    formats = {
        value
        for value, keywords in rules.format_keywords
        if any(keyword in compact_title for keyword in keywords)
    }
    if not formats and defaults.format is not None:
        formats.add(defaults.format)
    return ContestTags(field=frozenset(fields), format=frozenset(formats))


def needs_tag_check(tags: ContestTags, requested: ContestTags) -> bool:
    """'분류 확인 필요' (F3-3).

    태그 필터가 있으면 필터한 차원 중 하나라도 미분류여서 조건 확인 없이 걸린 경우,
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
    keyword: str | None = None
    tags: ContestTags = NO_TAGS
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
