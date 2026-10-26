"""Alembic 실행 환경. DB 주소는 Settings(DATABASE_URL)에서 읽는다 (비밀 값은 환경변수로만)."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.auth.models  # noqa: F401  모델을 Base.metadata 에 등록
import app.contests.models  # noqa: F401
from app.core.db import get_settings
from app.core.sql import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ConfigParser 보간 때문에 URL 인코딩된 비밀번호의 `%` 를 `%%` 로 바꿔 넣는다
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
