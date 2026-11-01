"""CLI-утилита для обслуживания системы MEDIC.

Запуск: `python -m app.cli <команда> [параметры]`

Основные команды:
- create-superadmin — создать первого администратора платформы (bootstrap).
- create-clinic     — создать клинику/филиал.
- ensure-clinic     — создать клинику по умолчанию, если её нет.
- seed-demo         — наполнить базу демонстрационными данными.
- list-users        — список пользователей (для диагностики).
"""

import argparse
import asyncio
import getpass
import sys

from app.core.logging import logger, setup_logging
from app.db.session import async_session_factory
from app.services.bootstrap_service import BootstrapService


async def _run_create_superadmin(args: argparse.Namespace) -> int:
    """Создаёт супер-админа. Пароль можно передать или ввести интерактивно."""
    email = args.email
    phone = args.phone
    password = args.password

    if not email:
        email = input("Email администратора: ").strip()
    if not phone:
        phone = input("Телефон администратора: ").strip()
    if not password:
        password = getpass.getpass("Пароль администратора: ")
        confirm = getpass.getpass("Повторите пароль: ")
        if password != confirm:
            logger.error("Пароли не совпадают")
            return 1

    if len(password) < 8:
        logger.error("Пароль должен быть не короче 8 символов")
        return 1

    async with async_session_factory() as session:
        service = BootstrapService(session)
        try:
            admin = await service.create_super_admin(
                email=email,
                password=password,
                phone=phone,
                first_name=args.first_name,
                last_name=args.last_name,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.error("Не удалось создать администратора: {}", exc)
            return 1

    logger.info("Супер-админ создан: {} (id={})", admin.email, admin.id)
    return 0


async def _run_create_clinic(args: argparse.Namespace) -> int:
    from app.models.entities import Clinic

    async with async_session_factory() as session:
        clinic = Clinic(
            name=args.name,
            code=args.code,
            address=args.address,
            phone=args.phone,
        )
        session.add(clinic)
        await session.commit()
        logger.info("Клиника создана: {} (id={})", clinic.name, clinic.id)
    return 0


async def _run_ensure_clinic(_: argparse.Namespace) -> int:
    async with async_session_factory() as session:
        service = BootstrapService(session)
        clinic = await service.ensure_default_clinic()
        await session.commit()
        logger.info("Клиника по умолчанию: {} (id={})", clinic.name, clinic.id)
    return 0


async def _run_list_users(_: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.models.entities import User

    async with async_session_factory() as session:
        users: list[User] = list(
            (await session.execute(select(User).order_by(User.id))).scalars().all()
        )
        if not users:
            logger.info("Пользователей нет")
            return 0
        for user in users:
            logger.info(
                "id={} | {} | роль={} | активен={}",
                user.id,
                user.email,
                ",".join(user.roles),
                user.is_active,
            )
    return 0


async def _run_seed_demo(_: argparse.Namespace) -> int:
    """Наполняет базу демонстрационными данными (идемпотентно).

    Создаёт клинику по умолчанию, врача, пациента, услугу и график работы.
    Повторный запуск не дублирует уже существующие записи.
    """
    from app.services.demo_service import DemoSeedService

    async with async_session_factory() as session:
        service = DemoSeedService(session)
        try:
            summary = await service.seed()
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.error("Не удалось наполнить демо-данными: {}", exc)
            return 1

    logger.info("Демо-данные готовы: {}", summary)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="medic", description="CLI системы MEDIC")
    sub = parser.add_subparsers(dest="command", required=True)

    p_admin = sub.add_parser("create-superadmin", help="Создать первого супер-админа")
    p_admin.add_argument("--email", help="Email администратора")
    p_admin.add_argument("--phone", help="Телефон администратора")
    p_admin.add_argument("--password", help="Пароль (иначе запросится интерактивно)")
    p_admin.add_argument("--first-name", default="Super", help="Имя")
    p_admin.add_argument("--last-name", default="Admin", help="Фамилия")
    p_admin.set_defaults(func=_run_create_superadmin)

    p_clinic = sub.add_parser("create-clinic", help="Создать клинику/филиал")
    p_clinic.add_argument("--name", required=True)
    p_clinic.add_argument("--code", required=True)
    p_clinic.add_argument("--address", required=True)
    p_clinic.add_argument("--phone", required=True)
    p_clinic.set_defaults(func=_run_create_clinic)

    p_ensure = sub.add_parser("ensure-clinic", help="Создать клинику по умолчанию, если её нет")
    p_ensure.set_defaults(func=_run_ensure_clinic)

    p_seed = sub.add_parser("seed-demo", help="Наполнить базу демонстрационными данными")
    p_seed.set_defaults(func=_run_seed_demo)

    p_list = sub.add_parser("list-users", help="Список пользователей")
    p_list.set_defaults(func=_run_list_users)

    return parser


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)

    runner = args.func
    return asyncio.run(runner(args))


if __name__ == "__main__":
    sys.exit(main())
