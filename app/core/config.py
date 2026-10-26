from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./dev.db"
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "contest"
    redis_url: str = "redis://127.0.0.1:6380/0"
    jwt_secret: str = ""  # 32바이트 이상 필수. .env 나 환경변수로만 넣는다
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
