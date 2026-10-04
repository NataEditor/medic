from datetime import UTC, datetime

from app.core.constants import MEDICAL_RECORD_PREFIX


def utcnow() -> datetime:
    """Возвращает текущее время в UTC. Используется как default в моделях вместо лямбд."""
    return datetime.now(UTC)


def build_full_name(
    last_name: str,
    first_name: str,
    middle_name: str | None = None,
) -> str:
    """Единое форматирование ФИО для User, Patient и отчётов."""
    parts = (last_name.strip(), first_name.strip(), middle_name.strip() if middle_name else None)
    return " ".join(p for p in parts if p)


def format_medical_record_number(patient_id: int, year: int | None = None) -> str:
    """Формирует номер электронной медицинской карты: MED-{ГОД}-{ID:06d}."""
    actual_year = year or utcnow().year
    return f"{MEDICAL_RECORD_PREFIX}-{actual_year}-{patient_id:06d}"


def calculate_total_pages(total: int, page_size: int) -> int:
    """Вычисляет общее количество страниц пагинации."""
    if total <= 0 or page_size <= 0:
        return 0
    return (total + page_size - 1) // page_size
