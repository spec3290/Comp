"""서비스 JWT 발급·검증·회전 (F1-4)·폐기 (F1-5, ADR-0002).

Access 는 서명만으로 검증하는 짧은 JWT, Refresh 는 jti 를 Redis 에 저장해 회전·폐기한다.
만료는 401 `TOKEN_EXPIRED`, 그 밖의 위조·형식 오류·회전·폐기는 모두 401 `TOKEN_INVALID` 다
(ADR-0004). 토큰 값은 로그에 남기지 않는다.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import jwt

from app.auth.repository import RefreshRecord, TokenStore
from app.core.errors import AppError
from app.core.time import KST

logger = logging.getLogger(__name__)

JWT_ALGORITHM = "HS256"
MIN_SECRET_BYTES = 32
TokenType = Literal["access", "refresh"]
# `fam` 이 없는 토큰(F1-5 이전 발급)은 로그아웃할 세션을 찾을 수 없어 받지 않는다. 재발급하면 붙는다
_REQUIRED_CLAIMS = ["sub", "jti", "fam", "type", "iat", "exp"]


class TokenExpiredError(AppError):
    status_code = 401
    code = "TOKEN_EXPIRED"


class TokenInvalidError(AppError):
    status_code = 401
    code = "TOKEN_INVALID"


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    access_expires_in: int  # 초


@dataclass(frozen=True)
class TokenClaims:
    user_id: int
    jti: str
    family_id: str  # 로그인 세션
    expires_at: datetime


class TokenIssuer:
    def __init__(
        self,
        store: TokenStore,
        secret: str,
        access_ttl: timedelta,
        refresh_ttl: timedelta,
    ) -> None:
        # HS256 키가 짧으면 서명을 추측할 수 있다 (RFC 7518 3.2). 기본값·빈 값도 여기서 막힌다
        if len(secret.encode()) < MIN_SECRET_BYTES:
            raise ValueError(f"JWT_SECRET 은 {MIN_SECRET_BYTES}바이트 이상이어야 합니다.")
        self._store = store
        self._secret = secret
        self._access_ttl = access_ttl
        self._refresh_ttl = refresh_ttl

    def issue(self, user_id: int, now: datetime, family_id: str | None = None) -> IssuedTokens:
        """새 토큰 쌍. 로그인이면 새 family, 재발급이면 기존 family 를 이어 쓴다."""
        family = family_id or uuid.uuid4().hex
        refresh_jti = uuid.uuid4().hex
        access = self._encode(
            "access", user_id, uuid.uuid4().hex, now, self._access_ttl, {"fam": family}
        )
        refresh = self._encode(
            "refresh", user_id, refresh_jti, now, self._refresh_ttl, {"fam": family}
        )
        if not self._store.save(refresh_jti, user_id, family, self._refresh_ttl):
            # 재발급 도중 같은 family 가 재사용 탐지로 폐기됐다
            raise TokenInvalidError("이미 사용된 토큰입니다. 다시 로그인해 주세요.")
        return IssuedTokens(
            access_token=access,
            refresh_token=refresh,
            access_expires_in=int(self._access_ttl.total_seconds()),
        )

    def verify(self, token: str, expected: TokenType) -> TokenClaims:
        """서명·만료·종류를 확인한다. 저장소(회전·블랙리스트)는 보지 않는다."""
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[JWT_ALGORITHM],
                options={"require": _REQUIRED_CLAIMS},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError("토큰이 만료되었습니다.") from exc
        except jwt.InvalidTokenError as exc:
            raise TokenInvalidError("유효하지 않은 토큰입니다.") from exc
        if claims["type"] != expected:
            raise TokenInvalidError("유효하지 않은 토큰입니다.")
        try:
            return TokenClaims(
                user_id=int(claims["sub"]),
                jti=str(claims["jti"]),
                family_id=str(claims["fam"]),
                expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=KST),
            )
        except (TypeError, ValueError) as exc:
            raise TokenInvalidError("유효하지 않은 토큰입니다.") from exc

    def authenticate(self, access_token: str) -> TokenClaims:
        """API 요청의 Access 를 검증한다. 로그아웃해 블랙리스트에 오른 토큰도 거절한다."""
        claims = self.verify(access_token, "access")
        if self._store.is_blacklisted(claims.jti):
            raise TokenInvalidError("로그아웃한 토큰입니다. 다시 로그인해 주세요.")
        return claims

    def revoke_session(self, access: TokenClaims, now: datetime) -> None:
        """로그아웃: 이 로그인 세션(family)의 Refresh 를 지우고 쓰던 Access 를 만료까지 막는다."""
        self._store.revoke_family(access.family_id, self._refresh_ttl)
        self._store.blacklist(access.jti, access.expires_at - now)

    def rotate(self, refresh_token: str) -> RefreshRecord:
        """Refresh 를 한 번 쓴 것으로 표시한다. 이미 쓴 토큰이면 family 를 폐기하고 401.

        돌려받은 user_id·family_id 로 새 토큰을 발급하는 것은 호출한 쪽 몫이다.
        """
        claims = self.verify(refresh_token, "refresh")
        record = self._store.consume(claims.jti)
        if record is None or record.user_id != claims.user_id:
            raise TokenInvalidError("유효하지 않은 토큰입니다.")
        if record.reused:
            self._store.revoke_family(record.family_id, self._refresh_ttl)
            logger.warning("회전된 Refresh 재사용 감지, family 폐기: user_id=%d", record.user_id)
            raise TokenInvalidError("이미 사용된 토큰입니다. 다시 로그인해 주세요.")
        return record

    def _encode(
        self,
        token_type: TokenType,
        user_id: int,
        jti: str,
        now: datetime,
        ttl: timedelta,
        extra: Mapping[str, str] | None = None,
    ) -> str:
        claims = {
            "sub": str(user_id),
            "jti": jti,
            "type": token_type,
            "iat": now,
            "exp": now + ttl,
            **(extra or {}),
        }
        return jwt.encode(claims, self._secret, algorithm=JWT_ALGORITHM)
