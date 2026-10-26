"""회원 저장소(PostgreSQL)와 토큰 저장소(Redis, Q-4·Q-17).

Refresh Token 은 `refresh:{jti}` 해시에 user_id·family_id 를 담고, 만료 시각에 맞춰 Redis TTL
로 지운다. 한 로그인에서 회전으로 이어진 토큰들(family)의 jti 는 `refresh_family:{family_id}`
집합에 모아 한꺼번에 폐기한다 (ADR-0002).

회전한 토큰은 지우지 않고 `rotated` 표시만 남겨 만료 때까지 재사용을 탐지한다.
로그아웃한 Access 는 `blacklist:{jti}` 에 남은 만료 시간만큼만 둔다 (F1-5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from redis import Redis
from redis.client import Pipeline
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import User


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: int) -> User | None:
        return self._session.get(User, user_id)

    def get_by_provider(self, provider: str, provider_id: str) -> User | None:
        stmt = select(User).where(User.provider == provider, User.provider_id == provider_id)
        return self._session.scalars(stmt).one_or_none()

    def get_or_create(self, provider: str, provider_id: str, now: datetime) -> tuple[User, bool]:
        """`(provider, provider_id)` 회원을 돌려준다. 없으면 만들고 두 번째 값이 True.

        같은 계정이 동시에 처음 로그인해도 UNIQUE 제약이 한쪽을 막으므로, 막힌 쪽은 먼저 만든
        회원을 다시 조회한다.
        """
        user = self.get_by_provider(provider, provider_id)
        if user is not None:
            return user, False
        user = User(provider=provider, provider_id=provider_id, created_at=now)
        self._session.add(user)
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            existing = self.get_by_provider(provider, provider_id)
            if existing is None:
                raise
            return existing, False
        return user, True


def _refresh_key(jti: str) -> str:
    return f"refresh:{jti}"


def _family_key(family_id: str) -> str:
    return f"refresh_family:{family_id}"


def _blacklist_key(jti: str) -> str:
    return f"blacklist:{jti}"


def _revoked_key(family_id: str) -> str:
    return f"refresh_family_revoked:{family_id}"


@dataclass(frozen=True)
class RefreshRecord:
    user_id: int
    family_id: str
    reused: bool  # 이미 회전된 토큰이 다시 왔다 (탈취 의심)


class TokenStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def save(self, jti: str, user_id: int, family_id: str, ttl: timedelta) -> bool:
        """저장했으면 True. family 가 이미 폐기됐으면 저장하지 않고 False.

        폐기 표식을 WATCH 하고 같은 트랜잭션에서 저장하므로, 재발급 도중 다른 요청이 family 를
        폐기해도 폐기된 family 에 새 토큰이 살아남지 않는다.
        """
        key, family, revoked = _refresh_key(jti), _family_key(family_id), _revoked_key(family_id)
        saved: list[bool] = []

        def write(pipe: Pipeline) -> None:
            saved.clear()  # WATCH 충돌로 다시 불릴 수 있다
            if pipe.exists(revoked):
                saved.append(False)
                return
            pipe.multi()
            pipe.hset(key, mapping={"user_id": str(user_id), "family_id": family_id})
            pipe.expire(key, ttl)
            pipe.sadd(family, jti)
            pipe.expire(family, ttl)  # family 의 가장 최근 토큰과 같이 만료된다
            saved.append(True)

        self._redis.transaction(write, revoked)
        return saved[0]

    def consume(self, jti: str) -> RefreshRecord | None:
        """토큰을 회전된 것으로 표시하고 저장된 값을 돌려준다. 없거나 만료됐으면 None.

        같은 토큰으로 동시에 재발급해도 WATCH 트랜잭션이라 한 요청만 처음 사용이 되고,
        나머지는 재시도 끝에 `reused=True` 를 받는다.
        """
        key = _refresh_key(jti)
        found: list[RefreshRecord] = []

        def mark_rotated(pipe: Pipeline) -> None:
            found.clear()  # WATCH 충돌로 다시 불릴 수 있다
            stored = pipe.hgetall(key)
            if not stored:
                return
            record = RefreshRecord(
                user_id=int(stored["user_id"]),
                family_id=str(stored["family_id"]),
                reused="rotated" in stored,
            )
            found.append(record)
            if not record.reused:
                pipe.multi()
                pipe.hset(key, "rotated", "1")

        self._redis.transaction(mark_rotated, key)
        return found[0] if found else None

    def revoke_family(self, family_id: str, ttl: timedelta) -> None:
        """family 의 모든 Refresh 를 지운다. 이후 그 family 의 어떤 토큰도 재발급되지 않는다.

        폐기 표식을 먼저 남긴 뒤 지운다. 표식보다 먼저 끝난 저장은 아래에서 함께 지워지고,
        늦은 저장은 save 의 WATCH 에 걸려 거절된다. 표식은 family 의 토큰이 모두 만료될
        시간(ttl)만큼 남긴다.
        """
        self._redis.set(_revoked_key(family_id), "1", ex=ttl)
        family = _family_key(family_id)
        jtis = self._redis.smembers(family)
        self._redis.delete(family, *(_refresh_key(str(jti)) for jti in jtis))

    def blacklist(self, jti: str, ttl: timedelta) -> None:
        """Access 를 만료 때까지 막는다. 이미 만료됐으면 저장할 필요가 없다.

        초 단위로 넘기면 남은 시간이 잘려 만료 직전에 토큰이 다시 통과할 수 있어서, 밀리초로
        올림하고 시계 오차만큼 1초를 더 둔다.
        """
        remaining_ms = math.ceil(ttl.total_seconds() * 1000)
        if remaining_ms > 0:
            self._redis.set(_blacklist_key(jti), "1", px=remaining_ms + 1000)

    def is_blacklisted(self, jti: str) -> bool:
        return bool(self._redis.exists(_blacklist_key(jti)))
