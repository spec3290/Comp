"""회원 저장소(PostgreSQL)와 Refresh Token 저장소(Redis, Q-4).

Refresh Token 은 `refresh:{jti}` 해시에 user_id·family_id 를 담고, 만료 시각에 맞춰 Redis TTL
로 지운다. 회전·폐기(F1-4, F1-5)는 같은 키를 쓴다 (ADR-0002).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from redis import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import User


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

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


class RefreshTokenStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def save(self, jti: str, user_id: int, family_id: str, ttl: timedelta) -> None:
        key = _refresh_key(jti)
        with self._redis.pipeline() as pipe:
            pipe.hset(key, mapping={"user_id": str(user_id), "family_id": family_id})
            pipe.expire(key, ttl)
            pipe.execute()

    def get(self, jti: str) -> dict[str, str]:
        """저장된 값. 없거나 만료됐으면 빈 dict."""
        # 클라이언트가 decode_responses=True 라 값은 str 이지만 타입 힌트는 bytes 도 포함한다
        stored = self._redis.hgetall(_refresh_key(jti))
        return {str(k): str(v) for k, v in stored.items()}
