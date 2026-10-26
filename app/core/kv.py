"""Redis 연결. Refresh Token 처럼 만료가 있는 키-값을 둔다 (ADR-0002, Q-4).

Redis 클라이언트는 생성 시점에 접속하지 않고 첫 명령 때 접속한다.
"""

from functools import lru_cache

from redis import Redis

from app.core.db import get_settings


@lru_cache
def get_redis() -> Redis:
    # Redis 가 응답하지 않을 때 요청 스레드가 무한정 묶이지 않게 한다
    return Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def close_redis() -> None:
    if get_redis.cache_info().currsize:
        get_redis().close()
        get_redis.cache_clear()
