"""Тесты безопасности инфраструктуры: CORS и защита публичных эндпоинтов.

Проверяем:
- логику разбора CORS_ORIGINS и безопасное сочетание с credentials;
- что эндпоинты клиник и списка врачей требуют авторизации.
"""

import unittest
from unittest.mock import patch

from app.core.config import Settings
from app.main import app
from fastapi.testclient import TestClient


class TestCorsSettings(unittest.TestCase):
    def test_parse_origins_list(self) -> None:
        s = Settings(cors_origins="http://a.com, http://b.com ,")
        self.assertEqual(s.cors_origins_list, ["http://a.com", "http://b.com"])

    def test_wildcard_disables_credentials(self) -> None:
        s = Settings(cors_origins="*")
        self.assertFalse(s.cors_allow_credentials)

    def test_specific_origins_allow_credentials(self) -> None:
        s = Settings(cors_origins="http://a.com,http://b.com")
        self.assertTrue(s.cors_allow_credentials)


class TestPublicEndpointsProtected(unittest.TestCase):
    """Без авторизации эти эндпоинты должны возвращать 401/403, а не данные."""

    def setUp(self) -> None:
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_clinics_requires_auth(self) -> None:
        resp = self.client.get("/api/v1/clinics")
        self.assertIn(resp.status_code, (401, 403))

    def test_clinic_by_id_requires_auth(self) -> None:
        resp = self.client.get("/api/v1/clinics/1")
        self.assertIn(resp.status_code, (401, 403))

    def test_doctors_requires_auth(self) -> None:
        resp = self.client.get("/api/v1/users/doctors")
        self.assertIn(resp.status_code, (401, 403))

    def test_health_is_public(self) -> None:
        """Проверка живости должна оставаться открытой для балансировщиков."""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)


class TestCorsHeadersApplied(unittest.TestCase):
    def test_patched_settings_reflected_in_config(self) -> None:
        # Middleware читает настройки один раз при старте приложения,
        # поэтому проверяем саму логику формирования конфигурации CORS.
        with patch("app.core.config.settings.cors_origins", "http://allowed.com"):
            from app.core.config import settings as live_settings

            self.assertEqual(live_settings.cors_origins_list, ["http://allowed.com"])
            self.assertTrue(live_settings.cors_allow_credentials)


class TestProductionSecretValidation(unittest.TestCase):
    """В production приложение не стартует с небезопасными секретами."""

    _STRONG_KEY = "x" * 48

    def test_dev_allows_defaults(self) -> None:
        """В development дефолты допустимы (удобство запуска)."""
        s = Settings(app_env="development")
        self.assertFalse(s.is_production)

    def test_prod_rejects_default_secret_key(self) -> None:
        with self.assertRaises(ValueError):
            Settings(
                app_env="production",
                secret_key="change-me-to-a-very-secret-32-byte-key-medic-mis",
                bootstrap_admin_on_startup=False,
                debug=False,
            )

    def test_prod_rejects_short_secret_key(self) -> None:
        with self.assertRaises(ValueError):
            Settings(
                app_env="production",
                secret_key="short",
                bootstrap_admin_on_startup=False,
                debug=False,
            )

    def test_prod_rejects_default_bootstrap_password(self) -> None:
        with self.assertRaises(ValueError):
            Settings(
                app_env="production",
                secret_key=self._STRONG_KEY,
                bootstrap_admin_on_startup=True,
                bootstrap_admin_password="Admin12345",
                debug=False,
            )

    def test_prod_rejects_debug(self) -> None:
        with self.assertRaises(ValueError):
            Settings(
                app_env="production",
                secret_key=self._STRONG_KEY,
                bootstrap_admin_on_startup=False,
                debug=True,
            )

    def test_prod_accepts_secure_config(self) -> None:
        s = Settings(
            app_env="production",
            secret_key=self._STRONG_KEY,
            bootstrap_admin_on_startup=False,
            debug=False,
        )
        self.assertTrue(s.is_production)


if __name__ == "__main__":
    unittest.main()
