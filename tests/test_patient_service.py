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

    @staticmethod
    def _build_dto(
        series: str = "1234",
        number: str = "567890",
        phone: str | None = "+79991112233",
    ) -> PatientCreate:
        return PatientCreate(
            first_name="Иван",
            last_name="Иванов",
            middle_name="Иванович",
            passport_series=series,
            passport_number=number,
            birth_date=date(1985, 5, 20),
            gender="M",
            phone=phone,
            email="ivan@example.com",
        )

    async def test_create_patient_auto_generates_medical_record(self) -> None:
        async with self.session_factory() as session:
            service = PatientService(session)
            patient = await service.create_patient_with_record(self._build_dto())
            await session.commit()

            self.assertIsNotNone(patient.id)
            self.assertEqual(patient.full_name, "Иванов Иван Иванович")
            self.assertEqual(patient.passport, "1234 567890")

            record = await service.get_patient_card(patient.id)
            self.assertIsNotNone(record)
            self.assertTrue(record.record_number.startswith("MED-"))

    async def test_duplicate_passport_raises_conflict(self) -> None:
        """Паспорт — естественный ключ: второй такой пациент невозможен."""
        async with self.session_factory() as session:
            service = PatientService(session)
            await service.create_patient_with_record(self._build_dto())
            await session.commit()

            # Другой человек, другой телефон, но тот же паспорт
            duplicate = self._build_dto(phone="+79990000000")
            with self.assertRaises(ConflictError):
                await service.create_patient_with_record(duplicate)

    async def test_same_phone_allowed_for_different_passports(self) -> None:
        """Одинаковый телефон — не повод отказать: различаем пациентов по паспорту."""
        async with self.session_factory() as session:
            service = PatientService(session)
            first = await service.create_patient_with_record(self._build_dto())
            second = await service.create_patient_with_record(
                self._build_dto(series="9876", number="543210", phone="+79991112233")
            )
            await session.commit()

            self.assertNotEqual(first.id, second.id)
            self.assertEqual(first.phone, second.phone)

    async def test_patient_can_be_created_without_phone(self) -> None:
        async with self.session_factory() as session:
            service = PatientService(session)
            patient = await service.create_patient_with_record(self._build_dto(phone=None))
            await session.commit()

            self.assertIsNone(patient.phone)

    async def test_update_passport_to_existing_raises_conflict(self) -> None:
        async with self.session_factory() as session:
            service = PatientService(session)
            first = await service.create_patient_with_record(self._build_dto())
            second = await service.create_patient_with_record(
                self._build_dto(series="9876", number="543210")
            )
            await session.commit()

            from app.schemas.patients import PatientUpdate

            with self.assertRaises(ConflictError):
                await service.update_patient(
                    second.id,
                    PatientUpdate(
                        passport_series=first.passport_series, passport_number=first.passport_number
                    ),
                )


if __name__ == "__main__":
    unittest.main()
