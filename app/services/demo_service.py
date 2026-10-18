"""Наполнение базы демонстрационными данными.

Используется для быстрого развёртывания стенда и ручного тестирования:
создаёт клинику, врача с профилем и графиком, пациента с ЭМК и каталог услуг.

Сервис идемпотентен: повторный запуск не создаёт дубликатов — уже
существующие сущности переиспользуются, а недостающие досоздаются.
"""

from datetime import date, time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.core.security import hash_password
from app.models.entities import (
    Clinic,
    DoctorProfile,
    DoctorSchedule,
    Patient,
    Service,
    User,
)
from app.models.enums import RoleEnum
from app.repositories.patient_repo import PatientRepository
from app.repositories.user_repo import UserRepository

# Демо-пароль для всех создаваемых учётных записей (только для стенда!)
DEMO_PASSWORD = "Demo12345"

# Рабочие дни врача: понедельник–пятница (0..4)
_WORKDAYS: tuple[int, ...] = (0, 1, 2, 3, 4)


class DemoSeedService:
    """Создаёт демонстрационный набор данных в базе."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.user_repo = UserRepository(session)
        self.patient_repo = PatientRepository(session)

    async def seed(self) -> dict[str, int]:
        """Наполнить базу демо-данными и вернуть сводку по созданным сущностям."""
        clinic = await self._ensure_clinic()
        doctor = await self._ensure_doctor(clinic)
        await self._ensure_schedule(doctor, clinic)
        patient = await self._ensure_patient()
        await self._ensure_services()

        summary = {
            "clinic_id": clinic.id,
            "doctor_id": doctor.id,
            "patient_id": patient.id,
        }
        logger.info("Демо-данные созданы: {}", summary)
        return summary

    async def _ensure_clinic(self) -> Clinic:
        """Клиника по умолчанию: переиспользуем существующую или создаём новую."""
        existing = (await self.session.execute(select(Clinic).limit(1))).scalars().first()
        if existing:
            return existing

        clinic = Clinic(
            name="Демо-клиника «Здоровье»",
            code="DEMO",
            address="г. Москва, ул. Демонстрационная, д. 1",
            phone="+74950000000",
            email="demo@medic.local",
        )
        self.session.add(clinic)
        await self.session.flush()
        await self.session.refresh(clinic)
        return clinic

    async def _ensure_doctor(self, clinic: Clinic) -> User:
        """Врач-терапевт с профилем. Переиспользуем, если email уже занят."""
        email = "doctor@medic.local"
        existing = await self.user_repo.get_by_email(email)
        if existing:
            return existing

        doctor = User(
            clinic_id=clinic.id,
            email=email,
            phone="+74950000001",
            hashed_password=hash_password(DEMO_PASSWORD),
            role=RoleEnum.DOCTOR,
            first_name="Иван",
            last_name="Петров",
            middle_name="Сергеевич",
            is_active=True,
        )
        created = await self.user_repo.create(doctor)

        profile = DoctorProfile(
            user_id=created.id,
            specialization="Терапевт",
            cabinet_number="101",
        )
        await self.user_repo.create_doctor_profile(profile)
        return created

    async def _ensure_schedule(self, doctor: User, clinic: Clinic) -> None:
        """График приёма врача: будни 09:00–18:00 с обедом 13:00–14:00."""
        existing = (
            await self.session.execute(
                select(DoctorSchedule).where(DoctorSchedule.doctor_id == doctor.id).limit(1)
            )
        ).scalars().first()
        if existing:
            return

        for weekday in _WORKDAYS:
            self.session.add(
                DoctorSchedule(
                    doctor_id=doctor.id,
                    clinic_id=clinic.id,
                    weekday=weekday,
                    start_time=time(9, 0),
                    end_time=time(18, 0),
                    break_start=time(13, 0),
                    break_end=time(14, 0),
                    cabinet_number="101",
                    is_active=True,
                )
            )
        await self.session.flush()

    async def _ensure_patient(self) -> Patient:
        """Пациент с открытой ЭМК. Переиспользуем по паспорту, если уже есть."""
        series, number = "4509", "123456"
        existing = await self.patient_repo.get_by_passport(series, number)
        if existing:
            return existing

        from app.schemas.patients import PatientCreate
        from app.services.patient_service import PatientService

        service = PatientService(self.session)
        return await service.create_patient_with_record(
            PatientCreate(
                first_name="Анна",
                last_name="Смирнова",
                middle_name="Викторовна",
                passport_series=series,
                passport_number=number,
                birth_date=date(1990, 5, 17),
                gender="F",
                phone="+74950000002",
                email="patient@example.com",            )
        )

    async def _ensure_services(self) -> None:
        """Базовый прайс-лист: консультация и типовые процедуры."""
        catalog = (
            ("CONS-THER", "Консультация терапевта", Decimal("2500.00"), 30, False),
            ("PROC-INJ", "Внутримышечная инъекция", Decimal("500.00"), 15, True),
            ("PROC-DRIP", "Внутривенная капельница", Decimal("1500.00"), 40, True),
            ("LAB-CBC", "Общий анализ крови", Decimal("800.00"), 10, True),
        )
        for code, name, price, duration, is_procedure in catalog:
            exists = (
                await self.session.execute(select(Service).where(Service.code == code).limit(1))
            ).scalars().first()
            if exists:
                continue
            self.session.add(
                Service(
                    code=code,
                    name=name,
                    price=price,
                    duration_minutes=duration,
                    is_procedure=is_procedure,
                    is_active=True,
                )
            )
        await self.session.flush()
