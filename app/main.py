from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.contests.router import router as contests_router
from app.core.db import close_client
from app.core.errors import install_error_handlers
from app.health import router as health_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    close_client()


def create_app() -> FastAPI:
    application = FastAPI(title="Contest Collector", lifespan=lifespan)
    install_error_handlers(application)
    application.include_router(health_router)
    application.include_router(contests_router)
    return application


app = create_app()
