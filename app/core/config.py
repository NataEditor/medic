from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения, читаются из переменных окружения и файла .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "MEDIC"
    app_env: str = "development"
    debug: bool = True
    log_level: str = "INFO"

    secret_key: str = "change-me-to-a-very-secret-32-byte-key-medic-mis"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14

    postgres_user: str = "medic"
    postgres_password: str = "medic"
    postgres_db: str = "medic"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    redis_url: str = "redis://localhost:6379/0"

    rabbitmq_user: str = "medic"
    rabbitmq_password: str = "medic"
    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = 5672

    media_dir: str = "media"
    # Закрытое хранилище (паспорта и др. ПДн): Nginx его не отдаёт, доступ только через API
    private_media_dir: str = "private_media"
    max_upload_size_mb: int = Field(default=10, ge=1, le=100)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """DSN для асинхронного драйвера asyncpg."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """DSN для синхронного драйвера — нужен Alembic и Celery."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rabbitmq_url(self) -> str:
        return (
            f"amqp://{self.rabbitmq_user}:{self.rabbitmq_password}"
            f"@{self.rabbitmq_host}:{self.rabbitmq_port}//"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
