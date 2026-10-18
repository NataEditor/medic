import unittest
from unittest.mock import patch

from app.core.exceptions import ConflictError
from app.db.base import Base
from app.models.entities import Clinic, User
from app.models.enums import RoleEnum
from app.services.bootstrap_service import BootstrapService
from app.services.demo_service import DemoSeedService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class TestBootstrapService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_create_super_admin(self) -> None:
        async with self.session_factory() as session:
            service = BootstrapService(session)
            admin = await service.create_super_admin(
                email="root@medic.local",
                password="StrongPass1",
                phone="+70000000001",
                first_name="Root",
                last_name="Admin",
            )
            await session.commit()

            self.assertEqual(admin.role, RoleEnum.SUPER_ADMIN)
            self.assertTrue(admin.is_active)
            self.assertTrue(await service.has_any_admin())

    async def test_duplicate_email_rejected(self) -> None:
        async with self.session_factory() as session:
            service = BootstrapService(session)
            await service.create_super_admin(
                email="root@medic.local",
                password="StrongPass1",
                phone="+70000000001",
                first_name="Root",
                last_name="Admin",
            )
            await session.commit()

            with self.assertRaises(ConflictError):
                await service.create_super_admin(
                    email="root@medic.local",
                    password="StrongPass2",
                    phone="+70000000002",
                    first_name="Other",
                    last_name="Admin",
                )

    async def test_ensure_super_admin_skips_when_admin_exists(self) -> None:
        async with self.session_factory() as session:
            service = BootstrapService(session)
            await service.create_super_admin(
                email="root@medic.local",
                password="StrongPass1",
                phone="+70000000001",
                first_name="Root",
                last_name="Admin",
            )
            await session.commit()

            # Админ уже есть -> повторное создание не происходит
            created = await service.ensure_super_admin_on_startup()
            self.assertIsNone(created)

    async def test_ensure_super_admin_creates_from_settings(self) -> None:
        async with self.session_factory() as session:
            service = BootstrapService(session)
            with patch("app.services.bootstrap_service.settings") as mock_settings:
                mock_settings.bootstrap_admin_on_startup = True
                mock_settings.bootstrap_admin_email = "boot@medic.local"
                mock_settings.bootstrap_admin_password = "BootPass12"
                mock_settings.bootstrap_admin_phone = "+70000000009"
                mock_settings.bootstrap_admin_first_name = "Boot"
                mock_settings.bootstrap_admin_last_name = "Admin"

                created = await service.ensure_super_admin_on_startup()
                await session.commit()

            self.assertIsNotNone(created)
            self.assertEqual(created.email, "boot@medic.local")

    async def test_ensure_default_clinic_is_idempotent(self) -> None:
        async with self.session_factory() as session:
            service = BootstrapService(session)
            first = await service.ensure_default_clinic()
            await session.commit()
            second = await service.ensure_default_clinic()
            self.assertEqual(first.id, second.id)

            # В базе ровно одна клиника
            from sqlalchemy import func, select

            count = int((await session.execute(select(func.count(Clinic.id)))).scalar_one())
            self.assertEqual(count, 1)


class TestDemoSeedService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_seed_creates_data_idempotently(self) -> None:
        async with self.session_factory() as session:
            service = DemoSeedService(session)
            summary = await service.seed()
            await session.commit()

            self.assertIn("clinic_id", summary)
            self.assertIn("doctor_id", summary)

            # Повторный запуск не должен падать и не создавать дубли
            summary2 = await service.seed()
            await session.commit()
            self.assertEqual(summary["clinic_id"], summary2["clinic_id"])
            self.assertEqual(summary["doctor_id"], summary2["doctor_id"])

            from sqlalchemy import func, select

            doctors = int(
                (
                    await session.execute(
                        select(func.count(User.id)).where(User.role == RoleEnum.DOCTOR)
                    )
                ).scalar_one()
            )
            self.assertEqual(doctors, 1)


if __name__ == "__main__":
    unittest.main()
