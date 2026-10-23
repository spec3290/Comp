from fastapi import FastAPI

from app.health import router as health_router


def create_app() -> FastAPI:
    application = FastAPI(title="Contest Collector")
    application.include_router(health_router)
    return application


app = create_app()
