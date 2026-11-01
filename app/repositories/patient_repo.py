from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.entities import Appointment, MedicalRecord, MedicalRecordEntry, Patient
from app.repositories.base import BaseRepository


class PatientRepository(BaseRepository[Patient]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Patient, session)

    async def get_by_passport(
        self,
        passport_series: str,
        passport_number: str,
    ) -> Patient | None:
        """Поиск пациента по естественному ключу — серии и номеру паспорта."""
        stmt = select(Patient).where(
            Patient.passport_series == passport_series.strip(),
            Patient.passport_number == passport_number.strip(),
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_user_id(self, user_id: int) -> Patient | None:
        stmt = (
            select(Patient)
            .options(selectinload(Patient.medical_record))
            .where(Patient.user_id == user_id)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def search(
        self,
        query: str | None = None,
        offset: int = 0,
        limit: int = 20,
        doctor_id: int | None = None,
        clinic_id: int | None = None,
    ) -> tuple[list[Patient], int]:
        """Поиск пациентов с ролевыми ограничениями области видимости.

        - doctor_id: правило «по касанию» — только пациенты, с которыми
          у врача есть приём (назначенный или в прошлом).
        - clinic_id: изоляция по клинике (🏥). None — вся сеть (SA, главврач).
        """
        stmt = select(Patient)
        count_stmt = select(func.count()).select_from(Patient)

        if doctor_id is not None:
            # «По касанию»: пациент виден, только если есть связь через приём
            linked = (
                select(Appointment.patient_id)
                .where(Appointment.doctor_id == doctor_id)
                .scalar_subquery()
            )
            stmt = stmt.where(Patient.id.in_(linked))
            count_stmt = count_stmt.where(Patient.id.in_(linked))

        if clinic_id is not None:
            stmt = stmt.where(Patient.clinic_id == clinic_id)
            count_stmt = count_stmt.where(Patient.clinic_id == clinic_id)

        if query:
            q = f"%{query.strip()}%"
            digits_only = query.strip().replace(" ", "").replace("-", "")
            filter_clause = or_(
                Patient.last_name.ilike(q),
                Patient.first_name.ilike(q),
                Patient.middle_name.ilike(q),
                Patient.phone.ilike(q),
                Patient.snils.ilike(q),
                # Паспорт ищем и по частям: '1234' найдёт серию, '567890' — номер
                Patient.passport_series.ilike(q),
                Patient.passport_number.ilike(q),
                Patient.passport_series.ilike(f"%{digits_only}%"),
                Patient.passport_number.ilike(f"%{digits_only}%"),
            )
            stmt = stmt.where(filter_clause)
            count_stmt = count_stmt.where(filter_clause)
        stmt = stmt.order_by(Patient.last_name, Patient.first_name).offset(offset).limit(limit)
        items = (await self.session.execute(stmt)).scalars().all()
        total = int((await self.session.execute(count_stmt)).scalar_one())
        return list(items), total

    async def get_medical_record(self, patient_id: int) -> MedicalRecord | None:
        stmt = (
            select(MedicalRecord)
            .options(
                selectinload(MedicalRecord.entries).selectinload(MedicalRecordEntry.doctor),
                selectinload(MedicalRecord.assignments),
            )
            .where(MedicalRecord.patient_id == patient_id)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()
