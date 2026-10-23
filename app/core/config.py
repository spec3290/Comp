from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./dev.db"
    jwt_secret: str = "dev-only-secret"  # noqa: S105  운영에서는 환경변수로 반드시 교체
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14
