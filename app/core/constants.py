from decimal import Decimal

# ----- Пагинация -----
DEFAULT_PAGE: int = 1
DEFAULT_PAGE_SIZE: int = 20
MAX_PAGE_SIZE: int = 100

# ----- Длительность приёмов (в минутах) -----
# Стандартный приём 20-30 минут: шаг сетки расписания и дефолтная длительность.
DEFAULT_APPOINTMENT_DURATION_MINUTES: int = 30
MIN_APPOINTMENT_DURATION_MINUTES: int = 20
MAX_APPOINTMENT_DURATION_MINUTES: int = 240
MAX_SCHEDULE_LOOKAHEAD_HOURS: int = 4
# Пациент может перенести/отменить свой приём не позднее чем за N часов.
PATIENT_CANCEL_LIMIT_HOURS: int = 24

# ----- График работы врачей -----
# Рабочий день по умолчанию: смена и обеденный перерыв (по шаблону дня недели).
DEFAULT_SHIFT_START_HOUR: int = 9
DEFAULT_SHIFT_END_HOUR: int = 18
DEFAULT_BREAK_START_HOUR: int = 13
DEFAULT_BREAK_END_HOUR: int = 14
# Минимальный шаг сетки записи (кратность слота).
SLOT_STEP_MINUTES: int = 10

# ----- Финансовые ставки по умолчанию (проценты) -----
DEFAULT_DOCTOR_APPOINTMENT_FEE_PERCENT: Decimal = Decimal("30.00")
DEFAULT_DOCTOR_PROCEDURE_FEE_PERCENT: Decimal = Decimal("15.00")
ONE_HUNDRED_PERCENT: Decimal = Decimal(100)

# ----- Защита кассы -----
# Финансовые проводки неизменяемы: их нельзя удалять или править никому,
# включая супер-админа. Единственная допустимая операция — контролируемый возврат,
# который создаётся отдельной записью и фиксируется в журнале аудита.
CASH_AUDIT_ENTITY: str = "payment"
ACTION_REFUND_PAYMENT: str = "refund_payment"

# ----- Аудит и защита ПДн (152-ФЗ) -----
# Тип события «просмотр персональных данных»: фиксируется при каждом
# открытии ЭМК или скана паспорта. Нужен для разбора инцидентов.
ACTION_VIEW_PDN: str = "VIEW_PDN"
# Псевдонимы конкретных просмотров (для фильтрации в журнале).
ACTION_VIEW_MEDICAL_RECORD: str = "view_medical_record"
ACTION_VIEW_PASSPORT_SCAN: str = "view_passport_scan"

# ----- Защита от подбора паролей (rate limiting) -----
# После N неудачных попыток вход блокируется на LOCKOUT_MINUTES минут.
# Счётчик ведётся по паре «логин + IP», чтобы атака с одного адреса
# не блокировала всех пользователей, и наоборот.
LOGIN_MAX_ATTEMPTS: int = 5
LOGIN_LOCKOUT_MINUTES: int = 15
# Окно, в котором считаются попытки (после него счётчик сбрасывается).
LOGIN_ATTEMPT_WINDOW_MINUTES: int = 15

# ----- Личный кабинет пациента -----
# Срок жизни временного пароля, отправленного пациенту по SMS.
TEMP_PASSWORD_TTL_HOURS: int = 24
TEMP_PASSWORD_LENGTH: int = 8  # ----- Файлы и медиа -----
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
