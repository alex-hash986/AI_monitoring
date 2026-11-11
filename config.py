from functools import lru_cache
from pathlib import Path

from pydantic import Field, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


DEMO_DATABASE = Path(__file__).resolve().with_name("monitoring_demo.db")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "monitoring"
    postgres_user: str = "monitoring"
    postgres_password: SecretStr | None = None

    celery_broker_url: RedisDsn = RedisDsn("redis://localhost:6379/0")
    celery_result_backend: RedisDsn = RedisDsn("redis://localhost:6379/1")
    celery_worker_concurrency: int = Field(default=2, ge=1)

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.4-mini"
    demo_mode: bool = False
    local_demo: bool = True
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    http_timeout_seconds: float = Field(default=20, gt=0)

    @model_validator(mode="after")
    def configure_local_demo(self):
        if not self.postgres_password:
            self.local_demo = True
        if self.local_demo:
            self.demo_mode = True
            self.openai_api_key = None
            self.telegram_bot_token = None
            self.telegram_chat_id = None
        return self

    @property
    def database_url(self) -> URL:
        if self.local_demo or not self.postgres_password:
            return URL.create("sqlite+pysqlite", database=str(DEMO_DATABASE))
        return URL.create(
            "postgresql+psycopg2",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
