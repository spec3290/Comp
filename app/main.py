"""FastAPI 앱 조립: 미들웨어, 에러 핸들러, 라우터 등록."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.router import add_swagger_oauth, users_router
from app.auth.router import router as auth_router
from app.auth.service import close_http_client, get_token_issuer, swagger_oauth_providers
from app.contests.router import me_router as my_contests_router
from app.contests.router import router as contests_router
from app.core.db import close_client, get_settings
from app.core.errors import install_error_handlers
from app.core.kv import close_redis
from app.core.log_redaction import install_log_redaction
from app.core.sql import dispose_engine
from app.health import router as health_router
from app.notifications.router import router as notifications_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    """기동 시 설정을 검증하고 종료 시 외부 연결을 정리한다."""
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
    """CORS·로그 가림·에러 핸들러·라우터를 붙인 앱을 만든다."""
    application = FastAPI(
        title="Contest Collector",
        lifespan=lifespan,
        # Swagger Authorize 창의 client_id 칸. 서버는 값을 보지 않는다 (Q-40)
        swagger_ui_init_oauth={"clientId": "swagger"},
    )
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
    application.include_router(notifications_router)
    _install_swagger_oauth(application)
    return application


def _install_swagger_oauth(application: FastAPI) -> None:
    """SWAGGER_OAUTH_ENABLED 면 /docs 의 Authorize 창에 소셜 로그인을 띄운다 (Q-40)."""
    build_schema = application.openapi

    def openapi() -> dict[str, Any]:
        """처음 한 번 만든 스키마에 OAuth2 스킴을 더해 캐시한다."""
        if application.openapi_schema is None:
            application.openapi_schema = add_swagger_oauth(
                build_schema(), swagger_oauth_providers()
            )
        return application.openapi_schema

    application.openapi = openapi  # type: ignore[method-assign]


app = create_app()
