"""인증 테스트 fixture. PostgreSQL 은 메모리 SQLite, Redis 는 fakeredis 로 바꾼다."""

from collections.abc import Iterator

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.repository import RefreshTokenStore, UserRepository
from app.auth.service import AuthService, get_auth_service, get_token_issuer
from app.auth.tokens import TokenIssuer
from app.core.sql import Base
from app.main import app
from tests.unit.auth_support import ACCESS_TTL, CODES, REFRESH_TTL, SECRET, FakeProvider


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s
    engine.dispose()


@pytest.fixture
def redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def issuer(redis: fakeredis.FakeRedis) -> TokenIssuer:
    return TokenIssuer(RefreshTokenStore(redis), SECRET, ACCESS_TTL, REFRESH_TTL)


@pytest.fixture
def client(session: Session, issuer: TokenIssuer) -> Iterator[TestClient]:
    service = AuthService(
        providers={"google": FakeProvider(CODES)},
        users=UserRepository(session),
        issuer=issuer,
    )
    app.dependency_overrides[get_auth_service] = lambda: service
    app.dependency_overrides[get_token_issuer] = lambda: issuer
    yield TestClient(app)
    app.dependency_overrides.clear()
