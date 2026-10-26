"""시간 규칙: 모든 시각은 KST 기준 tz-aware datetime 으로 다룬다 (AGENTS.md 하드 규칙)."""

from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    return datetime.now(KST)
