"""Initial schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-18 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создаёт начальную схему БД."""
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_settings")),
    )

    op.create_table(
        "telegram_users",
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "is_admin_cached",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_users")),
    )
    op.create_index(
        op.f("ix_telegram_users_telegram_id"),
        "telegram_users",
        ["telegram_id"],
        unique=True,
    )

    op.create_table(
        "clans",
        sa.Column("tag", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("badge_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_status", sa.String(length=64), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clans")),
    )
    op.create_index(op.f("ix_clans_tag"), "clans", ["tag"], unique=True)

    op.create_table(
        "telegram_chats",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column(
            "is_forum",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "bot_is_admin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "bot_can_restrict_members",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("bot_permissions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_permissions_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_chats")),
    )
    op.create_index(
        op.f("ix_telegram_chats_chat_id"),
        "telegram_chats",
        ["chat_id"],
        unique=True,
    )

    op.create_table(
        "api_errors",
        sa.Column("endpoint", sa.String(length=512), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=True),
        sa.Column("entity_tag", sa.String(length=128), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("message", sa.String(length=1024), nullable=False),
        sa.Column("response_snippet", sa.String(length=2048), nullable=True),
        sa.Column("exception_class", sa.String(length=255), nullable=True),
        sa.Column("worker_name", sa.String(length=128), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_errors")),
    )
    op.create_index(op.f("ix_api_errors_endpoint"), "api_errors", ["endpoint"])
    op.create_index(op.f("ix_api_errors_entity_tag"), "api_errors", ["entity_tag"])
    op.create_index(op.f("ix_api_errors_entity_type"), "api_errors", ["entity_type"])
    op.create_index(op.f("ix_api_errors_status"), "api_errors", ["status"])
    op.create_index(op.f("ix_api_errors_worker_name"), "api_errors", ["worker_name"])

    op.create_table(
        "player_accounts",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("player_tag", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("unlinked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_clan_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_clan_id"],
            ["clans.id"],
            name=op.f("fk_player_accounts_last_seen_clan_id_clans"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_player_accounts_telegram_user_id_telegram_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_player_accounts")),
    )
    op.create_index(
        op.f("ix_player_accounts_last_seen_clan_id"),
        "player_accounts",
        ["last_seen_clan_id"],
    )
    op.create_index(
        op.f("ix_player_accounts_player_tag"),
        "player_accounts",
        ["player_tag"],
        unique=True,
    )
    op.create_index(
        op.f("ix_player_accounts_telegram_user_id"),
        "player_accounts",
        ["telegram_user_id"],
    )

    op.create_table(
        "clan_member_snapshots",
        sa.Column("clan_id", sa.BigInteger(), nullable=False),
        sa.Column("player_tag", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("town_hall_level", sa.Integer(), nullable=True),
        sa.Column("exp_level", sa.Integer(), nullable=True),
        sa.Column("trophies", sa.Integer(), nullable=True),
        sa.Column("donations", sa.Integer(), nullable=True),
        sa.Column("donations_received", sa.Integer(), nullable=True),
        sa.Column("war_preference", sa.String(length=32), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "is_current",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["clan_id"],
            ["clans.id"],
            name=op.f("fk_clan_member_snapshots_clan_id_clans"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clan_member_snapshots")),
    )
    op.create_index(
        "ix_clan_member_snapshots_clan_id_player_tag_snapshot_at",
        "clan_member_snapshots",
        ["clan_id", "player_tag", "snapshot_at"],
    )

    op.create_table(
        "player_profile_snapshots",
        sa.Column("player_tag", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("town_hall_level", sa.Integer(), nullable=True),
        sa.Column("town_hall_weapon_level", sa.Integer(), nullable=True),
        sa.Column("exp_level", sa.Integer(), nullable=True),
        sa.Column("trophies", sa.Integer(), nullable=True),
        sa.Column("best_trophies", sa.Integer(), nullable=True),
        sa.Column("war_stars", sa.Integer(), nullable=True),
        sa.Column("donations", sa.Integer(), nullable=True),
        sa.Column("donations_received", sa.Integer(), nullable=True),
        sa.Column("clan_capital_contributions", sa.Integer(), nullable=True),
        sa.Column("heroes_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("troops_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("spells_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "achievements_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "snapshot_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_player_profile_snapshots")),
    )
    op.create_index(
        "ix_player_profile_snapshots_player_tag_snapshot_at",
        "player_profile_snapshots",
        ["player_tag", "snapshot_at"],
    )

    op.create_table(
        "war_snapshots",
        sa.Column("clan_id", sa.BigInteger(), nullable=False),
        sa.Column("war_tag", sa.String(length=128), nullable=True),
        sa.Column("war_event_key", sa.String(length=160), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("team_size", sa.Integer(), nullable=False),
        sa.Column("attacks_per_member", sa.Integer(), server_default=sa.text("2"), nullable=False),
        sa.Column("preparation_start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opponent_tag", sa.String(length=32), nullable=True),
        sa.Column("opponent_name", sa.String(length=255), nullable=True),
        sa.Column("our_stars", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("opponent_stars", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("our_destruction", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("opponent_destruction", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("our_attacks", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("opponent_attacks", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["clan_id"],
            ["clans.id"],
            name=op.f("fk_war_snapshots_clan_id_clans"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_war_snapshots")),
    )
    op.create_index(op.f("ix_war_snapshots_clan_id"), "war_snapshots", ["clan_id"])
    op.create_index(
        op.f("ix_war_snapshots_war_event_key"),
        "war_snapshots",
        ["war_event_key"],
        unique=True,
    )
    op.create_index(op.f("ix_war_snapshots_war_tag"), "war_snapshots", ["war_tag"])

    op.create_table(
        "cwl_seasons",
        sa.Column("clan_id", sa.BigInteger(), nullable=False),
        sa.Column("season", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["clan_id"],
            ["clans.id"],
            name=op.f("fk_cwl_seasons_clan_id_clans"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cwl_seasons")),
    )
    op.create_index(op.f("ix_cwl_seasons_clan_id"), "cwl_seasons", ["clan_id"])
    op.create_index(op.f("ix_cwl_seasons_season"), "cwl_seasons", ["season"])

    op.create_table(
        "raid_seasons",
        sa.Column("clan_id", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("capital_total_loot", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("raids_completed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("total_attacks", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "enemy_districts_destroyed",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("offensive_reward", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("defensive_reward", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["clan_id"],
            ["clans.id"],
            name=op.f("fk_raid_seasons_clan_id_clans"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raid_seasons")),
    )
    op.create_index(op.f("ix_raid_seasons_clan_id"), "raid_seasons", ["clan_id"])

    op.create_table(
        "cwl_wars",
        sa.Column("cwl_season_id", sa.BigInteger(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("war_tag", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("our_clan_tag", sa.String(length=32), nullable=False),
        sa.Column("opponent_clan_tag", sa.String(length=32), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("our_stars", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("opponent_stars", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("our_destruction", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("opponent_destruction", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["cwl_season_id"],
            ["cwl_seasons.id"],
            name=op.f("fk_cwl_wars_cwl_season_id_cwl_seasons"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cwl_wars")),
    )
    op.create_index(op.f("ix_cwl_wars_cwl_season_id"), "cwl_wars", ["cwl_season_id"])
    op.create_index(op.f("ix_cwl_wars_opponent_clan_tag"), "cwl_wars", ["opponent_clan_tag"])
    op.create_index(op.f("ix_cwl_wars_our_clan_tag"), "cwl_wars", ["our_clan_tag"])
    op.create_index(op.f("ix_cwl_wars_war_tag"), "cwl_wars", ["war_tag"], unique=True)

    op.create_table(
        "raid_members",
        sa.Column("raid_season_id", sa.BigInteger(), nullable=False),
        sa.Column("player_tag", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("attacks", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "project_expected_attacks",
            sa.Integer(),
            server_default=sa.text("6"),
            nullable=False,
        ),
        sa.Column(
            "capital_resources_looted",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["raid_season_id"],
            ["raid_seasons.id"],
            name=op.f("fk_raid_members_raid_season_id_raid_seasons"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raid_members")),
    )
    op.create_index(op.f("ix_raid_members_player_tag"), "raid_members", ["player_tag"])
    op.create_index(op.f("ix_raid_members_raid_season_id"), "raid_members", ["raid_season_id"])

    op.create_table(
        "war_members",
        sa.Column("war_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("player_tag", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("town_hall_level", sa.Integer(), nullable=True),
        sa.Column("map_position", sa.Integer(), nullable=True),
        sa.Column("attacks_done", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("attacks_left", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["war_snapshot_id"],
            ["war_snapshots.id"],
            name=op.f("fk_war_members_war_snapshot_id_war_snapshots"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_war_members")),
    )
    op.create_index(op.f("ix_war_members_player_tag"), "war_members", ["player_tag"])
    op.create_index(op.f("ix_war_members_war_snapshot_id"), "war_members", ["war_snapshot_id"])

    op.create_table(
        "war_attacks",
        sa.Column("war_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("attacker_tag", sa.String(length=32), nullable=False),
        sa.Column("defender_tag", sa.String(length=32), nullable=False),
        sa.Column("stars", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("destruction_percentage", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["war_snapshot_id"],
            ["war_snapshots.id"],
            name=op.f("fk_war_attacks_war_snapshot_id_war_snapshots"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_war_attacks")),
    )
    op.create_index(op.f("ix_war_attacks_attacker_tag"), "war_attacks", ["attacker_tag"])
    op.create_index(op.f("ix_war_attacks_defender_tag"), "war_attacks", ["defender_tag"])
    op.create_index(op.f("ix_war_attacks_war_snapshot_id"), "war_attacks", ["war_snapshot_id"])

    op.create_table(
        "notification_routes",
        sa.Column("clan_id", sa.BigInteger(), nullable=False),
        sa.Column("notification_type", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_thread_id", sa.BigInteger(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by_telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["telegram_chats.chat_id"],
            name=op.f("fk_notification_routes_chat_id_telegram_chats"),
        ),
        sa.ForeignKeyConstraint(
            ["clan_id"],
            ["clans.id"],
            name=op.f("fk_notification_routes_clan_id_clans"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_notification_routes_created_by_telegram_user_id_telegram_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_routes")),
    )
    op.create_index(op.f("ix_notification_routes_chat_id"), "notification_routes", ["chat_id"])
    op.create_index(op.f("ix_notification_routes_clan_id"), "notification_routes", ["clan_id"])
    op.create_index(
        op.f("ix_notification_routes_created_by_telegram_user_id"),
        "notification_routes",
        ["created_by_telegram_user_id"],
    )
    op.create_index(
        op.f("ix_notification_routes_message_thread_id"),
        "notification_routes",
        ["message_thread_id"],
    )
    op.create_index(
        "uq_notification_routes_clan_notification_chat_thread",
        "notification_routes",
        [
            "clan_id",
            "notification_type",
            "chat_id",
            sa.text("coalesce(message_thread_id, 0)"),
        ],
        unique=True,
    )

    op.create_table(
        "notification_logs",
        sa.Column("route_id", sa.BigInteger(), nullable=True),
        sa.Column("notification_type", sa.String(length=64), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_thread_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("payload_summary", sa.String(length=2048), nullable=False),
        sa.Column("error_text", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["telegram_chats.chat_id"],
            name=op.f("fk_notification_logs_chat_id_telegram_chats"),
        ),
        sa.ForeignKeyConstraint(
            ["route_id"],
            ["notification_routes.id"],
            name=op.f("fk_notification_logs_route_id_notification_routes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_logs")),
    )
    op.create_index(op.f("ix_notification_logs_chat_id"), "notification_logs", ["chat_id"])
    op.create_index(
        op.f("ix_notification_logs_event_key"),
        "notification_logs",
        ["event_key"],
        unique=True,
    )
    op.create_index(op.f("ix_notification_logs_route_id"), "notification_logs", ["route_id"])

    op.create_table(
        "warnings",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column(
            "is_impactful",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("comment", sa.String(length=2048), nullable=True),
        sa.Column("author_telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("clan_id", sa.BigInteger(), nullable=True),
        sa.Column("event_key", sa.String(length=255), nullable=True),
        sa.Column(
            "affected_player_tags_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "affected_player_names_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_cwl_season_key", sa.String(length=64), nullable=True),
        sa.Column("active_until_cwl_season_id", sa.BigInteger(), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("cancelled_reason", sa.String(length=2048), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["active_until_cwl_season_id"],
            ["cwl_seasons.id"],
            name=op.f("fk_warnings_active_until_cwl_season_id_cwl_seasons"),
        ),
        sa.ForeignKeyConstraint(
            ["author_telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_warnings_author_telegram_user_id_telegram_users"),
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_warnings_cancelled_by_telegram_user_id_telegram_users"),
        ),
        sa.ForeignKeyConstraint(
            ["clan_id"],
            ["clans.id"],
            name=op.f("fk_warnings_clan_id_clans"),
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_warnings_telegram_user_id_telegram_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_warnings")),
    )
    op.create_index(
        op.f("ix_warnings_active_until_cwl_season_id"),
        "warnings",
        ["active_until_cwl_season_id"],
    )
    op.create_index(
        op.f("ix_warnings_author_telegram_user_id"),
        "warnings",
        ["author_telegram_user_id"],
    )
    op.create_index(
        op.f("ix_warnings_cancelled_by_telegram_user_id"),
        "warnings",
        ["cancelled_by_telegram_user_id"],
    )
    op.create_index(op.f("ix_warnings_clan_id"), "warnings", ["clan_id"])
    op.create_index(
        op.f("ix_warnings_created_cwl_season_key"),
        "warnings",
        ["created_cwl_season_key"],
    )
    op.create_index(op.f("ix_warnings_event_key"), "warnings", ["event_key"], unique=True)
    op.create_index(
        op.f("ix_warnings_telegram_user_id"),
        "warnings",
        ["telegram_user_id"],
    )

    op.create_table(
        "kick_candidates",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("player_tag", sa.String(length=32), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_by_telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_comment", sa.String(length=2048), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["telegram_users.id"],
            name=op.f("fk_kick_candidates_created_by_telegram_users"),
        ),
        sa.ForeignKeyConstraint(
            ["decision_by_telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_kick_candidates_decision_by_telegram_user_id_telegram_users"),
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_kick_candidates_telegram_user_id_telegram_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kick_candidates")),
    )
    op.create_index(op.f("ix_kick_candidates_created_by"), "kick_candidates", ["created_by"])
    op.create_index(
        op.f("ix_kick_candidates_decision_by_telegram_user_id"),
        "kick_candidates",
        ["decision_by_telegram_user_id"],
    )
    op.create_index(
        op.f("ix_kick_candidates_event_key"),
        "kick_candidates",
        ["event_key"],
        unique=True,
    )
    op.create_index(op.f("ix_kick_candidates_player_tag"), "kick_candidates", ["player_tag"])
    op.create_index(
        op.f("ix_kick_candidates_telegram_user_id"),
        "kick_candidates",
        ["telegram_user_id"],
    )

    op.create_table(
        "player_events",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("player_tag", sa.String(length=32), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2048), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_player_events_telegram_user_id_telegram_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_player_events")),
    )
    op.create_index(op.f("ix_player_events_event_type"), "player_events", ["event_type"])
    op.create_index(op.f("ix_player_events_player_tag"), "player_events", ["player_tag"])
    op.create_index(
        op.f("ix_player_events_telegram_user_id"),
        "player_events",
        ["telegram_user_id"],
    )


def downgrade() -> None:
    """Удаляет начальную схему БД."""
    op.drop_table("player_events")
    op.drop_table("kick_candidates")
    op.drop_table("warnings")
    op.drop_table("notification_logs")
    op.drop_table("notification_routes")
    op.drop_table("war_attacks")
    op.drop_table("war_members")
    op.drop_table("raid_members")
    op.drop_table("cwl_wars")
    op.drop_table("raid_seasons")
    op.drop_table("cwl_seasons")
    op.drop_table("war_snapshots")
    op.drop_table("player_profile_snapshots")
    op.drop_table("clan_member_snapshots")
    op.drop_table("player_accounts")
    op.drop_table("api_errors")
    op.drop_table("telegram_chats")
    op.drop_table("clans")
    op.drop_table("telegram_users")
    op.drop_table("app_settings")
    