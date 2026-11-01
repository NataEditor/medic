"""E2E-тесты на реальном PostgreSQL.

Запускаются только если задан TEST_DATABASE_URL (иначе пропускаются).
Проверяют, что схема и ключевые сценарии работают на PostgreSQL, а не
только на SQLite in-memory, где часть поведения отличается (типы, JSONB,
FOR UPDATE, ограничения).

Запуск:
    TEST_DATABASE_URL=postgresql+asyncpg://medic:medic@localhost:5432/medic_test \
        python -m unittest tests.test_postgres_e2e
"""

import os
import unittest
from datetime import date

from app.db.base import Base
from app.models.entities import Clinic, Patient, User
from app.models.enums import RoleEnum
from app.schemas.patients import PatientCreate
from app.services.patient_service import PatientService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DB_URL = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DB_URL, "TEST_DATABASE_URL не задан — пропуск e2e на PostgreSQL")
class TestPostgresE2E(unittest.IsolatedAsyncioTestCase):
    """Сквозные проверки на реальном PostgreSQL."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(TEST_DB_URL, echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    async def test_schema_creates_all_tables(self) -> None:
        """Схема полностью создаётся на PostgreSQL (все 20 таблиц)."""
        from sqlalchemy import inspect

        async with self.engine.connect() as conn:
            tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        self.assertIn("patients", tables)
        self.assertIn("appointments", tables)
        self.assertIn("audit_logs", tables)
        self.assertGreaterEqual(len(tables), 20)

    async def test_create_patient_with_record(self) -> None:
        """Создание карты пациента и ЭМК на реальной БД."""
        async with self.session_factory() as session:
            clinic = Clinic(name="Клиника", code="C1", address="Адрес", phone="+70000000000")
            session.add(clinic)
            await session.commit()

            patient = await PatientService(session).create_patient_with_record(
                PatientCreate(
                    first_name="Иван",
                    last_name="Тестов",
                    passport_series="1234",
                    passport_number="567890",
                    birth_date=date(1990, 1, 1),
                    gender="M",
                ),
                created_by_clinic_id=clinic.id,
            )
            await session.commit()

            self.assertIsNotNone(patient.id)
            self.assertEqual(patient.created_by_clinic_id, clinic.id)

            record = await PatientService(session).get_patient_card(patient.id)
            self.assertIsNotNone(record)
            self.assertTrue(record.record_number.startswith("MED"))

    async def test_created_by_clinic_id_fk(self) -> None:
        """FK created_by_clinic_id сохраняется и читается на PostgreSQL."""
        async with self.session_factory() as session:
            clinic = Clinic(name="К", code="C2", address="А", phone="+70000000001")
            session.add(clinic)
            await session.commit()

            patient = Patient(
                first_name="Пётр",
                last_name="Второй",
                passport_series="2222",
                passport_number="333333",
                birth_date=date(1985, 5, 5),
                gender="M",
                created_by_clinic_id=clinic.id,
            )
            session.add(patient)
            await session.commit()
            await session.refresh(patient)

            self.assertEqual(patient.created_by_clinic_id, clinic.id)

    async def test_multi_role_user_persisted(self) -> None:
        """Мультиролевость (list[str]) корректно хранится в PostgreSQL."""
        async with self.session_factory() as session:
            user = User(
                clinic_id=None,
                email="multi@medic.local",
                phone="+70000000099",
                hashed_password="x",
                roles=[RoleEnum.DOCTOR.value, RoleEnum.HEAD_DOCTOR.value],
                first_name="Мульти",
                last_name="Роль",
                is_active=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            self.assertTrue(user.has_role(RoleEnum.DOCTOR))
            self.assertTrue(user.has_role(RoleEnum.HEAD_DOCTOR))
            self.assertEqual(len(user.roles), 2)


if __name__ == "__main__":
    unittest.main()
