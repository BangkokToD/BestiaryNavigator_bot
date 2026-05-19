"""Forbid zero Telegram message thread id.

Revision ID: 0002_route_thread_id_guard
Revises: 0001_initial_schema
Create Date: 2026-05-19 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_route_thread_id_guard"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Запрещает sentinel-значение 0 для Telegram topic/thread id."""
    op.create_check_constraint(
        op.f("ck_notification_routes_message_thread_id_positive"),
        "notification_routes",
        "message_thread_id IS NULL OR message_thread_id > 0",
    )


def downgrade() -> None:
    """Удаляет ограничение Telegram topic/thread id."""
    op.drop_constraint(
        op.f("ck_notification_routes_message_thread_id_positive"),
        "notification_routes",
        type_="check",
    )
