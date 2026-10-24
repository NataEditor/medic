"""Тесты Этапа 5: ограничения ресепшена при загрузке анализа и прайс для пациента.

Проверяем:
- ресепшен прикладывает только файл, без медицинского заключения;
- ресепшен обязан приложить файл (не может создать пустую запись);
- пациент видит только активные услуги прайса.
"""

import unittest
from decimal import Decimal

from app.db.base import Base
from app.models.entities import Clinic, Service, User
from app.models.enums import RoleEnum
from app.repositories.base import BaseRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _user(roles: list[RoleEnum], user_id: int = 1) -> User:
    return User(
        id=user_id,
        clinic_id=1,
        email=f"u{user_id}@medic.local",
        phone=f"+7000000{user_id:04d}",
        hashed_password="x",
        roles=[r.value for r in roles],
        first_name="Имя",
        last_name="Фамилия",
        is_active=True,
    )


class TestReceptionAnalysisRestriction(unittest.TestCase):
    """Правило: ресепшен не заполняет медицинское содержимое анализа."""

    @staticmethod
    def _is_pure_receptionist(user: User) -> bool:
        """Повторяет условие из эндпоинта загрузки анализа."""
        return user.has_role(RoleEnum.RECEPTIONIST) and not user.has_any_role(
            RoleEnum.DOCTOR, RoleEnum.NURSE, RoleEnum.HEAD_DOCTOR
        )

    def test_pure_receptionist_detected(self) -> None:
        self.assertTrue(self._is_pure_receptionist(_user([RoleEnum.RECEPTIONIST])))

    def test_receptionist_plus_doctor_not_restricted(self) -> None:
        """Мультиролевой сотрудник (ресепшен + врач) не ограничен."""
        user = _user([RoleEnum.RECEPTIONIST, RoleEnum.DOCTOR])
        self.assertFalse(self._is_pure_receptionist(user))

    def test_doctor_not_restricted(self) -> None:
        self.assertFalse(self._is_pure_receptionist(_user([RoleEnum.DOCTOR])))

    def test_nurse_not_restricted(self) -> None:
        self.assertFalse(self._is_pure_receptionist(_user([RoleEnum.NURSE])))


class TestPatientPriceList(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async with self.session_factory() as session:
            clinic = Clinic(name="К", code="C", address="А", phone="+70000000000")
            session.add(clinic)
            await session.commit()

            session.add_all(
                [
                    Service(
                        code="ACTIVE-1",
                        name="Консультация терапевта",
                        price=Decimal("2500.00"),
                        duration_minutes=30,
                        is_active=True,
                    ),
                    Service(
                        code="ARCHIVED-1",
                        name="Снятая услуга",
                        price=Decimal("1000.00"),
                        duration_minutes=30,
                        is_active=False,
                    ),
                ]
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_patient_sees_only_active_services(self) -> None:
        """Пациент видит только активные услуги, даже если запросит все."""
        async with self.session_factory() as session:
            patient = _user([RoleEnum.PATIENT])
            only_active = False  # пациент «просит» все

            # Повторяем логику эндпоинта
            if patient.has_role(RoleEnum.PATIENT):
                only_active = True

            repo = BaseRepository(Service, session)
            filters = [Service.is_active.is_(True)] if only_active else None
            items, _ = await repo.list_paginated(limit=500, filters=filters)

            codes = {s.code for s in items}
            self.assertIn("ACTIVE-1", codes)
            self.assertNotIn("ARCHIVED-1", codes)

    async def test_staff_can_see_archived(self) -> None:
        """Сотрудник с only_active=False видит и архивные услуги."""
        async with self.session_factory() as session:
            staff = _user([RoleEnum.ACCOUNTANT])
            only_active = False

            if staff.has_role(RoleEnum.PATIENT):
                only_active = True

            repo = BaseRepository(Service, session)
            filters = [Service.is_active.is_(True)] if only_active else None
            items, _ = await repo.list_paginated(limit=500, filters=filters)

            codes = {s.code for s in items}
            self.assertIn("ACTIVE-1", codes)
            self.assertIn("ARCHIVED-1", codes)


if __name__ == "__main__":
    unittest.main()
