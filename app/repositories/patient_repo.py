from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.entities import MedicalRecord, MedicalRecordEntry, Patient
from app.repositories.base import BaseRepository


class PatientRepository(BaseRepository[Patient]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Patient, session)

    async def get_by_phone(self, phone: str) -> Patient | None:
        stmt = select(Patient).where(Patient.phone == phone.strip())
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
    ) -> tuple[list[Patient], int]:
        stmt = select(Patient)
        count_stmt = select(func.count()).select_from(Patient)

        if query:
            q = f"%{query.strip()}%"
            filter_clause = or_(
                Patient.last_name.ilike(q),
                Patient.first_name.ilike(q),
                Patient.phone.ilike(q),
                Patient.snils.ilike(q),
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
