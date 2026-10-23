"""MongoDB 연결. 프로세스당 MongoClient 하나를 재사용한다.

MongoClient 는 생성 시점에 접속하지 않고 첫 요청 때 접속한다.
조회한 datetime 은 KST tz-aware 로 돌려받는다 (AGENTS.md 하드 규칙).
"""

from functools import lru_cache
from typing import Any

from pymongo import MongoClient
from pymongo.database import Database

from app.core.config import Settings
from app.core.time import KST

Document = dict[str, Any]


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_client() -> MongoClient[Document]:
    return MongoClient(get_settings().mongodb_uri, tz_aware=True, tzinfo=KST)


def get_database() -> Database[Document]:
    return get_client()[get_settings().mongodb_db]


def ping() -> bool:
    """서버에 닿으면 True. 헬스 체크·기동 점검용."""
    try:
        get_client().admin.command("ping")
    except Exception:
        return False
    return True


def close_client() -> None:
    if get_client.cache_info().currsize:
        get_client().close()
        get_client.cache_clear()
