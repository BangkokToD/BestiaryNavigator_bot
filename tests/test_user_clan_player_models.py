"""Тесты моделей пользователей, кланов и игроков."""

from sqlalchemy import DateTime, Index, inspect
from sqlalchemy.dialects.postgresql import JSONB

from app.db.models import (
    Clan,
    ClanMemberSnapshot,
    PlayerAccount,
    PlayerProfileSnapshot,
    TelegramUser,
)


def test_telegram_user_model_contract() -> None:
    """Проверяет контракт модели TelegramUser."""
    table = TelegramUser.__table__
    columns = table.c

    assert table.name == "telegram_users"
    assert columns.telegram_id.unique is True
    assert columns.telegram_id.nullable is False
    assert columns.username.nullable is True
    assert columns.display_name.nullable is True
    assert columns.is_admin_cached.nullable is False

    first_seen_at_type = columns.first_seen_at.type
    last_seen_at_type = columns.last_seen_at.type

    assert isinstance(first_seen_at_type, DateTime)
    assert isinstance(last_seen_at_type, DateTime)
    assert first_seen_at_type.timezone is True
    assert last_seen_at_type.timezone is True


def test_player_account_model_contract_and_relationships() -> None:
    """Проверяет контракт модели PlayerAccount и связь с TelegramUser."""
    table = PlayerAccount.__table__
    columns = table.c

    assert table.name == "player_accounts"
    assert columns.player_tag.unique is True
    assert columns.player_tag.nullable is False
    assert columns.telegram_user_id.nullable is True
    assert columns.last_seen_clan_id.nullable is True
    assert columns.is_active.nullable is False

    mapper = inspect(PlayerAccount)

    assert mapper.relationships["telegram_user"].mapper.class_ is TelegramUser
    assert mapper.relationships["last_seen_clan"].mapper.class_ is Clan

    user_mapper = inspect(TelegramUser)

    assert user_mapper.relationships["accounts"].mapper.class_ is PlayerAccount


def test_clan_model_contract() -> None:
    """Проверяет контракт модели Clan."""
    table = Clan.__table__
    columns = table.c

    assert table.name == "clans"
    assert columns.tag.unique is True
    assert columns.tag.nullable is False
    assert columns.name.nullable is False
    assert columns.type.nullable is False
    assert columns.level.nullable is True
    assert columns.badge_url.nullable is True
    assert columns.is_active.nullable is False
    assert columns.last_sync_at.nullable is True
    assert columns.sync_status.nullable is True


def test_clan_member_snapshot_model_contract_and_index() -> None:
    """Проверяет контракт модели ClanMemberSnapshot и обязательный индекс."""
    table = ClanMemberSnapshot.__table__
    columns = table.c

    assert table.name == "clan_member_snapshots"
    assert columns.clan_id.nullable is False
    assert columns.player_tag.nullable is False
    assert columns.name.nullable is False
    assert columns.is_current.nullable is False

    snapshot_at_type = columns.snapshot_at.type

    assert isinstance(snapshot_at_type, DateTime)
    assert snapshot_at_type.timezone is True

    expected_index_columns = ("clan_id", "player_tag", "snapshot_at")
    index_columns = {
        tuple(index.columns.keys())
        for index in table.indexes
        if isinstance(index, Index)
    }

    assert expected_index_columns in index_columns

    mapper = inspect(ClanMemberSnapshot)

    assert mapper.relationships["clan"].mapper.class_ is Clan


def test_player_profile_snapshot_model_contract_and_jsonb_fields() -> None:
    """Проверяет контракт модели PlayerProfileSnapshot и JSONB-поля."""
    table = PlayerProfileSnapshot.__table__
    columns = table.c

    assert table.name == "player_profile_snapshots"
    assert columns.player_tag.nullable is False
    assert columns.name.nullable is False
    assert columns.snapshot_at.nullable is False

    for field_name in (
        "heroes_json",
        "troops_json",
        "spells_json",
        "achievements_json",
    ):
        assert isinstance(columns[field_name].type, JSONB)
        assert columns[field_name].nullable is False

    expected_index_columns = ("player_tag", "snapshot_at")
    index_columns = {
        tuple(index.columns.keys())
        for index in table.indexes
        if isinstance(index, Index)
    }

    assert expected_index_columns in index_columns