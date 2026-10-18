from datetime import timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import (
    PASSPORT_SCAN_DIR_NAME,
    PASSPORT_SCAN_FILENAME,
    TEMP_PASSWORD_TTL_HOURS,
)
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from app.core.security import hash_password
from app.core.utils import format_medical_record_number, generate_temp_password, utcnow
from app.models.entities import MedicalRecord, Patient, User
from app.models.enums import RoleEnum
from app.repositories.patient_repo import PatientRepository
from app.repositories.user_repo import UserRepository
from app.schemas.patients import PatientCreate, PatientUpdate


class PatientService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.patient_repo = PatientRepository(session)
        self.user_repo = UserRepository(session)

    async def create_patient_with_record(self, data: PatientCreate) -> Patient:
        """Ресепшен регистрирует пациента по паспорту — сразу открывается ЭМК.

        Уникальность определяется парой серия+номер паспорта, а не телефоном:
        один и тот же номер телефона может принадлежать разным людям
        (например, родственникам).
        """
        existing = await self.patient_repo.get_by_passport(
            data.passport_series, data.passport_number
        )
        if existing:
            raise ConflictError(
                f"Пациент с паспортом {data.passport_series} {data.passport_number} "
                f"уже зарегистрирован (карта {existing.id})"
            )

        patient = Patient(
            first_name=data.first_name.strip(),
            last_name=data.last_name.strip(),
            middle_name=data.middle_name.strip() if data.middle_name else None,
            passport_series=data.passport_series,
            passport_number=data.passport_number,
            birth_date=data.birth_date,
            gender=data.gender,
            phone=data.phone.strip() if data.phone else None,
            email=data.email.lower().strip() if data.email else None,
            snils=data.snils.strip() if data.snils else None,
            oms_number=data.oms_number.strip() if data.oms_number else None,
            address=data.address.strip() if data.address else None,
        )
        created_patient = await self.patient_repo.create(patient)

        # Автоматическая генерация номера ЭМК по единому формату
        record_number = format_medical_record_number(created_patient.id)

        medical_record = MedicalRecord(
            patient_id=created_patient.id,
            record_number=record_number,
        )
        self.session.add(medical_record)
        await self.session.flush()

        return created_patient

    async def update_patient(self, patient_id: int, data: PatientUpdate) -> Patient:
        patient = await self.patient_repo.get_by_id(patient_id)
        if not patient:
            raise NotFoundError("Пациент не найден")

        update_dict = data.model_dump(exclude_unset=True)

        # Смена паспорта — новая личность: проверяем, что такой уже нет
        new_series = update_dict.get("passport_series", patient.passport_series)
        new_number = update_dict.get("passport_number", patient.passport_number)
        if (new_series, new_number) != (patient.passport_series, patient.passport_number):
            duplicate = await self.patient_repo.get_by_passport(new_series, new_number)
            if duplicate and duplicate.id != patient.id:
                raise ConflictError(f"Пациент с паспортом {new_series} {new_number} уже существует")

        for key, val in update_dict.items():
            if isinstance(val, str):
                val = val.strip()
            setattr(patient, key, val)

        await self.session.flush()
        await self.session.refresh(patient)
        return patient

    async def get_patient_card(self, patient_id: int) -> MedicalRecord:
        record = await self.patient_repo.get_medical_record(patient_id)
        if not record:
            raise NotFoundError("Медицинская карта не найдена")
        return record

    def build_passport_scan_path(self, patient_id: int) -> Path:
        """Путь к скану паспорта в закрытом хранилище (вне public media)."""
        return (
            Path(settings.private_media_dir)
            / PASSPORT_SCAN_DIR_NAME
            / str(patient_id)
            / PASSPORT_SCAN_FILENAME
        )

    async def attach_passport_scan(self, patient_id: int, content: bytes) -> Patient:
        """Сохраняет скан паспорта и привязывает его к карте пациента.

        Файл кладём в private_media — Nginx это хранилище не раздаёт,
        доступ возможен только через API с проверкой роли.
        """
        patient = await self.patient_repo.get_by_id(patient_id)
        if not patient:
            raise NotFoundError("Пациент не найден")

        max_bytes = settings.max_upload_size_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise ValidationError(f"Файл превышает {settings.max_upload_size_mb} МБ")

        target_path = self.build_passport_scan_path(patient_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)

        patient.passport_scan_path = str(target_path)
        patient.passport_scan_uploaded_at = utcnow()

        await self.session.flush()
        await self.session.refresh(patient)
        return patient

    async def get_passport_scan(self, patient_id: int) -> Path:
        patient = await self.patient_repo.get_by_id(patient_id)
        if not patient:
            raise NotFoundError("Пациент не найден")
        if not patient.passport_scan_path:
            raise NotFoundError("Скан паспорта не загружен")

        scan_path = Path(patient.passport_scan_path)
        if not scan_path.exists():
            raise NotFoundError("Файл скана паспорта отсутствует в хранилище")
        return scan_path

    async def grant_portal_access(self, patient_id: int, login_phone: str) -> tuple[str, User]:
        """Выдаёт пациенту доступ в личный кабинет.

        Создаёт (или обновляет) учётную запись User с ролью PATIENT, генерирует
        временный пароль и помечает его обязательным к смене. Открытый пароль
        возвращается вызывающему для отправки по SMS и в БД не сохраняется.

        Требует наличия телефона — на него уходит код входа.
        """
        patient = await self.patient_repo.get_by_id(patient_id)
        if not patient:
            raise NotFoundError("Пациент не найден")

        phone = login_phone.strip()
        if not phone:
            raise BusinessRuleError("Для доступа в ЛК необходимо указать телефон пациента")

        # Телефон уже занят другим пользователем (не этим пациентом)?
        existing_user = await self.user_repo.get_by_phone(phone)
        if existing_user and patient.user_id != existing_user.id:
            raise ConflictError("Этот телефон уже привязан к другой учётной записи")

        temp_password = generate_temp_password()

        if patient.user_id:
            user = await self.user_repo.get_by_id(patient.user_id)
            if user is None:
                raise NotFoundError("Учётная запись пациента не найдена")
            user.phone = phone
        else:
            # Пациенты входят по телефону; email у них может отсутствовать,
            # поэтому используем служебный (суррогатный) адрес.
            user = User(
                email=f"patient_{patient.id}@medic.local",
                phone=phone,
                hashed_password=hash_password(temp_password),
                role=RoleEnum.PATIENT,
                first_name=patient.first_name,
                last_name=patient.last_name,
                middle_name=patient.middle_name,
                is_active=True,
            )
            user = await self.user_repo.create(user)
            patient.user_id = user.id

        user.hashed_password = hash_password(temp_password)
        user.must_change_password = True
        user.temp_password_expires_at = utcnow() + timedelta(hours=TEMP_PASSWORD_TTL_HOURS)

        await self.session.flush()
        await self.session.refresh(user)

        from app.tasks.notifications import send_temp_password_sms

        send_temp_password_sms.delay(phone, temp_password, TEMP_PASSWORD_TTL_HOURS)

        return temp_password, user
