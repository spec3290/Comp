"""OAuth 제공자 계약 (F1-6, F1-8). Google/Kakao 구현체가 이 Protocol 을 따른다 (ADR-0003)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class OAuthIdentity:
    provider: str
    provider_id: str


class OAuthProvider(Protocol):
    name: str

    def exchange_code(self, code: str) -> OAuthIdentity: ...

    def revoke(self, provider_id: str) -> None:
        """OAuth 연결 해제 (F1-8)."""
        ...
