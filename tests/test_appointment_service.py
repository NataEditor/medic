import unittest
from datetime import UTC, datetime, timedelta

from app.core.exceptions import BusinessRuleError
from app.db.base import Base
from app.models.entities import Clinic, DoctorProfile, Patient, User
from app.models.enums import RoleEnum
from app.schemas.appointments import AppointmentCreate
from app.services.appointment_service import AppointmentService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class TestAppointmentService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async with self.session_factory() as session:
            # Создаём клинику, врача и пациента
            clinic = Clinic(
                name="Центральная клиника",
                code="CENTRAL",
                address="ул. Ленина, 1",
                phone="+74950000000",
            )
            doctor = User(
                email="doc@example.com",
                phone="+79990000001",
                hashed_password="hash",
                role=RoleEnum.DOCTOR,
                first_name="Терапевт",
                last_name="Докторов",
            )
            patient = Patient(
                first_name="Тест",
                last_name="Пациентов",
                birth_date=datetime(1990, 1, 1, tzinfo=UTC).date(),
                gender="M",
                phone="+79990000002",
            )
            session.add_all([clinic, doctor, patient])
            await session.commit()

            profile = DoctorProfile(user_id=doctor.id, specialization="Терапевт")
            session.add(profile)
            await session.commit()

            self.clinic_id = clinic.id
            self.doctor_id = doctor.id
            self.patient_id = patient.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_cannot_book_in_the_past(self) -> None:
        async with self.session_factory() as session:
            service = AppointmentService(session)
            past_time = datetime.now(UTC) - timedelta(days=1)
            dto = AppointmentCreate(
                clinic_id=self.clinic_id,
                doctor_id=self.doctor_id,
                patient_id=self.patient_id,
                scheduled_at=past_time,
            )
            with self.assertRaises(BusinessRuleError):
                await service.create_appointment(dto)

    async def test_cannot_double_book_doctor(self) -> None:
        async with self.session_factory() as session:
            service = AppointmentService(session)
            future_time = datetime.now(UTC) + timedelta(days=2)
            dto = AppointmentCreate(
                clinic_id=self.clinic_id,
                doctor_id=self.doctor_id,
                patient_id=self.patient_id,
                scheduled_at=future_time,
                duration_minutes=30,
            )
            # Первая запись успешна
            await service.create_appointment(dto)
            await session.commit()

            # Вторая запись в то же время вызывает ошибку
            with self.assertRaises(BusinessRuleError):
                await service.create_appointment(dto)


if __name__ == "__main__":
    unittest.main()
