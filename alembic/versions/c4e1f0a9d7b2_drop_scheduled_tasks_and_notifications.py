"""drop scheduled_tasks and notifications

Removes the proactive scheduler feature and its notifications (the scheduler was the
only producer of notifications).

Revision ID: c4e1f0a9d7b2
Revises: 592e7ae78c43
Create Date: 2026-09-22 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e1f0a9d7b2"
down_revision: Union[str, Sequence[str], None] = "592e7ae78c43"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the scheduled-task + notification tables."""
    op.drop_index(op.f("ix_scheduled_tasks_next_run"), table_name="scheduled_tasks")
    op.drop_table("scheduled_tasks")
    op.drop_index(op.f("ix_notifications_created_at"), table_name="notifications")
    op.drop_table("notifications")


def downgrade() -> None:
    """Recreate the tables (matches the initial schema) so the migration is reversible."""
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("read", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_notifications_created_at"), "notifications", ["created_at"], unique=False
    )
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("schedule_kind", sa.String(length=16), nullable=False),
        sa.Column("interval_sec", sa.Integer(), nullable=True),
        sa.Column("time_of_day", sa.String(length=5), nullable=True),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("last_run", sa.Float(), nullable=True),
        sa.Column("next_run", sa.Float(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_scheduled_tasks_next_run"), "scheduled_tasks", ["next_run"], unique=False
    )
