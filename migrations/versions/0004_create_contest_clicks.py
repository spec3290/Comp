"""참가 바로가기(F2-5) contest_clicks 테이블 생성

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """contest_clicks 테이블과 대회별 집계용 인덱스를 만든다."""
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


def downgrade() -> None:
    """contest_clicks 테이블을 지운다."""
    op.drop_index("ix_contest_clicks_contest_id", table_name="contest_clicks")
    op.drop_table("contest_clicks")
