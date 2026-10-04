from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.entities import DoctorProfile, User
from app.models.enums import RoleEnum
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(User, session)

    async def get_by_email(self, email: str) -> User | None:
        stmt = (
            select(User)
            .options(
                selectinload(User.clinic),
                selectinload(User.doctor_profile),
                selectinload(User.patient_profile),
            )
            .where(User.email == email.lower().strip())
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_phone(self, phone: str) -> User | None:
        stmt = select(User).where(User.phone == phone.strip())
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_doctors(self, clinic_id: int | None = None) -> list[User]:
        stmt = (
            select(User)
            .options(selectinload(User.doctor_profile))
            .where(User.role == RoleEnum.DOCTOR, User.is_active.is_(True))
        )
        if clinic_id:
            stmt = stmt.where(User.clinic_id == clinic_id)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def create_doctor_profile(self, profile: DoctorProfile) -> DoctorProfile:
        self.session.add(profile)
        await self.session.flush()
        return profile
