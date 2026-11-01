import unittest
from datetime import UTC, date, datetime, time

from app.core.exceptions import ConflictError
from app.db.base import Base
from app.models.entities import Clinic, DoctorProfile, Patient, User
from app.models.enums import RoleEnum
from app.schemas.schedules import DoctorScheduleCreate, TimeOffCreate
from app.services.schedule_service import ScheduleService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class TestScheduleService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async with self.session_factory() as session:
            clinic = Clinic(name="Клиника", code="C1", address="Адрес", phone="+70000000000")
            doctor = User(
                email="doc@example.com",
                phone="+70000000010",
                hashed_password="hash",
                role=RoleEnum.DOCTOR,
                first_name="Терапевт",
                last_name="Докторов",
            )
            patient = Patient(
                first_name="Пациент",
                last_name="Тестов",
                passport_series="1234",
                passport_number="567890",
                birth_date=date(1990, 1, 1),
                gender="M",
            )
            session.add_all([clinic, doctor, patient])
            await session.commit()

            session.add(DoctorProfile(user_id=doctor.id, specialization="Терапевт"))
            await session.commit()

            self.clinic_id = clinic.id
            self.doctor_id = doctor.id
            self.patient_id = patient.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    # ----- Шаблоны смен -----
    async def test_create_schedule_and_reject_duplicate_weekday(self) -> None:
        async with self.session_factory() as session:
            service = ScheduleService(session)
            dto = DoctorScheduleCreate(
                doctor_id=self.doctor_id,
                clinic_id=self.clinic_id,
                weekday=0,
                start_time=time(9, 0),
                end_time=time(18, 0),
                break_start=time(13, 0),
                break_end=time(14, 0),
            )
            created = await service.create_schedule(dto)
            await session.commit()
            self.assertIsNotNone(created.id)

            # Второй раз на тот же день недели — конфликт
            with self.assertRaises(ConflictError):
                await service.create_schedule(dto)

    # ----- Генерация свободных слотов -----
    async def test_free_slots_exclude_break(self) -> None:
        """Слоты 09:00-18:00 с обедом 13:00-14:00 не должны попадать в обед."""
        async with self.session_factory() as session:
            service = ScheduleService(session)
            # Понедельник
            weekday = 0
            await service.create_schedule(
                DoctorScheduleCreate(
                    doctor_id=self.doctor_id,
                    clinic_id=self.clinic_id,
                    weekday=weekday,
                    start_time=time(9, 0),
                    end_time=time(18, 0),
                    break_start=time(13, 0),
                    break_end=time(14, 0),
                )
            )
            await session.commit()

            # Находим ближайший понедельник
            today = date.today()
            days_ahead = (7 - today.weekday() + weekday) % 7 or 7
            target = date.fromordinal(today.toordinal() + days_ahead)

            day = await service.get_doctor_day_schedule(self.doctor_id, target, slot_minutes=30)
            self.assertTrue(day.is_working_day)

            # Слота, пересекающегося с обедом, быть не должно
            for slot in day.free_slots:
                self.assertFalse(
                    slot.start < datetime.combine(target, time(14, 0))
                    and datetime.combine(target, time(13, 0)) < slot.end,
                    f"Слот {slot.start}-{slot.end} пересекает обед",
                )

    async def test_day_off_when_no_schedule(self) -> None:
        """Нет смены на этот день недели — рабочий день отсутствует."""
        async with self.session_factory() as session:
            service = ScheduleService(session)
            some_date = date(2026, 6, 3)  # среда
            day = await service.get_doctor_day_schedule(self.doctor_id, some_date, slot_minutes=30)
            self.assertFalse(day.is_working_day)
            self.assertEqual(day.free_slots, [])

    async def test_time_off_blocks_day(self) -> None:
        """Отпуск полностью блокирует день, даже если есть смена."""
        async with self.session_factory() as session:
            service = ScheduleService(session)
            target = date(2026, 6, 1)  # понедельник
            await service.create_schedule(
                DoctorScheduleCreate(
                    doctor_id=self.doctor_id,
                    clinic_id=self.clinic_id,
                    weekday=target.weekday(),
                    start_time=time(9, 0),
                    end_time=time(18, 0),
                )
            )
            await service.create_time_off(
                TimeOffCreate(
                    doctor_id=self.doctor_id,
                    start_date=target,
                    end_date=target,
                    reason="Отпуск",
                )
            )
            await session.commit()

            day = await service.get_doctor_day_schedule(self.doctor_id, target, slot_minutes=30)
            self.assertFalse(day.is_working_day)
            self.assertEqual(day.free_slots, [])

    # ----- Проверка доступности при создании приёма -----
    async def test_is_doctor_available_respects_shift_and_break(self) -> None:
        async with self.session_factory() as session:
            service = ScheduleService(session)
            target = date(2026, 6, 1)  # понедельник
            await service.create_schedule(
                DoctorScheduleCreate(
                    doctor_id=self.doctor_id,
                    clinic_id=self.clinic_id,
                    weekday=target.weekday(),
                    start_time=time(9, 0),
                    end_time=time(18, 0),
                    break_start=time(13, 0),
                    break_end=time(14, 0),
                )
            )
            await session.commit()

            # Внутри смены, до обеда — доступен
            self.assertTrue(
                await service.is_doctor_available(
                    self.doctor_id,
                    datetime.combine(target, time(10, 0), tzinfo=UTC),
                    duration_minutes=30,
                )
            )
            # В обед — недоступен
            self.assertFalse(
                await service.is_doctor_available(
                    self.doctor_id,
                    datetime.combine(target, time(13, 0), tzinfo=UTC),
                    duration_minutes=30,
                )
            )
            # До открытия — недоступен
            self.assertFalse(
                await service.is_doctor_available(
                    self.doctor_id,
                    datetime.combine(target, time(8, 0), tzinfo=UTC),
                    duration_minutes=30,
                )
            )

    async def test_schedule_validation_rejects_bad_times(self) -> None:
        """Схема не должна пропускать смену, где конец раньше начала."""
        from pydantic import ValidationError as PydanticValidationError

        with self.assertRaises(PydanticValidationError):
            DoctorScheduleCreate(
                doctor_id=self.doctor_id,
                clinic_id=self.clinic_id,
                weekday=1,
                start_time=time(18, 0),
                end_time=time(9, 0),
            )


if __name__ == "__main__":
    unittest.main()
