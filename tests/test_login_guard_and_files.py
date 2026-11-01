"""Тесты Приоритета F2: защита входа и закрытое хранилище файлов.

Проверяем:
- после N неудачных попыток пара «логин + IP» блокируется;
- успешный вход сбрасывает счётчик;
- блокировка одного логина не влияет на других;
- файлы анализов лежат в закрытом хранилище, а не в публичном media;
- Nginx не раздаёт /media/ напрямую.
"""

import unittest
from pathlib import Path

from app.core.constants import (
    LOGIN_LOCKOUT_MINUTES,
    LOGIN_MAX_ATTEMPTS,
)
from app.services.login_guard import LoginGuard, _MemoryBackend


class TestLoginGuard(unittest.TestCase):
    def setUp(self) -> None:
        # Явно memory-backend: тесты не должны зависеть от запущенного Redis
        self.guard = LoginGuard(backend=_MemoryBackend())

    def test_not_locked_initially(self) -> None:
        self.assertFalse(self.guard.is_locked("user@medic.local", "10.0.0.1"))

    def test_locks_after_max_attempts(self) -> None:
        login, ip = "user@medic.local", "10.0.0.1"
        for _ in range(LOGIN_MAX_ATTEMPTS - 1):
            self.guard.register_failure(login, ip)
        self.assertFalse(self.guard.is_locked(login, ip))

        self.guard.register_failure(login, ip)
        self.assertTrue(self.guard.is_locked(login, ip))

    def test_lockout_has_remaining_time(self) -> None:
        login, ip = "user@medic.local", "10.0.0.1"
        for _ in range(LOGIN_MAX_ATTEMPTS):
            self.guard.register_failure(login, ip)

        remaining = self.guard.remaining_lockout_seconds(login, ip)
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, LOGIN_LOCKOUT_MINUTES * 60)

    def test_successful_login_resets_counter(self) -> None:
        login, ip = "user@medic.local", "10.0.0.1"
        for _ in range(LOGIN_MAX_ATTEMPTS - 1):
            self.guard.register_failure(login, ip)

        self.guard.reset(login, ip)
        self.assertFalse(self.guard.is_locked(login, ip))

        # После сброса снова доступны все попытки
        for _ in range(LOGIN_MAX_ATTEMPTS - 1):
            self.guard.register_failure(login, ip)
        self.assertFalse(self.guard.is_locked(login, ip))

    def test_lockout_is_per_login_and_ip(self) -> None:
        """Блокировка одного логина не мешает другому пользователю."""
        for _ in range(LOGIN_MAX_ATTEMPTS):
            self.guard.register_failure("attacker@medic.local", "10.0.0.1")

        self.assertTrue(self.guard.is_locked("attacker@medic.local", "10.0.0.1"))
        self.assertFalse(self.guard.is_locked("doctor@medic.local", "10.0.0.1"))
        self.assertFalse(self.guard.is_locked("attacker@medic.local", "10.0.0.2"))

    def test_login_is_case_insensitive(self) -> None:
        for _ in range(LOGIN_MAX_ATTEMPTS):
            self.guard.register_failure("User@Medic.Local", "10.0.0.1")
        self.assertTrue(self.guard.is_locked("user@medic.local", "10.0.0.1"))

    def test_clear_resets_all(self) -> None:
        for _ in range(LOGIN_MAX_ATTEMPTS):
            self.guard.register_failure("user@medic.local", "10.0.0.1")
        self.guard.clear()
        self.assertFalse(self.guard.is_locked("user@medic.local", "10.0.0.1"))


def _redis_available() -> bool:
    """Есть ли живой Redis для интеграционного теста."""
    try:
        import redis
        from app.core.config import settings

        redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1).ping()
        return True
    except Exception:
        return False


@unittest.skipUnless(_redis_available(), "Redis не запущен — интеграционный тест пропущен")
class TestLoginGuardRedis(unittest.TestCase):
    """Счётчики в Redis: общее состояние для всех инстансов приложения."""

    def setUp(self) -> None:
        from app.core.config import settings
        from app.services.login_guard import _RedisBackend

        self.backend = _RedisBackend(
            settings.redis_url,
            f"{settings.login_guard_key_prefix}:test",
        )
        self.backend.clear()
        self.guard = LoginGuard(backend=self.backend)

    def tearDown(self) -> None:
        self.backend.clear()

    def test_locks_after_max_attempts(self) -> None:
        login, ip = "redis_user@medic.local", "10.0.0.1"
        for _ in range(LOGIN_MAX_ATTEMPTS):
            self.guard.register_failure(login, ip)
        self.assertTrue(self.guard.is_locked(login, ip))

    def test_state_is_shared_between_instances(self) -> None:
        """Второй экземпляр guard'а видит блокировку, выданную первым."""
        from app.core.config import settings
        from app.services.login_guard import _RedisBackend

        login, ip = "shared@medic.local", "10.0.0.5"
        for _ in range(LOGIN_MAX_ATTEMPTS):
            self.guard.register_failure(login, ip)

        other = LoginGuard(
            backend=_RedisBackend(
                settings.redis_url,
                f"{settings.login_guard_key_prefix}:test",
            )
        )
        self.assertTrue(other.is_locked(login, ip))

    def test_reset_clears_redis_keys(self) -> None:
        login, ip = "reset@medic.local", "10.0.0.7"
        for _ in range(LOGIN_MAX_ATTEMPTS - 1):
            self.guard.register_failure(login, ip)
        self.guard.reset(login, ip)
        self.assertFalse(self.guard.is_locked(login, ip))
        self.assertEqual(self.backend.get_failures(self.guard._key(login, ip)), 0)


class TestPrivateMediaStorage(unittest.TestCase):
    """Файлы медицинских документов не должны лежать в публичном каталоге."""

    def test_analyses_upload_uses_private_dir(self) -> None:
        source = Path("app/api/v1/analyses.py").read_text(encoding="utf-8")
        self.assertIn("settings.private_media_dir", source)
        # Публичный media_dir для анализов больше не используется
        self.assertNotIn('Path(settings.media_dir) / "analyses"', source)

    def test_nginx_does_not_serve_media_directly(self) -> None:
        """Nginx не должен отдавать /media/ через alias — только через API."""
        config = Path("nginx/nginx.conf").read_text(encoding="utf-8")
        self.assertNotIn("alias /var/www/media/", config)
        self.assertIn("location /media/", config)

    def test_analysis_file_endpoint_exists(self) -> None:
        source = Path("app/api/v1/analyses.py").read_text(encoding="utf-8")
        self.assertIn("/{analysis_id}/file", source)
        self.assertIn("record_pdn_view", source)


if __name__ == "__main__":
    unittest.main()
