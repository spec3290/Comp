"""Alembic 실행 환경. 접속 주소는 DATABASE_URL 환경변수, 모델 메타데이터는 app.core.db.Base."""

from logging.config import fileConfig

from alembic import context

import app.contests.models  # noqa: F401  테이블을 Base.metadata 에 등록
from app.core.db import Base, get_engine, get_settings

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

target_metadata = Base.metadata


def run_offline() -> None:
    context.configure(url=get_settings().database_url, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    with get_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
