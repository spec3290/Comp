"""DB 연결 (ADR-0008). PostgreSQL 은 SQLAlchemy, MongoDB 는 pymongo.

프로세스당 MongoClient·Engine 하나를 재사용한다. 둘 다 생성 시점이 아니라 첫 요청 때 접속한다.
조회한 datetime 은 KST tz-aware 로 돌려받는다 (AGENTS.md 하드 규칙).
"""

from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from pymongo import MongoClient
from pymongo.database import Database
from sqlalchemy import DateTime, Dialect, Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

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


class Base(DeclarativeBase):
    """PostgreSQL 테이블 모델의 공통 부모. 마이그레이션은 Alembic (ADR-0005)."""


class AwareDateTime(TypeDecorator[datetime]):
    """UTC 로 저장하고 KST tz-aware 로 읽는다 (ADR-0005). naive 값은 받지 않는다.

    SQLite 처럼 시간대를 보존하지 않는 DB 에서 naive 로 돌아오면 저장한 UTC 로 해석한다.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime 은 저장할 수 없다")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(KST)


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)


def dispose_engine() -> None:
    if get_engine.cache_info().currsize:
        get_engine().dispose()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
