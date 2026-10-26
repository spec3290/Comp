"""프로필 설정 (F1-7): 학년 정수화, 닉네임 UNIQUE, 관심 분야 테이블

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # F1-7 이전에는 프로필을 쓰는 곳이 없어 grade 는 모두 NULL 이다
    op.alter_column(
        "users",
        "grade",
        type_=sa.SmallInteger(),
        existing_nullable=True,
        postgresql_using="grade::smallint",
    )
    op.create_unique_constraint("uq_users_nickname", "users", ["nickname"])
    op.create_table(
        "user_interest_fields",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("field", sa.String(20), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("user_interest_fields")
    op.drop_constraint("uq_users_nickname", "users", type_="unique")
    op.alter_column(
        "users",
        "grade",
        type_=sa.String(20),
        existing_nullable=True,
        postgresql_using="grade::varchar",
    )
