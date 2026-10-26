"""PostgreSQL 연결 (SQLAlchemy, ADR-0008). 사용자·인증 등 관계형 데이터를 둔다.

스키마는 Alembic 마이그레이션(`migrations/`)으로만 바꾼다. 모델은 각 기능의 `models.py`에서
`Base` 를 상속한다.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.db import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """요청마다 세션 하나. 커밋은 서비스가 하고, 끝나면 닫는다."""
    with _session_factory()() as session:
        yield session


def dispose_engine() -> None:
    if get_engine.cache_info().currsize:
        get_engine().dispose()
        get_engine.cache_clear()
        _session_factory.cache_clear()
