"""Тесты моделей войн, ЛВК и рейдов."""

from sqlalchemy import DateTime, Numeric, String, inspect

from app.db.models import (
    Clan,
    CwlSeason,
    CwlWar,
    RaidMember,
    RaidSeason,
    WarAttack,
    WarMember,
    WarSnapshot,
)


def get_fk_targets(column: object) -> set[str]:
    """Возвращает target_fullname всех FK колонки.

    Args:
        column: SQLAlchemy column.

    Returns:
        Набор строк вида `table.column`.
    """
    return {foreign_key.target_fullname for foreign_key in column.foreign_keys}


def assert_no_ondelete_cascade(column: object) -> None:
    """Проверяет, что FK не использует каскадное удаление.

    Args:
        column: SQLAlchemy column.
    """
    assert column.foreign_keys
    assert all(foreign_key.ondelete is None for foreign_key in column.foreign_keys)


def test_war_snapshot_model_contract() -> None:
    """Проверяет контракт модели WarSnapshot."""
    table = WarSnapshot.__table__
    columns = table.c

    assert table.name == "war_snapshots"
    assert get_fk_targets(columns.clan_id) == {"clans.id"}
    assert columns.war_event_key.unique is True
    assert columns.war_event_key.nullable is False
    assert columns.war_tag.nullable is True
    assert isinstance(columns.state.type, String)
    assert isinstance(columns.preparation_start_time.type, DateTime)
    assert isinstance(columns.start_time.type, DateTime)
    assert isinstance(columns.end_time.type, DateTime)
    assert columns.preparation_start_time.type.timezone is True
    assert columns.start_time.type.timezone is True
    assert columns.end_time.type.timezone is True
    assert isinstance(columns.our_destruction.type, Numeric)
    assert isinstance(columns.opponent_destruction.type, Numeric)

    assert_no_ondelete_cascade(columns.clan_id)

    mapper = inspect(WarSnapshot)

    assert mapper.relationships["clan"].mapper.class_ is Clan


def test_war_member_model_contract_and_fk_policy() -> None:
    """Проверяет контракт модели WarMember и FK без cascade-delete."""
    table = WarMember.__table__
    columns = table.c

    assert table.name == "war_members"
    assert get_fk_targets(columns.war_snapshot_id) == {"war_snapshots.id"}
    assert columns.side.nullable is False
    assert columns.player_tag.nullable is False
    assert columns.name.nullable is False
    assert columns.attacks_done.nullable is False
    assert columns.attacks_left.nullable is False

    assert_no_ondelete_cascade(columns.war_snapshot_id)

    mapper = inspect(WarMember)

    assert mapper.relationships["war_snapshot"].mapper.class_ is WarSnapshot


def test_war_attack_model_contract_and_fk_policy() -> None:
    """Проверяет контракт модели WarAttack и FK без cascade-delete."""
    table = WarAttack.__table__
    columns = table.c

    assert table.name == "war_attacks"
    assert get_fk_targets(columns.war_snapshot_id) == {"war_snapshots.id"}
    assert columns.attacker_tag.nullable is False
    assert columns.defender_tag.nullable is False
    assert columns.stars.nullable is False
    assert isinstance(columns.destruction_percentage.type, Numeric)
    assert columns.order.nullable is False

    assert_no_ondelete_cascade(columns.war_snapshot_id)

    mapper = inspect(WarAttack)

    assert mapper.relationships["war_snapshot"].mapper.class_ is WarSnapshot


def test_cwl_season_model_contract_and_fk_policy() -> None:
    """Проверяет контракт модели CwlSeason и FK без cascade-delete."""
    table = CwlSeason.__table__
    columns = table.c

    assert table.name == "cwl_seasons"
    assert get_fk_targets(columns.clan_id) == {"clans.id"}
    assert columns.season.nullable is False
    assert isinstance(columns.state.type, String)
    assert isinstance(columns.started_at.type, DateTime)
    assert columns.started_at.type.timezone is True
    assert columns.ended_at.nullable is True

    assert_no_ondelete_cascade(columns.clan_id)

    mapper = inspect(CwlSeason)

    assert mapper.relationships["clan"].mapper.class_ is Clan


def test_cwl_war_model_contract_and_unique_war_tag() -> None:
    """Проверяет контракт модели CwlWar и уникальность war_tag."""
    table = CwlWar.__table__
    columns = table.c

    assert table.name == "cwl_wars"
    assert get_fk_targets(columns.cwl_season_id) == {"cwl_seasons.id"}
    assert columns.round_number.nullable is False
    assert columns.war_tag.unique is True
    assert columns.war_tag.nullable is False
    assert isinstance(columns.state.type, String)
    assert columns.our_clan_tag.nullable is False
    assert columns.opponent_clan_tag.nullable is False
    assert isinstance(columns.our_destruction.type, Numeric)
    assert isinstance(columns.opponent_destruction.type, Numeric)

    assert_no_ondelete_cascade(columns.cwl_season_id)

    mapper = inspect(CwlWar)

    assert mapper.relationships["cwl_season"].mapper.class_ is CwlSeason


def test_raid_season_model_contract_and_fk_policy() -> None:
    """Проверяет контракт модели RaidSeason и FK без cascade-delete."""
    table = RaidSeason.__table__
    columns = table.c

    assert table.name == "raid_seasons"
    assert get_fk_targets(columns.clan_id) == {"clans.id"}
    assert isinstance(columns.state.type, String)
    assert isinstance(columns.start_time.type, DateTime)
    assert isinstance(columns.end_time.type, DateTime)
    assert columns.start_time.type.timezone is True
    assert columns.end_time.type.timezone is True
    assert columns.snapshot_at.type.timezone is True

    assert_no_ondelete_cascade(columns.clan_id)


def test_raid_member_model_contract_expected_attacks_and_status() -> None:
    """Проверяет контракт модели RaidMember, default 6 и статусное поле."""
    table = RaidMember.__table__
    columns = table.c

    assert table.name == "raid_members"
    assert get_fk_targets(columns.raid_season_id) == {"raid_seasons.id"}
    assert columns.player_tag.nullable is False
    assert columns.name.nullable is False
    assert columns.attacks.nullable is False
    assert columns.project_expected_attacks.nullable is False
    assert columns.project_expected_attacks.default is not None
    assert columns.project_expected_attacks.default.arg == 6
    assert columns.capital_resources_looted.nullable is False
    assert isinstance(columns.status.type, String)

    assert_no_ondelete_cascade(columns.raid_season_id)

    mapper = inspect(RaidMember)

    assert mapper.relationships["raid_season"].mapper.class_ is RaidSeason