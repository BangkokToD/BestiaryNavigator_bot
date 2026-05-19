"""Тесты моделей warn и кандидатов на кик."""

from sqlalchemy import DateTime, String, inspect
from sqlalchemy.dialects.postgresql import JSONB

from app.db.models import Clan, CwlSeason, KickCandidate, TelegramUser, Warning


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


def test_warning_model_contract_and_unique_nullable_event_key() -> None:
    """Проверяет основной контракт модели Warning."""
    table = Warning.__table__
    columns = table.c

    assert table.name == "warnings"
    assert get_fk_targets(columns.telegram_user_id) == {"telegram_users.id"}
    assert columns.telegram_user_id.nullable is False
    assert columns.source.nullable is False
    assert isinstance(columns.source.type, String)
    assert columns.status.nullable is False
    assert isinstance(columns.status.type, String)
    assert columns.reason_code.nullable is False
    assert columns.category.nullable is False
    assert columns.is_impactful.nullable is False
    assert columns.event_key.unique is True
    assert columns.event_key.nullable is True

    assert_no_ondelete_cascade(columns.telegram_user_id)


def test_warning_cancellation_expiration_and_context_fields_are_nullable() -> None:
    """Проверяет nullable-поля отмены, истечения и контекста warn."""
    columns = Warning.__table__.c

    assert columns.comment.nullable is True
    assert columns.author_telegram_user_id.nullable is True
    assert columns.clan_id.nullable is True
    assert columns.created_cwl_season_key.nullable is True
    assert columns.active_until_cwl_season_id.nullable is True
    assert columns.expired_at.nullable is True
    assert columns.cancelled_at.nullable is True
    assert columns.cancelled_by_telegram_user_id.nullable is True
    assert columns.cancelled_reason.nullable is True

    assert get_fk_targets(columns.author_telegram_user_id) == {"telegram_users.id"}
    assert get_fk_targets(columns.cancelled_by_telegram_user_id) == {"telegram_users.id"}
    assert get_fk_targets(columns.clan_id) == {"clans.id"}
    assert get_fk_targets(columns.active_until_cwl_season_id) == {"cwl_seasons.id"}

    assert_no_ondelete_cascade(columns.author_telegram_user_id)
    assert_no_ondelete_cascade(columns.cancelled_by_telegram_user_id)
    assert_no_ondelete_cascade(columns.clan_id)
    assert_no_ondelete_cascade(columns.active_until_cwl_season_id)

    assert isinstance(columns.expired_at.type, DateTime)
    assert isinstance(columns.cancelled_at.type, DateTime)
    assert columns.expired_at.type.timezone is True
    assert columns.cancelled_at.type.timezone is True


def test_warning_affected_accounts_are_jsonb_fields() -> None:
    """Проверяет JSONB-поля затронутых аккаунтов warn."""
    columns = Warning.__table__.c

    assert isinstance(columns.affected_player_tags_json.type, JSONB)
    assert isinstance(columns.affected_player_names_json.type, JSONB)
    assert columns.affected_player_tags_json.nullable is False
    assert columns.affected_player_names_json.nullable is False


def test_warning_relationships_target_expected_models() -> None:
    """Проверяет связи Warning с пользователем, кланом и сезоном ЛВК."""
    mapper = inspect(Warning)

    assert mapper.relationships["telegram_user"].mapper.class_ is TelegramUser
    assert mapper.relationships["author"].mapper.class_ is TelegramUser
    assert mapper.relationships["cancelled_by"].mapper.class_ is TelegramUser
    assert mapper.relationships["clan"].mapper.class_ is Clan
    assert mapper.relationships["active_until_cwl_season"].mapper.class_ is CwlSeason


def test_kick_candidate_model_contract_and_unique_nullable_event_key() -> None:
    """Проверяет основной контракт модели KickCandidate."""
    table = KickCandidate.__table__
    columns = table.c

    assert table.name == "kick_candidates"
    assert columns.telegram_user_id.nullable is True
    assert columns.player_tag.nullable is True
    assert columns.reason_code.nullable is False
    assert columns.status.nullable is False
    assert isinstance(columns.status.type, String)
    assert columns.event_key.unique is True
    assert columns.event_key.nullable is True
    assert columns.created_by.nullable is True

    assert get_fk_targets(columns.telegram_user_id) == {"telegram_users.id"}
    assert get_fk_targets(columns.created_by) == {"telegram_users.id"}

    assert_no_ondelete_cascade(columns.telegram_user_id)
    assert_no_ondelete_cascade(columns.created_by)


def test_kick_candidate_decision_fields_are_nullable() -> None:
    """Проверяет nullable decision-поля кандидата на кик."""
    columns = KickCandidate.__table__.c

    assert columns.deadline_at.nullable is True
    assert columns.decision_by_telegram_user_id.nullable is True
    assert columns.decision_at.nullable is True
    assert columns.decision_comment.nullable is True
    assert columns.executed_at.nullable is True

    assert get_fk_targets(columns.decision_by_telegram_user_id) == {"telegram_users.id"}
    assert_no_ondelete_cascade(columns.decision_by_telegram_user_id)

    assert isinstance(columns.deadline_at.type, DateTime)
    assert isinstance(columns.decision_at.type, DateTime)
    assert isinstance(columns.executed_at.type, DateTime)
    assert columns.deadline_at.type.timezone is True
    assert columns.decision_at.type.timezone is True
    assert columns.executed_at.type.timezone is True


def test_kick_candidate_relationships_target_expected_models() -> None:
    """Проверяет связи KickCandidate с TelegramUser."""
    mapper = inspect(KickCandidate)

    assert mapper.relationships["telegram_user"].mapper.class_ is TelegramUser
    assert mapper.relationships["created_by_user"].mapper.class_ is TelegramUser
    assert mapper.relationships["decision_by_user"].mapper.class_ is TelegramUser
