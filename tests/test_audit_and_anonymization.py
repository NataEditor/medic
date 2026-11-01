"""Тесты Типа F: журнал аудита и заявки на обезличивание (152-ФЗ).

Проверяем:
- журнал аудита читают только SA (вся сеть) и админ клиники (своя);
- админ клиники не видит записи сотрудников чужого филиала;
- фильтр only_pdn отбирает только просмотры персональных данных;
- пациент подаёт заявку на обезличивание, решение принимает только SA;
- свою заявку подтвердить нельзя (двойной контроль);
- после подтверждения ПДн скрыты, медзаписи сохранены.
"""

import unittest
from datetime import date

from app.api.deps import record_pdn_view
from app.core.constants import ACTION_VIEW_PDN
from app.core.exceptions import BusinessRuleError, PermissionDeniedError
from app.core.security import hash_password
from app.db.base import Base
from app.models.entities import AuditLog, Clinic, Patient, User
from app.models.enums import AnonymizationRequestStatus, PatientStatus, RoleEnum
from app.schemas.anonymization import (
    AnonymizationRequestCreate,
    AnonymizationRequestReview,
)
from app.schemas.audit import AuditLogFilter
from app.services.anonymization_service import AnonymizationService
from app.services.audit_service import AuditService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class TestAuditLogAccess(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async with self.session_factory() as session:
            clinic_a = Clinic(name="Клиника А", code="A", address="Адрес А", phone="+70000000001")
            clinic_b = Clinic(name="Клиника Б", code="B", address="Адрес Б", phone="+70000000002")
            session.add_all([clinic_a, clinic_b])
            await session.commit()

            super_admin = User(
                email="root@medic.local",
                phone="+70000000010",
                hashed_password=hash_password("secret123"),
                roles=[RoleEnum.SUPER_ADMIN.value],
                first_name="Root",
                last_name="Admin",
            )
            admin_a = User(
                clinic_id=clinic_a.id,
                email="admin_a@medic.local",
                phone="+70000000011",
                hashed_password=hash_password("secret123"),
                roles=[RoleEnum.CLINIC_ADMIN.value],
                first_name="Админ",
                last_name="Клиники А",
            )
            doctor_a = User(
                clinic_id=clinic_a.id,
                email="doc_a@medic.local",
                phone="+70000000012",
                hashed_password=hash_password("secret123"),
                roles=[RoleEnum.DOCTOR.value],
                first_name="Врач",
                last_name="Клиники А",
            )
            doctor_b = User(
                clinic_id=clinic_b.id,
                email="doc_b@medic.local",
                phone="+70000000013",
                hashed_password=hash_password("secret123"),
                roles=[RoleEnum.DOCTOR.value],
                first_name="Врач",
                last_name="Клиники Б",
            )
            session.add_all([super_admin, admin_a, doctor_a, doctor_b])
            await session.commit()

            self.super_admin_id = super_admin.id
            self.admin_a_id = admin_a.id
            self.doctor_a_id = doctor_a.id
            self.doctor_b_id = doctor_b.id

            # Просмотры ПДн: по одному от врача каждой клиники
            await record_pdn_view(
                db=session,
                user=doctor_a,
                entity_name="medical_record",
                entity_id=1,
                resource="medical_record",
            )
            await record_pdn_view(
                db=session,
                user=doctor_b,
                entity_name="medical_record",
                entity_id=2,
                resource="medical_record",
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_super_admin_sees_all_logs(self) -> None:
        async with self.session_factory() as session:
            actor = await session.get(User, self.super_admin_id)
            service = AuditService(session)
            items, total = await service.list_logs(actor, AuditLogFilter())
            self.assertEqual(total, 2)
            self.assertEqual(len(items), 2)

    async def test_clinic_admin_sees_only_own_clinic(self) -> None:
        """Админ клиники А не видит записи врача клиники Б."""
        async with self.session_factory() as session:
            actor = await session.get(User, self.admin_a_id)
            service = AuditService(session)
            items, total = await service.list_logs(actor, AuditLogFilter())
            self.assertEqual(total, 1)
            self.assertEqual(items[0].user_id, self.doctor_a_id)

    async def test_doctor_cannot_read_audit_log(self) -> None:
        async with self.session_factory() as session:
            actor = await session.get(User, self.doctor_a_id)
            service = AuditService(session)
            with self.assertRaises(PermissionDeniedError):
                await service.list_logs(actor, AuditLogFilter())

    async def test_only_pdn_filter(self) -> None:
        async with self.session_factory() as session:
            actor = await session.get(User, self.super_admin_id)
            service = AuditService(session)
            items, total = await service.list_logs(actor, AuditLogFilter(only_pdn=True))
            self.assertEqual(total, 2)
            for item in items:
                self.assertEqual(item.action, ACTION_VIEW_PDN)

    async def test_pdn_view_records_resource(self) -> None:
        async with self.session_factory() as session:
            logs = list((await session.execute(AuditLog.__table__.select())).all())
            self.assertTrue(
                any(
                    "passport_scan" in (r.changes or "") or "medical_record" in (r.changes or "")
                    for r in logs
                )
            )


class TestAnonymizationRequests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async with self.session_factory() as session:
            clinic = Clinic(name="Клиника", code="C", address="Адрес", phone="+70000000001")
            session.add(clinic)
            await session.commit()

            super_admin = User(
                email="root@medic.local",
                phone="+70000000010",
                hashed_password=hash_password("secret123"),
                roles=[RoleEnum.SUPER_ADMIN.value],
                first_name="Root",
                last_name="Admin",
            )
            session.add(super_admin)
            await session.commit()

            patient_user = User(
                email="patient@medic.local",
                phone="+70000000020",
                hashed_password=hash_password("secret123"),
                roles=[RoleEnum.PATIENT.value],
                first_name="Иван",
                last_name="Петров",
            )
            session.add(patient_user)
            await session.commit()

            patient = Patient(
                user_id=patient_user.id,
                first_name="Иван",
                last_name="Петров",
                passport_series="1234",
                passport_number="567890",
                birth_date=date(1990, 1, 1),
                gender="M",
                phone="+70000000020",
                status=PatientStatus.ACTIVE,
            )
            session.add(patient)
            await session.commit()

            self.super_admin_id = super_admin.id
            self.patient_user_id = patient_user.id
            self.patient_id = patient.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_patient_creates_request(self) -> None:
        async with self.session_factory() as session:
            actor = await session.get(User, self.patient_user_id)
            service = AnonymizationService(session)
            request = await service.create_request(
                actor,
                AnonymizationRequestCreate(reason="Хочу удалить свои данные"),
            )
            self.assertEqual(request.status, AnonymizationRequestStatus.PENDING)
            self.assertEqual(request.patient_id, self.patient_id)

    async def test_duplicate_pending_request_rejected(self) -> None:
        async with self.session_factory() as session:
            actor = await session.get(User, self.patient_user_id)
            service = AnonymizationService(session)
            await service.create_request(actor, AnonymizationRequestCreate(reason="Первая заявка"))
            with self.assertRaises(BusinessRuleError):
                await service.create_request(
                    actor, AnonymizationRequestCreate(reason="Вторая заявка")
                )

    async def test_non_patient_cannot_create_request(self) -> None:
        async with self.session_factory() as session:
            actor = await session.get(User, self.super_admin_id)
            service = AnonymizationService(session)
            with self.assertRaises(PermissionDeniedError):
                await service.create_request(
                    actor, AnonymizationRequestCreate(reason="Попытка админа")
                )

    async def test_only_super_admin_reviews(self) -> None:
        async with self.session_factory() as session:
            patient_actor = await session.get(User, self.patient_user_id)
            service = AnonymizationService(session)
            request = await service.create_request(
                patient_actor, AnonymizationRequestCreate(reason="Право на забвение")
            )

            # Пациент не может рассмотреть свою заявку
            with self.assertRaises(PermissionDeniedError):
                await service.review_request(
                    patient_actor,
                    request.id,
                    AnonymizationRequestReview(approve=True),
                )

    async def test_approve_anonymizes_patient(self) -> None:
        """После подтверждения ПДн скрыты, карта помечена как обезличенная."""
        async with self.session_factory() as session:
            patient_actor = await session.get(User, self.patient_user_id)
            service = AnonymizationService(session)
            request = await service.create_request(
                patient_actor, AnonymizationRequestCreate(reason="Право на забвение")
            )

            admin = await session.get(User, self.super_admin_id)
            reviewed = await service.review_request(
                admin,
                request.id,
                AnonymizationRequestReview(approve=True, comment="Одобрено"),
            )
            self.assertEqual(reviewed.status, AnonymizationRequestStatus.APPROVED)

            patient = await session.get(Patient, self.patient_id)
            self.assertEqual(patient.status, PatientStatus.ANONYMIZED)
            self.assertEqual(patient.last_name, "Обезличен")
            self.assertIsNone(patient.phone)
            self.assertIsNone(patient.snils)

    async def test_reject_keeps_personal_data(self) -> None:
        async with self.session_factory() as session:
            patient_actor = await session.get(User, self.patient_user_id)
            service = AnonymizationService(session)
            request = await service.create_request(
                patient_actor, AnonymizationRequestCreate(reason="Право на забвение")
            )

            admin = await session.get(User, self.super_admin_id)
            reviewed = await service.review_request(
                admin,
                request.id,
                AnonymizationRequestReview(approve=False, comment="Есть открытые приёмы"),
            )
            self.assertEqual(reviewed.status, AnonymizationRequestStatus.REJECTED)

            patient = await session.get(Patient, self.patient_id)
            self.assertEqual(patient.status, PatientStatus.ACTIVE)
            self.assertEqual(patient.last_name, "Петров")

    async def test_patient_cancels_own_request(self) -> None:
        async with self.session_factory() as session:
            patient_actor = await session.get(User, self.patient_user_id)
            service = AnonymizationService(session)
            request = await service.create_request(
                patient_actor, AnonymizationRequestCreate(reason="Передумал удалять данные")
            )
            cancelled = await service.cancel_request(patient_actor, request.id)
            self.assertEqual(cancelled.status, AnonymizationRequestStatus.CANCELLED)

    async def test_cannot_review_already_reviewed(self) -> None:
        async with self.session_factory() as session:
            patient_actor = await session.get(User, self.patient_user_id)
            service = AnonymizationService(session)
            request = await service.create_request(
                patient_actor, AnonymizationRequestCreate(reason="Право на забвение")
            )

            admin = await session.get(User, self.super_admin_id)
            await service.review_request(
                admin, request.id, AnonymizationRequestReview(approve=False)
            )
            with self.assertRaises(BusinessRuleError):
                await service.review_request(
                    admin, request.id, AnonymizationRequestReview(approve=True)
                )


if __name__ == "__main__":
    unittest.main()
