"""쓰는 곳이 없는 참가 바로가기 클릭 기록(F2-5) contest_clicks 테이블 삭제 (Q-39)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """읽는 기능 없이 회원 행동만 쌓던 클릭 기록을 지운다."""
    op.drop_index("ix_contest_clicks_contest_id", table_name="contest_clicks")
    op.drop_table("contest_clicks")


def downgrade() -> None:
    """빈 테이블과 인덱스만 되살린다. 지운 클릭은 돌아오지 않는다."""
    op.create_table(
        "contest_clicks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contest_id", sa.String(length=24), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_contest_clicks_contest_id", "contest_clicks", ["contest_id"])
