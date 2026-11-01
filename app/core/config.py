from functools import lru_cache

from pydantic import Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Значения по умолчанию, недопустимые в production (защита от деплоя «как есть»).
_INSECURE_SECRET_KEY = "change-me-to-a-very-secret-32-byte-key-medic-mis"
_INSECURE_BOOTSTRAP_PASSWORD = "Admin12345"
# Минимальная длина секретного ключа (RFC 7518 для HS256).
_MIN_SECRET_KEY_BYTES = 32
# Окружения, считающиеся production.
_PRODUCTION_ENVS = frozenset({"production", "prod"})


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
    # Отдельный флаг для логирования SQL-запросов (не завязан на DEBUG).
    sql_echo: bool = False

    secret_key: str = _INSECURE_SECRET_KEY
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14

    postgres_user: str = "medic"
    postgres_password: str = "medic"
    postgres_db: str = "medic"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    redis_url: str = "redis://localhost:6379/0"
    # Хранилище счётчиков неудачных входов.
    # "redis" — общее для всех инстансов (нужно при горизонтальном
    # масштабировании), "memory" — в памяти процесса (для тестов и
    # локальной разработки без Redis).
    login_guard_backend: str = "redis"
    # Префикс ключей в Redis, чтобы не пересекаться с Celery и кэшем.
    login_guard_key_prefix: str = "medic:login_guard"

    rabbitmq_user: str = "medic"
    rabbitmq_password: str = "medic"
    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = 5672

    media_dir: str = "media"
    # Закрытое хранилище (паспорта и др. ПДн): Nginx его не отдаёт, доступ только через API
    private_media_dir: str = "private_media"
    max_upload_size_mb: int = Field(default=10, ge=1, le=100)

    # --- Финансовые правила ---
    # Требовать оплату счёта до выполнения процедуры.
    # Для клиник с пакетной предоплатой можно отключить (false).
    require_payment_before_procedure: bool = True

    # --- CORS ---
    # Список разрешённых источников фронтенда через запятую.
    # Пример: CORS_ORIGINS=http://localhost:3000,https://mis.example.com
    # "*" разрешает все источники, но тогда cookie/credentials работать не будут.
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # --- Bootstrap первого супер-админа ---
    # Если в базе нет ни одного администратора, он создаётся при старте из этих значений.
    # Задаются через .env: BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_PASSWORD и т.д.
    bootstrap_admin_email: str = "admin@medic.local"
    bootstrap_admin_password: str = _INSECURE_BOOTSTRAP_PASSWORD
    bootstrap_admin_phone: str = "+70000000000"
    bootstrap_admin_first_name: str = "Super"
    bootstrap_admin_last_name: str = "Admin"
    # Автосоздание админа при старте приложения (в prod ставьте false и создавайте через CLI)
    bootstrap_admin_on_startup: bool = True

    @property
    def is_production(self) -> bool:
        """True, если приложение запущено в production-окружении."""
        return self.app_env.strip().lower() in _PRODUCTION_ENVS

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        """Запрещает небезопасные секреты в production.

        В dev/test дефолты допустимы (удобство запуска), но в prod
        приложение не должно стартовать с ключом-заглушкой или слабым
        паролем bootstrap-админа — это прямой риск компрометации.
        """
        if not self.is_production:
            return self

        errors: list[str] = []

        if self.secret_key == _INSECURE_SECRET_KEY:
            errors.append(
                "SECRET_KEY не изменён (используется значение по умолчанию). "
                'Задайте уникальный ключ: python -c "import secrets; '
                'print(secrets.token_urlsafe(48))"'
            )
        elif len(self.secret_key.encode("utf-8")) < _MIN_SECRET_KEY_BYTES:
            errors.append(
                f"SECRET_KEY короче {_MIN_SECRET_KEY_BYTES} байт — "
                "недостаточно для HS256 (RFC 7518)."
            )

        if (
            self.bootstrap_admin_on_startup
            and self.bootstrap_admin_password == _INSECURE_BOOTSTRAP_PASSWORD
        ):
            errors.append(
                "BOOTSTRAP_ADMIN_PASSWORD не изменён. Задайте надёжный пароль "
                "или отключите BOOTSTRAP_ADMIN_ON_STARTUP и создайте админа через CLI."
            )

        if self.debug:
            errors.append("DEBUG=true недопустим в production (утечка деталей ошибок).")

        if errors:
            raise ValueError("Небезопасная конфигурация для production:\n- " + "\n- ".join(errors))
        return self

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

    @property
    def cors_origins_list(self) -> list[str]:
        """Разбирает CORS_ORIGINS в список источников без пробелов."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def cors_allow_credentials(self) -> bool:
        """Cookie/credentials нельзя сочетать с wildcard-источником '*'.

        Браузеры отклоняют такой ответ, поэтому при '*' принудительно
        отключаем credentials — это исключает небезопасную конфигурацию.
        """
        return "*" not in self.cors_origins_list


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
