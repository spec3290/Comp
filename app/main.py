from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.auth.router import router as auth_router
from app.auth.service import close_http_client
from app.contests.router import router as contests_router
from app.core.db import close_client
from app.core.errors import install_error_handlers
from app.core.kv import close_redis
from app.core.sql import dispose_engine
from app.health import router as health_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    close_client()
    close_redis()
    dispose_engine()
    close_http_client()


def create_app() -> FastAPI:
    application = FastAPI(title="Contest Collector", lifespan=lifespan)
    install_error_handlers(application)
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(contests_router)
    return application


app = create_app()
