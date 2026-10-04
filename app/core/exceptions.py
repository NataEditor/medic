from typing import Any


class AppError(Exception):
    """Базовое доменное исключение приложения."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "Внутренняя ошибка сервера"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: Any = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "Объект не найден"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "Конфликт данных"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"
    message = "Некорректные данные"


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthorized"
    message = "Требуется аутентификация"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"
    message = "Недостаточно прав"


class BusinessRuleError(AppError):
    status_code = 400
    code = "business_rule_violation"
    message = "Нарушено бизнес-правило"
