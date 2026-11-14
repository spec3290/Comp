"""대회 도메인 규칙 (순수 파이썬). 상태·D-day는 저장하지 않고 날짜로부터 계산한다.

분야·형식 분류 (F3-1) 의 대응표·키워드는 코드가 아니라 `classification_rules.toml` 에 둔다.

프레임워크·DB·HTTP 를 import 하지 않는다 (AC-1).
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_LEADING_BRACKET = re.compile(r"^\s*\[[^\]]*\]\s*")
_NON_WORD = re.compile(r"[\W_]+")

# 접수 마감 후 이 일수가 지나면 삭제한다 (Q-3)
RETENTION_DAYS_AFTER_CLOSE = 3
# 날짜가 하나도 없는 대회의 참가 마지막 날. 마감되지 않고 마감 임박순에서 맨 뒤에 온다 (Q-35)
OPEN_ENDED = date.max


class ContestStatus(StrEnum):
    """API 에 그대로 쓰는 값. 화면 표기는 예정 / 접수 중 / 마감."""

    UPCOMING = "upcoming"
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True)
class ApplyPeriod:
    """접수 기간. 상태와 D-day 를 날짜로 계산한다."""

    start: date | None  # 없으면 이미 접수 중으로 본다 (Q-6)
    end: date | None  # 소스에서 못 찾으면 None. 화면은 '접수 마감일 미확인' (Q-35)
    event_start: date | None = None
    event_end: date | None = None

    @property
    def closes_on(self) -> date:
        """참가할 수 있는 마지막 날. 마감 판정·삭제·마감 임박순 정렬의 기준이다 (Q-35).

        접수 마감일을 모르면 대회가 끝난 뒤(끝나는 날이 없으면 시작한 뒤)에는 참가할 수 없다고 본다.
        이 날짜는 마감일로 보여 주지 않는다. 날짜가 하나도 없으면 소스가 내릴 때까지 남는다(Q-2).
        """
        return self.end or self.event_end or self.event_start or OPEN_ENDED

    def status(self, today: date) -> ContestStatus:
        """기준 날짜의 접수 상태를 계산한다."""
        if self.start is not None and today < self.start:
            return ContestStatus.UPCOMING
        if today > self.closes_on:
            return ContestStatus.CLOSED
        return ContestStatus.OPEN

    def d_day(self, today: date) -> int | None:
        """접수 마감까지 남은 일수. 마감 당일 0, 지나면 음수, 마감일을 모르면 None."""
        return None if self.end is None else (self.end - today).days


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
    """중복 비교용 정규화: 맨 앞 [접두어] 제거, 공백·특수문자 제거, 소문자 (Q-2)."""
    return _NON_WORD.sub("", _LEADING_BRACKET.sub("", text)).lower()


# 출처마다 값이 갈리면 앞선 소스의 값을 쓴다 (Q-2). 대회를 직접 여는 플랫폼 > 신청을 받는
# 플랫폼 > 집계 사이트 순이다. 여기 없는 소스는 맨 뒤(이름순)다.
SOURCE_PRIORITY = ("dacon", "devpost", "event_us", "dev_event", "code4korea")
# 한쪽 제목이 다른 쪽을 포함하는지 볼 때, 짧은 쪽이 이보다 짧으면 같은 제목일 때만 같다고 본다.
# '해커톤' 같은 짧은 제목이 이것저것 포함해 다른 대회를 합치는 것을 막는다.
MIN_CONTAINED_TITLE = 8


@dataclass(frozen=True)
class Listing:
    """한 사이트가 올린 대회 하나와 그 분류 태그 (F2-2). 같은 대회의 출처들이 문서 하나로 묶인다."""

    source: str
    source_url: str  # 그 사이트 안에서 원문을 가리키는 식별자
    title: str
    host: str
    field: str
    apply_start: date | None
    apply_end: date | None  # 소스에서 못 찾으면 None (Q-35)
    event_start: date | None
    event_end: date | None
    apply_url: str | None
    tags: ContestTags = NO_TAGS

    def period(self) -> ApplyPeriod:
        """이 출처 값의 접수 기간."""
        return ApplyPeriod(self.apply_start, self.apply_end, self.event_start, self.event_end)


def source_rank(source: str) -> tuple[int, str]:
    """값을 고를 때의 소스 순위. 작을수록 앞선다."""
    if source in SOURCE_PRIORITY:
        return SOURCE_PRIORITY.index(source), ""
    return len(SOURCE_PRIORITY), source


def url_key(url: str | None) -> str | None:
    """참가 URL 비교용: 스킴·`www.`·끝 `/`·프래그먼트를 뺀다. 쿼리는 행사 id 일 수 있어 남긴다."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    if not host:
        return None
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{parts.path.rstrip('/')}{query}"


def titles_overlap(a: str, b: str) -> bool:
    """정규화한 제목이 같거나, 충분히 긴 한쪽이 다른 쪽에 들어 있다 ('X 해커톤 대회' 등)."""
    short, long = sorted((normalize_text(a), normalize_text(b)), key=len)
    if not short:
        return False
    return short == long or (len(short) >= MIN_CONTAINED_TITLE and short in long)


def same_contest(a: Listing, b: Listing) -> bool:
    """두 출처가 같은 대회인지 판정한다 (F2-2, Q-2). 주최는 사이트마다 달라 보지 않는다.

    같은 사이트면 원문 식별자가 같을 때만 같다. 다른 사이트면 제목이 겹치고, 공식 참가 URL
    또는 마감일이 같아야 한다. URL 만으로 합치지 않는 것은 예선·본선이 홈페이지를 같이 쓰기
    때문이다. 마감일을 모르는 출처끼리는 마감일이 같다고 보지 않는다 (Q-35).
    """
    if a.source == b.source:
        return a.source_url == b.source_url
    if not titles_overlap(a.title, b.title):
        return False
    key = url_key(a.apply_url)
    if key is not None and key == url_key(b.apply_url):
        return True
    return a.apply_end is not None and a.apply_end == b.apply_end


def belongs_to(listing: Listing, bundle: Sequence[Listing]) -> bool:
    """출처가 이 묶음의 대회인지. 묶음에는 사이트마다 원문 하나만 들어간다."""
    same_site = [other for other in bundle if other.source == listing.source]
    if same_site:
        return any(other.source_url == listing.source_url for other in same_site)
    return any(same_contest(listing, other) for other in bundle)


def refresh(old: Listing | None, new: Listing) -> Listing:
    """다시 수집한 출처 값. 참가 링크를 못 찾았으면 전에 찾은 링크를 지우지 않는다 (F2-1, Q-30)."""
    if old is None or new.apply_url is not None:
        return new
    return replace(new, apply_url=old.apply_url)


def resolve(bundle: Sequence[Listing]) -> Listing:
    """묶음에서 보여 줄 값을 고른다 (Q-2). 실행 순서와 상관없이 결과가 같다.

    제목·원문은 우선순위가 가장 높은 출처(대표)의 값이다. 접수 기간은 마감일을 찾은 출처 중 앞선
    출처의 것이고, 아무도 못 찾았으면 대표의 값이다 (Q-35). 주최·분야 원문·참가 링크·대회 기간은
    비어 있지 않은 값 중 앞선 출처의 것, 분류 태그는 모든 출처의 합집합이다.
    """
    ordered = sorted(bundle, key=lambda listing: source_rank(listing.source))
    top = ordered[0]
    applied = next((x for x in ordered if x.apply_end is not None), top)
    event = next((x for x in ordered if x.event_start or x.event_end), top)
    tags = ContestTags(
        **{d.value: frozenset().union(*(x.tags.get(d) for x in ordered)) for d in Dimension}
    )
    return replace(
        top,
        apply_start=applied.apply_start,
        apply_end=applied.apply_end,
        host=next((x.host for x in ordered if x.host), ""),
        field=next((x.field for x in ordered if x.field), ""),
        apply_url=next((x.apply_url for x in ordered if x.apply_url), None),
        event_start=event.event_start,
        event_end=event.event_end,
        tags=tags,
    )


def fill_competition_deadline(listing: Listing) -> Listing:
    """접수 마감일을 못 찾은 경진대회는 대회 마감일을 접수 마감일로 쓴다 (Q-35).

    경진대회는 대회 기간 중이면 언제든 참가할 수 있다(Q-6, DACON 과 같은 방식). 다른 형식은 둔다.
    """
    if listing.apply_end is not None or listing.event_end is None:
        return listing
    if ContestFormat.COMPETITION not in listing.tags.format:
        return listing
    return replace(
        listing,
        apply_start=listing.apply_start or listing.event_start,
        apply_end=listing.event_end,
    )


def purge_cutoff(today: date) -> date:
    """참가 마지막 날(ApplyPeriod.closes_on)이 이보다 이르면 지운다. 마감 뒤 3일은 남긴다 (Q-3)."""
    return today - timedelta(days=RETENTION_DAYS_AFTER_CLOSE)
