import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.db.base import Base
from app.schemas.patients import PatientCreate
from app.services.patient_service import PatientService
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class TestPatientPassportScan(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self._orig_private = settings.private_media_dir
        # Перенаправляем private_media во временную папку на время теста
        settings.private_media_dir = self.temp_dir.name

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        # Создаём тестового пациента
        async with self.session_factory() as session:
            service = PatientService(session)
            self.patient = await service.create_patient_with_record(
                PatientCreate(
                    first_name="Анна",
                    last_name="Смирнова",
                    passport_series="4510",
                    passport_number="123456",
                    birth_date=date(1992, 3, 15),
                    gender="F",
                    email="anna@example.com",
                )
            )
            await session.commit()
            self.patient_id = self.patient.id

    async def asyncTearDown(self) -> None:
        settings.private_media_dir = self._orig_private
        self.temp_dir.cleanup()
        await self.engine.dispose()

    def _generate_test_jpeg(self) -> bytes:
        img = Image.new("RGB", (200, 300), color="white")
        buf = Path(self.temp_dir.name) / "temp_passport.jpg"
        img.save(buf, "JPEG")
        return buf.read_bytes()

    async def test_attach_and_retrieve_passport_scan(self) -> None:
        jpeg_bytes = self._generate_test_jpeg()

        async with self.session_factory() as session:
            service = PatientService(session)
            updated = await service.attach_passport_scan(self.patient_id, jpeg_bytes)
            await session.commit()

            self.assertIsNotNone(updated.passport_scan_path)
            self.assertIsNotNone(updated.passport_scan_uploaded_at)
            self.assertTrue(Path(updated.passport_scan_path).exists())

        async with self.session_factory() as session:
            service = PatientService(session)
            scan_path = await service.get_passport_scan(self.patient_id)
            self.assertTrue(scan_path.exists())
            self.assertEqual(scan_path.read_bytes(), jpeg_bytes)

    async def test_get_scan_when_not_uploaded_raises_not_found(self) -> None:
        async with self.session_factory() as session:
            service = PatientService(session)
            with self.assertRaises(NotFoundError):
                await service.get_passport_scan(self.patient_id)

    async def test_oversized_scan_raises_validation_error(self) -> None:
        too_big = b"x" * ((settings.max_upload_size_mb * 1024 * 1024) + 1)
        async with self.session_factory() as session:
            service = PatientService(session)
            with self.assertRaises(ValidationError):
                await service.attach_passport_scan(self.patient_id, too_big)


if __name__ == "__main__":
    unittest.main()
