"""인증 테스트 fixture. PostgreSQL 은 메모리 SQLite, Redis 는 fakeredis 로 바꾼다."""

from collections.abc import Iterator

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.repository import TokenStore, UserRepository
from app.auth.service import AuthService, CurrentToken, get_auth_service, get_token_issuer
from app.auth.tokens import TokenIssuer
from app.core.errors import install_error_handlers
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
    return TokenIssuer(TokenStore(redis), SECRET, ACCESS_TTL, REFRESH_TTL)


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


@pytest.fixture
def protected(issuer: TokenIssuer) -> Iterator[TestClient]:
    """`CurrentToken` 만 거는 작은 앱. 인증 의존성 자체를 확인한다."""
    api = FastAPI()
    install_error_handlers(api)

    @api.get("/protected")
    def protected_route(token: CurrentToken) -> dict[str, int]:
        return {"user_id": token.user_id}

    api.dependency_overrides[get_token_issuer] = lambda: issuer
    yield TestClient(api)
