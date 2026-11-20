"""프로필(F1-7)에서 쓰지 않는 학년·학교 유형 컬럼 삭제 (Q-38)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """맞출 대회 값이 없는 개인정보라 users 의 grade·school_type 을 지운다."""
    op.drop_column("users", "school_type")
    op.drop_column("users", "grade")


def downgrade() -> None:
    """컬럼만 NULL 로 되살린다. 지운 값은 돌아오지 않는다."""
    op.add_column("users", sa.Column("grade", sa.SmallInteger(), nullable=True))
    op.add_column("users", sa.Column("school_type", sa.String(20), nullable=True))
