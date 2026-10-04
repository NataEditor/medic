from decimal import Decimal

# ----- Пагинация -----
DEFAULT_PAGE: int = 1
DEFAULT_PAGE_SIZE: int = 20
MAX_PAGE_SIZE: int = 100

# ----- Длительность приёмов (в минутах) -----
DEFAULT_APPOINTMENT_DURATION_MINUTES: int = 30
MIN_APPOINTMENT_DURATION_MINUTES: int = 10
MAX_APPOINTMENT_DURATION_MINUTES: int = 240
MAX_SCHEDULE_LOOKAHEAD_HOURS: int = 4

# ----- Финансовые ставки по умолчанию (проценты) -----
DEFAULT_DOCTOR_APPOINTMENT_FEE_PERCENT: Decimal = Decimal("30.00")
DEFAULT_DOCTOR_PROCEDURE_FEE_PERCENT: Decimal = Decimal("15.00")
ONE_HUNDRED_PERCENT: Decimal = Decimal(100)

# ----- Файлы и медиа -----
ALLOWED_ANALYSIS_EXTENSIONS: tuple[str, ...] = (
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tiff",
)

# Скан паспорта принимаем только картинкой jpg — так требует бумажный документооборот
ALLOWED_PASSPORT_SCAN_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg")
# ----- Pillow / Обработка изображений -----
MAX_IMAGE_DIMENSION: int = 2560
THUMBNAIL_SIZE: tuple[int, int] = (400, 400)
THUMBNAIL_JPEG_QUALITY: int = 80
OPTIMIZED_JPEG_QUALITY: int = 85

# ----- Паспорт пациента -----
PASSPORT_SERIES_LENGTH: int = 4
PASSPORT_NUMBER_LENGTH: int = 6
# [0-9], а не \d: в regex-движке pydantic \d совпадает и с нелатинскими цифрами
PASSPORT_SERIES_REGEX_PATTERN: str = r"^[0-9]{4}$"
PASSPORT_NUMBER_REGEX_PATTERN: str = r"^[0-9]{6}$"
PASSPORT_SCAN_DIR_NAME: str = "patients"
PASSPORT_SCAN_FILENAME: str = "passport.jpg"
PASSPORT_SCAN_IMAGE_FORMAT: str = "JPEG"
PASSPORT_SCAN_MEDIA_TYPE: str = "image/jpeg"
PASSPORT_SCAN_URL_TEMPLATE: str = "/api/v1/patients/{patient_id}/passport-scan"

# ----- Форматы и паттерны -----
MEDICAL_RECORD_PREFIX: str = "MED"
GENDER_REGEX_PATTERN: str = r"^[MF]$"
PASSWORD_MIN_LENGTH: int = 8
LOGIN_PASSWORD_MIN_LENGTH: int = 6
ICD10_MAX_LENGTH: int = 20

# ----- Длины строковых полей -----
SHORT_STRING_LENGTH: int = 50
MEDIUM_STRING_LENGTH: int = 100
TITLE_STRING_LENGTH: int = 255
LONG_STRING_LENGTH: int = 500
