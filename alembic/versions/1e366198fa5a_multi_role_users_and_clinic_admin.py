"""multi-role users and clinic admin

Переход от единственной роли (users.role) к мультиролевой модели (users.roles).

Порядок безопасен для существующих данных:
1. Добавляем roles как nullable.
2. Переносим значение role в JSON-массив ["role"].
3. Делаем roles NOT NULL.
4. Удаляем старую колонку role и её индекс.

Revision ID: 1e366198fa5a
Revises: 3b64cf658e37
Create Date: 2026-10-24 15:25:08.711442

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "1e366198fa5a"
down_revision: Union[str, None] = "3b64cf658e37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Новая колонка — пока nullable, чтобы не упасть на существующих строках
    op.add_column(
        "users",
        sa.Column(
            "roles",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.Text(), "sqlite"),
            nullable=True,
        ),
    )

    # 2. Переносим старую роль в массив: role='doctor' -> roles='["doctor"]'.
    # lower() обязателен: PostgreSQL Enum отдаёт имя члена в UPPERCASE ('DOCTOR'),
    # а код приложения сравнивает со значениями в нижнем регистре.
    op.execute(
        """
        UPDATE users
        SET roles = to_jsonb(ARRAY[lower(role::text)])
        WHERE roles IS NULL
        """
    )
    # 3. Теперь можно сделать NOT NULL
    op.alter_column("users", "roles", nullable=False)

    # 4. Удаляем старую колонку и индекс
    op.drop_index(op.f("users_role_idx"), table_name="users")
    op.drop_column("users", "role")


def downgrade() -> None:
    # Обратный переход: берём первую роль из массива
    op.add_column("users", sa.Column("role", sa.VARCHAR(length=12), nullable=True))

    op.execute(
        """
        UPDATE users
        SET role = roles->>0
        WHERE roles IS NOT NULL
        """
    )

    op.alter_column("users", "role", nullable=False)
    op.create_index(op.f("users_role_idx"), "users", ["role"], unique=False)
    op.drop_column("users", "roles")
