"""프로필 규칙 (F1-7, Q-5). 프레임워크·DB 를 모르는 순수 규칙이다 (AC-1).

학교 유형·관심 분야 어휘는 자격 태그(F3-1)와 맞춤 추천(F3-4)이 같은 값을 쓰도록 여기 한 곳에 둔다.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum

NICKNAME_MIN_LENGTH = 2
NICKNAME_MAX_LENGTH = 12
GRADE_MIN = 1
GRADE_MAX = 6


class SchoolType(StrEnum):
    MIDDLE = "중학교"
    HIGH = "고등학교"
    UNIVERSITY = "대학교"
    OTHER = "기타"


class InterestField(StrEnum):
    DEV = "개발"
    AI = "AI"
    SECURITY = "보안"
    DESIGN = "디자인"
    IDEA = "아이디어"


def normalize_nickname(raw: str) -> str:
    """앞뒤 공백을 지우고 길이·문자를 확인한다. 어기면 ValueError."""
    nickname = unicodedata.normalize("NFC", raw.strip())
    if not NICKNAME_MIN_LENGTH <= len(nickname) <= NICKNAME_MAX_LENGTH:
        raise ValueError(f"닉네임은 {NICKNAME_MIN_LENGTH}~{NICKNAME_MAX_LENGTH}자여야 합니다.")
    if any(unicodedata.category(ch).startswith("C") for ch in nickname):
        raise ValueError("닉네임에 제어 문자를 쓸 수 없습니다.")
    return nickname


def ordered_fields(fields: set[InterestField] | frozenset[InterestField]) -> list[InterestField]:
    """응답·저장 순서를 어휘 정의 순서로 고정한다."""
    return [field for field in InterestField if field in fields]


@dataclass(frozen=True)
class ProfileInput:
    nickname: str  # normalize_nickname 을 거친 값
    grade: int | None
    school_type: SchoolType
    interest_fields: frozenset[InterestField]
