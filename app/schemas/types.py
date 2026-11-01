"""Кастомные типы Pydantic для схем МИС.

Зачем нужен свой EmailStr:
стандартный pydantic.EmailStr отклоняет служебные домены (.local, .test,
.internal, .example), потому что они зарезервированы RFC 2606/6761.
Для МИС это неприемлемо: клиники и разработчики используют внутренние
домены (admin@medic.local, demo@clinic.test), и такие адреса должны
проходить валидацию — иначе bootstrap-админ не сможет войти, а любой
ответ с таким email падает с 500 при сериализации.

Проверяем только структуру адреса (локальная часть + домен с точкой),
не навязывая публичные TLD.
"""

import re
from typing import Annotated

from pydantic import AfterValidator

# Локальная часть: буквы, цифры и . _ % + - (без пробелов и спецсимволов).
# Домен: метки через точку, минимум одна точка, TLD от 2 символов.
_EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+"
    r"@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}$"
)


def _validate_email(value: str) -> str:
    """Проверяет структуру email, не ограничивая домен публичными зонами."""
    if not isinstance(value, str):
        raise ValueError("Email должен быть строкой")

    normalized = value.strip()
    if not normalized:
        raise ValueError("Email не может быть пустым")
    if len(normalized) > 255:
        raise ValueError("Email длиннее 255 символов")
    if not _EMAIL_PATTERN.match(normalized):
        raise ValueError(f"Некорректный email: {normalized}")

    return normalized.lower()


# Тип для полей email: принимает и внутренние домены (.local, .test).
Email = Annotated[str, AfterValidator(_validate_email)]
