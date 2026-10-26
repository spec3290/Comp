"""회원 저장소(PostgreSQL)와 토큰 저장소(Redis, Q-4·Q-17).

Refresh Token 은 `refresh:{jti}` 해시에 user_id·family_id 를 담고, 만료 시각에 맞춰 Redis TTL
로 지운다. 한 로그인에서 회전으로 이어진 토큰들(family)의 jti 는 `refresh_family:{family_id}`
집합에 모아 한꺼번에 폐기한다 (ADR-0002).

회전한 토큰은 지우지 않고 `rotated` 표시만 남겨 만료 때까지 재사용을 탐지한다.
로그아웃한 Access 는 `blacklist:{jti}` 에 남은 만료 시간만큼만 둔다 (F1-5).
"""

from __future__ import annotations

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


@dataclass(frozen=True)
class RefreshRecord:
    user_id: int
    family_id: str
    reused: bool  # 이미 회전된 토큰이 다시 왔다 (탈취 의심)


class TokenStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def save(self, jti: str, user_id: int, family_id: str, ttl: timedelta) -> None:
        key, family = _refresh_key(jti), _family_key(family_id)
        with self._redis.pipeline() as pipe:
            pipe.hset(key, mapping={"user_id": str(user_id), "family_id": family_id})
            pipe.expire(key, ttl)
            pipe.sadd(family, jti)
            pipe.expire(family, ttl)  # family 의 가장 최근 토큰과 같이 만료된다
            pipe.execute()

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

    def revoke_family(self, family_id: str) -> None:
        """family 의 모든 Refresh 를 지운다. 이후 그 family 의 어떤 토큰도 재발급되지 않는다."""
        family = _family_key(family_id)
        jtis = self._redis.smembers(family)
        self._redis.delete(family, *(_refresh_key(str(jti)) for jti in jtis))

    def blacklist(self, jti: str, ttl: timedelta) -> None:
        """Access 를 만료 때까지 막는다. 이미 만료됐으면 저장할 필요가 없다."""
        if ttl.total_seconds() >= 1:
            self._redis.set(_blacklist_key(jti), "1", ex=ttl)

    def is_blacklisted(self, jti: str) -> bool:
        return bool(self._redis.exists(_blacklist_key(jti)))
