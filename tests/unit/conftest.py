"""인증 테스트 fixture. PostgreSQL 은 메모리 SQLite, Redis 는 fakeredis 로 바꾼다."""

from collections.abc import Iterator

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.repository import LoginCodeStore, TokenStore, UserRepository
from app.auth.service import (
    AuthService,
    CurrentToken,
    UserService,
    get_auth_service,
    get_token_issuer,
    get_user_service,
)
from app.auth.tokens import TokenIssuer
from app.core.errors import install_error_handlers
from app.core.sql import Base
from app.main import app
from tests.unit.auth_support import (
    ACCESS_TTL,
    CODES,
    FRONTEND_URL,
    REFRESH_TTL,
    SECRET,
    FakeProvider,
)


@pytest.fixture
def session() -> Iterator[Session]:
    """메모리 SQLite 로 만든 세션."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s
    engine.dispose()


@pytest.fixture
def redis() -> fakeredis.FakeRedis:
    """가짜 Redis."""
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def issuer(redis: fakeredis.FakeRedis) -> TokenIssuer:
    """테스트 키·TTL 로 만든 토큰 발급기."""
    return TokenIssuer(TokenStore(redis), SECRET, ACCESS_TTL, REFRESH_TTL)


@pytest.fixture
def client(
    session: Session, redis: fakeredis.FakeRedis, issuer: TokenIssuer
) -> Iterator[TestClient]:
    """가짜 제공자와 테스트 의존성을 끼운 API 클라이언트."""
    service = AuthService(
        providers={"google": FakeProvider(CODES)},
        users=UserRepository(session),
        issuer=issuer,
        login_codes=LoginCodeStore(redis),
        frontend_url=FRONTEND_URL,
    )
    app.dependency_overrides[get_auth_service] = lambda: service
    app.dependency_overrides[get_token_issuer] = lambda: issuer
    app.dependency_overrides[get_user_service] = lambda: UserService(UserRepository(session))
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def protected(issuer: TokenIssuer) -> Iterator[TestClient]:
    """`CurrentToken` 만 거는 작은 앱. 인증 의존성 자체를 확인한다."""
    api = FastAPI()
    install_error_handlers(api)

    @api.get("/protected")
    def protected_route(token: CurrentToken) -> dict[str, int]:
        """인증이 필요한 테스트용 엔드포인트."""
        return {"user_id": token.user_id}

    api.dependency_overrides[get_token_issuer] = lambda: issuer
    yield TestClient(api)
