from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.router import router as auth_router
from app.auth.router import users_router
from app.auth.service import close_http_client, get_token_issuer
from app.contests.router import me_router as my_contests_router
from app.contests.router import router as contests_router
from app.core.db import close_client, get_settings
from app.core.errors import install_error_handlers
from app.core.kv import close_redis
from app.core.log_redaction import install_log_redaction
from app.core.sql import dispose_engine
from app.health import router as health_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    get_token_issuer()  # JWT_SECRET 이 짧거나 없으면 첫 요청이 아니라 기동 때 실패한다
    yield
    get_token_issuer.cache_clear()  # Redis 클라이언트를 닫기 전에 놓는다
    close_client()
    close_redis()
    dispose_engine()
    close_http_client()


def _frontend_origin() -> str:
    """로그인 후 돌아갈 프론트 주소의 출처(scheme://host:port).

    이 출처의 브라우저 요청만 허용한다.
    """
    url = urlsplit(get_settings().frontend_login_redirect_url)
    return f"{url.scheme}://{url.netloc}"


def create_app() -> FastAPI:
    application = FastAPI(title="Contest Collector", lifespan=lifespan)
    # 토큰은 쿠키가 아니라 Authorization 헤더로 오가므로 credentials 는 허용하지 않는다
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[_frontend_origin()],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    install_log_redaction()
    install_error_handlers(application)
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(users_router)
    application.include_router(contests_router)
    application.include_router(my_contests_router)
    return application


app = create_app()
