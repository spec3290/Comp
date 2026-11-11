"""회원 저장소(PostgreSQL)와 토큰 저장소(Redis, Q-4·Q-17).

Refresh Token 은 `refresh:{jti}` 해시에 user_id·family_id 를 담고, 만료 시각에 맞춰 Redis TTL
로 지운다. 한 로그인에서 회전으로 이어진 토큰들(family)의 jti 는 `refresh_family:{family_id}`
집합에 모아 한꺼번에 폐기한다 (ADR-0002).

회전한 토큰은 지우지 않고 `rotated` 표시만 남겨 만료 때까지 재사용을 탐지한다.
로그아웃한 Access 는 `blacklist:{jti}` 에 남은 만료 시간만큼만 둔다 (F1-5).
소셜 로그인 콜백이 프론트로 넘기는 일회용 코드는 `login_code:{code}` 에 짧게 둔다 (Q-16).
회원의 로그인 세션 목록은 `user_families:{user_id}` 에 모아 탈퇴(F1-8) 때 전부 폐기하고,
탈퇴 재인증 state 는 `withdraw_state:{state}` 에 회원 id 와 묶어 둔다 (Q-25).
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

from app.auth.models import User, UserInterestField


class NicknameConflictError(Exception):
    """다른 회원이 이미 쓰는 닉네임이다 (UNIQUE 제약)."""


class UserRepository:
    """PostgreSQL 회원 저장소."""

    def __init__(self, session: Session) -> None:
        """요청 단위 세션을 받는다."""
        self._session = session

    def get(self, user_id: int) -> User | None:
        """ID 로 회원을 찾는다."""
        return self._session.get(User, user_id)

    def get_by_provider(self, provider: str, provider_id: str) -> User | None:
        """제공자와 제공자 측 ID 로 회원을 찾는다."""
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

    def nickname_taken(self, nickname: str, exclude_user_id: int) -> bool:
        """다른 회원이 이미 쓰는 닉네임인지 확인한다."""
        stmt = select(User.id).where(User.nickname == nickname, User.id != exclude_user_id)
        return self._session.scalars(stmt).first() is not None

    def update_profile(
        self,
        user: User,
        nickname: str,
        grade: int | None,
        school_type: str,
        fields: list[str],
    ) -> None:
        """프로필을 통째로 바꾼다. 관심 분야는 바뀐 것만 지우고 더한다.

        확인과 저장 사이에 다른 회원이 같은 닉네임을 먼저 저장하면 UNIQUE 제약에 걸려
        NicknameConflictError 가 난다.
        """
        user.nickname, user.grade, user.school_type = nickname, grade, school_type
        wanted = set(fields)
        for interest in [i for i in user.interests if i.field not in wanted]:
            user.interests.remove(interest)
        current = {i.field for i in user.interests}
        user.interests.extend(UserInterestField(field=f) for f in fields if f not in current)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise NicknameConflictError(nickname) from exc

    def delete(self, user: User) -> None:
        """회원을 지운다. 관심 분야·북마크는 외래 키 ON DELETE CASCADE 로 함께 지워진다."""
        self._session.delete(user)
        self._session.commit()


def _refresh_key(jti: str) -> str:
    """Refresh Token 레코드의 Redis 키."""
    return f"refresh:{jti}"


def _family_key(family_id: str) -> str:
    """로그인 세션(family)에 속한 토큰 목록의 Redis 키."""
    return f"refresh_family:{family_id}"


def _blacklist_key(jti: str) -> str:
    """로그아웃된 Access Token 의 Redis 키."""
    return f"blacklist:{jti}"


def _revoked_key(family_id: str) -> str:
    """폐기된 family 표식의 Redis 키."""
    return f"refresh_family_revoked:{family_id}"


def _login_code_key(code: str) -> str:
    """일회용 로그인 코드의 Redis 키."""
    return f"login_code:{code}"


def _user_families_key(user_id: int) -> str:
    """회원의 로그인 세션(family) 목록의 Redis 키."""
    return f"user_families:{user_id}"


def _withdraw_state_key(state: str) -> str:
    """탈퇴 재인증 state 의 Redis 키."""
    return f"withdraw_state:{state}"


@dataclass(frozen=True)
class RefreshRecord:
    """Refresh Token 을 소비한 결과."""

    user_id: int
    family_id: str
    reused: bool  # 이미 회전된 토큰이 다시 왔다 (탈취 의심)


class TokenStore:
    """Redis 에 Refresh Token·family·블랙리스트를 저장한다."""

    def __init__(self, redis: Redis) -> None:
        """Redis 클라이언트를 받는다."""
        self._redis = redis

    def save(self, jti: str, user_id: int, family_id: str, ttl: timedelta) -> bool:
        """저장했으면 True. family 가 이미 폐기됐으면 저장하지 않고 False.

        폐기 표식을 WATCH 하고 같은 트랜잭션에서 저장하므로, 재발급 도중 다른 요청이 family 를
        폐기해도 폐기된 family 에 새 토큰이 살아남지 않는다.
        """
        key, family, revoked = _refresh_key(jti), _family_key(family_id), _revoked_key(family_id)
        user_families = _user_families_key(user_id)
        saved: list[bool] = []

        def write(pipe: Pipeline) -> None:
            """family 가 폐기되지 않았을 때만 토큰을 저장하는 트랜잭션 본문."""
            saved.clear()  # WATCH 충돌로 다시 불릴 수 있다
            if pipe.exists(revoked):
                saved.append(False)
                return
            pipe.multi()
            pipe.hset(key, mapping={"user_id": str(user_id), "family_id": family_id})
            pipe.expire(key, ttl)
            pipe.sadd(family, jti)
            pipe.expire(family, ttl)  # family 의 가장 최근 토큰과 같이 만료된다
            pipe.sadd(user_families, family_id)
            pipe.expire(user_families, ttl)  # 회원의 가장 최근 토큰과 같이 만료된다
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
            """토큰을 읽고 회전됨으로 표시하는 트랜잭션 본문."""
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

    def revoke_user(self, user_id: int, ttl: timedelta) -> None:
        """회원의 모든 로그인 세션을 폐기한다 (탈퇴). 이후 그 세션의 Access·Refresh 는 거절된다.

        회원 id 대신 세션 단위로 막아서, id 를 다시 쓰는 DB 에서도 새 회원이 막히지 않는다.
        """
        user_families = _user_families_key(user_id)
        for family_id in self._redis.smembers(user_families):
            self.revoke_family(str(family_id), ttl)
        self._redis.delete(user_families)

    def is_access_revoked(self, jti: str, family_id: str) -> bool:
        """이 Access 가 블랙리스트에 있거나 세션(family)이 폐기됐다. 한 번의 EXISTS 로 본다.

        세션 폐기 표식은 Refresh 만료 시간만큼 남으므로 그 세션의 어떤 Access 보다 오래 간다.
        """
        return bool(self._redis.exists(_blacklist_key(jti), _revoked_key(family_id)))


class LoginCodeStore:
    """콜백에서 프론트로 넘기는 일회용 로그인 코드 (Q-16). 코드 하나는 회원 하나를 가리킨다."""

    def __init__(self, redis: Redis) -> None:
        """Redis 클라이언트를 받는다."""
        self._redis = redis

    def save(self, code: str, user_id: int, ttl: timedelta) -> None:
        """로그인 코드를 회원 ID 와 묶어 TTL 동안 저장한다."""
        self._redis.set(_login_code_key(code), str(user_id), ex=ttl)

    def consume(self, code: str) -> int | None:
        """코드를 지우면서 회원 id 를 돌려준다. 없거나 만료됐거나 이미 썼으면 None.

        GETDEL 한 번으로 읽고 지우므로 같은 코드로 동시에 교환해도 한 요청만 성공한다.
        """
        value = self._redis.getdel(_login_code_key(code))
        return int(value) if isinstance(value, str) else None


class WithdrawStateStore:
    """탈퇴 재인증의 state 를 회원과 묶어 둔다 (Q-25). state 하나는 한 번만 쓸 수 있다.

    로그인과 달리 쿠키가 아니라 서버에 두는 것은, 탈퇴 시작이 Bearer 를 실은 API 호출이라
    브라우저 쿠키를 심을 수 없기 때문이다. 콜백은 state 로 탈퇴를 요청한 회원을 찾는다.
    """

    def __init__(self, redis: Redis) -> None:
        """Redis 클라이언트를 받는다."""
        self._redis = redis

    def save(self, state: str, user_id: int, ttl: timedelta) -> None:
        """state 를 회원 ID 와 묶어 TTL 동안 저장한다."""
        self._redis.set(_withdraw_state_key(state), str(user_id), ex=ttl)

    def consume(self, state: str) -> int | None:
        """state 를 지우면서 회원 id 를 돌려준다. 없거나 만료됐거나 이미 썼으면 None."""
        value = self._redis.getdel(_withdraw_state_key(state))
        return int(value) if isinstance(value, str) else None
