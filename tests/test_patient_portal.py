import unittest
from datetime import date
from unittest.mock import patch

from app.core.exceptions import AuthenticationError, BusinessRuleError
from app.db.base import Base
from app.models.entities import Patient
from app.models.enums import RoleEnum
from app.schemas.auth import PhoneLoginRequest
from app.schemas.patients import PatientCreate
from app.services.auth_service import AuthService
from app.services.patient_service import PatientService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class TestPatientPortal(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        # Патчим отправку SMS: в тестах нет брокера RabbitMQ
        self._sms_patcher = patch(
            "app.tasks.notifications.send_temp_password_sms.delay",
            lambda *a, **kw: None,
        )
        self._sms_patcher.start()

        async with self.session_factory() as session:
            service = PatientService(session)
            patient = await service.create_patient_with_record(
                PatientCreate(
                    first_name="Анна",
                    last_name="Смирнова",
                    passport_series="4510",
                    passport_number="123456",
                    birth_date=date(1992, 3, 15),
                    gender="F",
                    phone="+79995554433",
                )
            )
            await session.commit()
            self.patient_id = patient.id

    async def asyncTearDown(self) -> None:
        self._sms_patcher.stop()
        await self.engine.dispose()

    async def test_grant_access_creates_user_and_flags_temp_password(self) -> None:
        async with self.session_factory() as session:
            service = PatientService(session)
            temp_password, user = await service.grant_portal_access(
                self.patient_id, "+79995554433"
            )
            await session.commit()

            self.assertEqual(user.role, RoleEnum.PATIENT)
            self.assertTrue(user.must_change_password)
            self.assertIsNotNone(user.temp_password_expires_at)
            self.assertEqual(len(temp_password), 8)

            patient = await session.get(Patient, self.patient_id)
            self.assertEqual(patient.user_id, user.id)

    async def test_patient_login_with_temp_password(self) -> None:
        async with self.session_factory() as session:
            patient_service = PatientService(session)
            temp_password, _ = await patient_service.grant_portal_access(
                self.patient_id, "+79995554433"
            )
            await session.commit()

            auth = AuthService(session)
            tokens = await auth.authenticate_patient(
                PhoneLoginRequest(phone="+79995554433", password=temp_password)
            )
            self.assertIsNotNone(tokens.access_token)
            self.assertTrue(tokens.must_change_password)

    async def test_login_with_wrong_password_rejected(self) -> None:
        async with self.session_factory() as session:
            patient_service = PatientService(session)
            await patient_service.grant_portal_access(self.patient_id, "+79995554433")
            await session.commit()

            auth = AuthService(session)
            with self.assertRaises(AuthenticationError):
                await auth.authenticate_patient(
                    PhoneLoginRequest(phone="+79995554433", password="WRONG123")
                )

    async def test_change_password_clears_flag(self) -> None:
        async with self.session_factory() as session:
            patient_service = PatientService(session)
            temp_password, user = await patient_service.grant_portal_access(
                self.patient_id, "+79995554433"
            )
            await session.commit()

            auth = AuthService(session)
            await auth.change_password(user, temp_password, "NewStrongPass1")
            await session.commit()

            self.assertFalse(user.must_change_password)
            # Вход с новым паролем проходит без флага смены
            tokens = await auth.authenticate_patient(
                PhoneLoginRequest(phone="+79995554433", password="NewStrongPass1")
            )
            self.assertFalse(tokens.must_change_password)

    async def test_change_password_same_rejected(self) -> None:
        async with self.session_factory() as session:
            patient_service = PatientService(session)
            temp_password, user = await patient_service.grant_portal_access(
                self.patient_id, "+79995554433"
            )
            await session.commit()

            auth = AuthService(session)
            with self.assertRaises(BusinessRuleError):
                await auth.change_password(user, temp_password, temp_password)

    async def test_grant_access_without_phone_rejected(self) -> None:
        """Без телефона выдать доступ нельзя — код некуда отправить."""
        async with self.session_factory() as session:
            # Пациент без телефона
            service = PatientService(session)
            patient = await service.create_patient_with_record(
                PatientCreate(
                    first_name="Без",
                    last_name="Телефона",
                    passport_series="9999",
                    passport_number="111111",
                    birth_date=date(1980, 1, 1),
                    gender="M",
                )
            )
            await session.commit()

            with self.assertRaises(BusinessRuleError):
                await service.grant_portal_access(patient.id, "")


if __name__ == "__main__":
    unittest.main()
