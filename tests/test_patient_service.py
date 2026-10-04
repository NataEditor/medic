import unittest
from datetime import date

from app.core.exceptions import ConflictError
from app.db.base import Base
from app.schemas.patients import PatientCreate
from app.services.patient_service import PatientService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class TestPatientService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_create_patient_auto_generates_medical_record(self) -> None:
        async with self.session_factory() as session:
            service = PatientService(session)
            dto = PatientCreate(
                first_name="Иван",
                last_name="Иванов",
                middle_name="Иванович",
                birth_date=date(1985, 5, 20),
                gender="M",
                phone="+79991112233",
                email="ivan@example.com",
            )
            patient = await service.create_patient_with_record(dto)
            await session.commit()

            self.assertIsNotNone(patient.id)
            self.assertEqual(patient.full_name, "Иванов Иван Иванович")

            record = await service.get_patient_card(patient.id)
            self.assertIsNotNone(record)
            self.assertTrue(record.record_number.startswith("MED-"))

    async def test_duplicate_phone_raises_conflict(self) -> None:
        async with self.session_factory() as session:
            service = PatientService(session)
            dto = PatientCreate(
                first_name="Пётр",
                last_name="Петров",
                birth_date=date(1990, 1, 1),
                gender="M",
                phone="+79998887766",
            )
            await service.create_patient_with_record(dto)
            await session.commit()

            with self.assertRaises(ConflictError):
                await service.create_patient_with_record(dto)


if __name__ == "__main__":
    unittest.main()
