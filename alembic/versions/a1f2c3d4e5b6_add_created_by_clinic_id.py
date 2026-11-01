"""add created_by_clinic_id to patients

Revision ID: a1f2c3d4e5b6
Revises: 8dd04b5e710e
Create Date: 2026-11-01 14:00:00.000000

Добавляет на карту пациента поле created_by_clinic_id — клинику, чей
сотрудник завёл карту. Это НЕ «принадлежность» пациента (карта общая
для сети), а факт регистрации: используется только для административных
операций над картой, у которой ещё нет приёмов.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f2c3d4e5b6"
down_revision: str | None = "8dd04b5e710e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.add_column(
        "patients",
        sa.Column("created_by_clinic_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("patients_created_by_clinic_id_idx"),
        "patients",
        ["created_by_clinic_id"],
    )
    op.create_foreign_key(
        op.f("patients_created_by_clinic_id_fkey"),
        "patients",
        "clinics",
        ["created_by_clinic_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("patients_created_by_clinic_id_fkey"),
        "patients",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("patients_created_by_clinic_id_idx"),
        table_name="patients",
    )
    op.drop_column("patients", "created_by_clinic_id")
