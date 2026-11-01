"""Защита входа от подбора паролей (rate limiting + блокировка).

Счётчик неудачных попыток ведётся по паре «логин + IP»:
- атака с одного адреса не блокирует остальных пользователей;
- подбор одного аккаунта с разных адресов тоже ограничен.

Хранилище выбирается настройкой LOGIN_GUARD_BACKEND:
- "redis"  — общее для всех инстансов приложения. Нужно при горизонтальном
             масштабировании: блокировка, выданная одним подом, действует
             на всех. Ключи живут в Redis с TTL, поэтому сами истекают.
- "memory" — в памяти процесса. Для тестов и локальной разработки без Redis.

Если Redis недоступен, guard автоматически переходит на память процесса
и пишет предупреждение: защита продолжает работать, но только в пределах
одного инстанса. Это осознанный компромисс — падать на логине из-за
недоступного Redis хуже, чем временно потерять общий счётчик.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from app.core.config import settings
from app.core.constants import (
    LOGIN_ATTEMPT_WINDOW_MINUTES,
    LOGIN_LOCKOUT_MINUTES,
    LOGIN_MAX_ATTEMPTS,
)
from app.core.utils import utcnow

logger = logging.getLogger(__name__)


class _Backend(Protocol):
    """Интерфейс хранилища счётчиков."""

    def get_failures(self, key: str) -> int: ...

    def set_failures(self, key: str, failures: int, ttl_seconds: int) -> None: ...

    def get_locked_until(self, key: str) -> datetime | None: ...

    def set_locked_until(self, key: str, until: datetime, ttl_seconds: int) -> None: ...

    def delete(self, key: str) -> None: ...

    def clear(self) -> None: ...


@dataclass
class _MemoryBackend:
    """Хранилище в памяти процесса (fallback и тесты)."""

    _failures: dict[str, tuple[int, datetime]] = field(default_factory=dict)
    _locks: dict[str, datetime] = field(default_factory=dict)

    def get_failures(self, key: str) -> int:
        entry = self._failures.get(key)
        if entry is None:
            return 0
        count, expires_at = entry
        if utcnow() > expires_at:
            self._failures.pop(key, None)
            return 0
        return count

    def set_failures(self, key: str, failures: int, ttl_seconds: int) -> None:
        expires_at = utcnow() + timedelta(seconds=ttl_seconds)
        self._failures[key] = (failures, expires_at)

    def get_locked_until(self, key: str) -> datetime | None:
        until = self._locks.get(key)
        if until is None:
            return None
        if until <= utcnow():
            self._locks.pop(key, None)
            return None
        return until

    def set_locked_until(self, key: str, until: datetime, ttl_seconds: int) -> None:
        self._locks[key] = until

    def delete(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locks.pop(key, None)

    def clear(self) -> None:
        self._failures.clear()
        self._locks.clear()


class _RedisBackend:
    """Хранилище в Redis: общее для всех инстансов приложения.

    Счётчик попыток и метка блокировки — отдельные ключи с TTL.
    TTL выставляется при каждой записи, поэтому «протухшие» записи
    удаляются самим Redis и не занимают память.
    """

    def __init__(self, url: str, prefix: str) -> None:
        import redis

        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._prefix = prefix
        # Проверяем соединение сразу: если Redis недоступен, конструктор
        # бросит исключение, и LoginGuard переключится на память.
        self._client.ping()

    def _k(self, key: str, suffix: str) -> str:
        return f"{self._prefix}:{suffix}:{key}"

    def get_failures(self, key: str) -> int:
        raw = self._client.get(self._k(key, "failures"))
        return int(raw) if raw else 0

    def set_failures(self, key: str, failures: int, ttl_seconds: int) -> None:
        self._client.set(self._k(key, "failures"), failures, ex=ttl_seconds)

    def get_locked_until(self, key: str) -> datetime | None:
        raw = self._client.get(self._k(key, "lock"))
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            # Повреждённое значение не должно ломать вход
            self._client.delete(self._k(key, "lock"))
            return None

    def set_locked_until(self, key: str, until: datetime, ttl_seconds: int) -> None:
        self._client.set(self._k(key, "lock"), until.isoformat(), ex=ttl_seconds)

    def delete(self, key: str) -> None:
        self._client.delete(self._k(key, "failures"), self._k(key, "lock"))

    def clear(self) -> None:
        """Удаляет все ключи guard'а (для тестов)."""
        for pattern in ("failures", "lock"):
            for found in self._client.scan_iter(match=f"{self._prefix}:{pattern}:*"):
                self._client.delete(found)


@dataclass
class LoginGuard:
    """Учёт неудачных попыток входа и временная блокировка.

    Использование:
        login_guard.ensure_not_locked(login, ip)  # проверка перед входом
        ...
        login_guard.register_failure(login, ip)   # после неудачного пароля
        login_guard.reset(login, ip)              # после успешного входа
    """

    max_attempts: int = LOGIN_MAX_ATTEMPTS
    lockout_minutes: int = LOGIN_LOCKOUT_MINUTES
    window_minutes: int = LOGIN_ATTEMPT_WINDOW_MINUTES
    backend: _Backend | None = None

    def __post_init__(self) -> None:
        if self.backend is None:
            self.backend = self._build_backend()

    @staticmethod
    def _build_backend() -> _Backend:
        """Выбирает хранилище по настройке, с откатом на память."""
        if settings.login_guard_backend.lower() != "redis":
            return _MemoryBackend()

        try:
            return _RedisBackend(
                settings.redis_url,
                settings.login_guard_key_prefix,
            )
        except Exception as exc:
            logger.warning(
                "Redis недоступен для LoginGuard (%s). "
                "Счётчики попыток входа хранятся в памяти процесса: "
                "блокировка действует только в пределах одного инстанса.",
                exc,
            )
            return _MemoryBackend()

    @staticmethod
    def _key(login: str, ip: str | None) -> str:
        return f"{(login or '').strip().lower()}|{ip or '-'}"

    def _window_ttl(self) -> int:
        return self.window_minutes * 60

    def _lockout_ttl(self) -> int:
        return self.lockout_minutes * 60

    def is_locked(self, login: str, ip: str | None = None) -> bool:
        """Заблокирован ли вход для этой пары «логин + IP»."""
        assert self.backend is not None
        return self.backend.get_locked_until(self._key(login, ip)) is not None

    def remaining_lockout_seconds(self, login: str, ip: str | None = None) -> int:
        """Сколько секунд осталось до разблокировки (0, если не заблокирован)."""
        assert self.backend is not None
        until = self.backend.get_locked_until(self._key(login, ip))
        if until is None:
            return 0
        return max(0, int((until - utcnow()).total_seconds()))

    def register_failure(self, login: str, ip: str | None = None) -> None:
        """Учесть неудачную попытку; при достижении лимита — заблокировать."""
        assert self.backend is not None
        key = self._key(login, ip)

        failures = self.backend.get_failures(key) + 1
        self.backend.set_failures(key, failures, self._window_ttl())

        if failures >= self.max_attempts:
            until = utcnow() + timedelta(minutes=self.lockout_minutes)
            self.backend.set_locked_until(key, until, self._lockout_ttl())

    def reset(self, login: str, ip: str | None = None) -> None:
        """Сбросить счётчик после успешного входа."""
        assert self.backend is not None
        self.backend.delete(self._key(login, ip))

    def clear(self) -> None:
        """Полная очистка (для тестов)."""
        assert self.backend is not None
        self.backend.clear()


# Единый экземпляр на процесс: счётчики общие для всех запросов.
login_guard = LoginGuard()
