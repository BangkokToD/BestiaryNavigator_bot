"""Тесты моделей Telegram, уведомлений, API errors, событий и app settings."""

from sqlalchemy import DateTime, String, inspect
from sqlalchemy.dialects.postgresql import JSONB

from app.db.models import (
    ApiError,
    AppSetting,
    Clan,
    NotificationLog,
    NotificationRoute,
    PlayerEvent,
    TelegramChat,
    TelegramUser,
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


def test_telegram_chat_model_contract() -> None:
    """Проверяет контракт модели TelegramChat."""
    table = TelegramChat.__table__
    columns = table.c

    assert table.name == "telegram_chats"
    assert columns.chat_id.unique is True
    assert columns.chat_id.nullable is False
    assert columns.title.nullable is False
    assert columns.type.nullable is False
    assert columns.is_forum.nullable is False
    assert columns.bot_is_admin.nullable is False
    assert columns.bot_can_restrict_members.nullable is False
    assert isinstance(columns.bot_permissions_json.type, JSONB)
    assert columns.bot_permissions_json.nullable is False
    assert columns.last_permissions_checked_at.nullable is True
    assert columns.last_permissions_checked_at.type.timezone is True


def test_notification_route_contract_and_unique_expression_index() -> None:
    """Проверяет контракт NotificationRoute и уникальность маршрута."""
    table = NotificationRoute.__table__
    columns = table.c

    assert table.name == "notification_routes"
    assert get_fk_targets(columns.clan_id) == {"clans.id"}
    assert get_fk_targets(columns.chat_id) == {"telegram_chats.chat_id"}
    assert get_fk_targets(columns.created_by_telegram_user_id) == {"telegram_users.id"}
    assert columns.notification_type.nullable is False
    assert columns.message_thread_id.nullable is True
    assert columns.enabled.nullable is False

    assert_no_ondelete_cascade(columns.clan_id)
    assert_no_ondelete_cascade(columns.chat_id)
    assert_no_ondelete_cascade(columns.created_by_telegram_user_id)

    route_unique_index = next(
        index
        for index in table.indexes
        if index.name == "uq_notification_routes_clan_notification_chat_thread"
    )

    assert route_unique_index.unique is True

    mapper = inspect(NotificationRoute)

    assert mapper.relationships["clan"].mapper.class_ is Clan
    assert mapper.relationships["chat"].mapper.class_ is TelegramChat
    assert mapper.relationships["created_by_user"].mapper.class_ is TelegramUser


def test_notification_log_contract_and_unique_nullable_event_key() -> None:
    """Проверяет контракт NotificationLog и event_key-дедупликацию."""
    table = NotificationLog.__table__
    columns = table.c

    assert table.name == "notification_logs"
    assert get_fk_targets(columns.route_id) == {"notification_routes.id"}
    assert get_fk_targets(columns.chat_id) == {"telegram_chats.chat_id"}
    assert columns.route_id.nullable is True
    assert columns.notification_type.nullable is False
    assert columns.event_key.unique is True
    assert columns.event_key.nullable is True
    assert columns.message_thread_id.nullable is True
    assert columns.status.nullable is False
    assert columns.telegram_message_id.nullable is True
    assert columns.payload_summary.nullable is False
    assert columns.error_text.nullable is True
    assert columns.created_at.type.timezone is True

    assert_no_ondelete_cascade(columns.route_id)
    assert_no_ondelete_cascade(columns.chat_id)

    mapper = inspect(NotificationLog)

    assert mapper.relationships["route"].mapper.class_ is NotificationRoute
    assert mapper.relationships["chat"].mapper.class_ is TelegramChat


def test_api_error_model_contract() -> None:
    """Проверяет контракт модели ApiError."""
    table = ApiError.__table__
    columns = table.c

    assert table.name == "api_errors"
    assert columns.endpoint.nullable is False
    assert columns.method.nullable is False
    assert columns.entity_type.nullable is True
    assert columns.entity_tag.nullable is True
    assert columns.status_code.nullable is True
    assert columns.message.nullable is False
    assert columns.response_snippet.nullable is True
    assert columns.exception_class.nullable is True
    assert columns.worker_name.nullable is True
    assert columns.retry_count.nullable is False
    assert columns.status.nullable is False
    assert columns.created_at.type.timezone is True
    assert columns.resolved_at.nullable is True
    assert columns.resolved_at.type.timezone is True


def test_player_event_contract_and_history_semantics() -> None:
    """Проверяет контракт PlayerEvent как истории игрока, а не audit log."""
    table = PlayerEvent.__table__
    columns = table.c

    assert table.name == "player_events"
    assert get_fk_targets(columns.telegram_user_id) == {"telegram_users.id"}
    assert columns.telegram_user_id.nullable is True
    assert columns.player_tag.nullable is True
    assert columns.event_type.nullable is False
    assert columns.title.nullable is False
    assert columns.description.nullable is True
    assert isinstance(columns.metadata_json.type, JSONB)
    assert columns.metadata_json.nullable is False
    assert columns.created_at.type.timezone is True

    assert "action" not in columns
    assert "before_json" not in columns
    assert "after_json" not in columns
    assert "ip_address" not in columns

    assert_no_ondelete_cascade(columns.telegram_user_id)

    mapper = inspect(PlayerEvent)

    assert mapper.relationships["telegram_user"].mapper.class_ is TelegramUser


def test_app_setting_uses_key_as_primary_key() -> None:
    """Проверяет key-value контракт модели AppSetting."""
    table = AppSetting.__table__
    columns = table.c

    assert table.name == "app_settings"
    assert columns.key.primary_key is True
    assert "id" not in columns
    assert isinstance(columns.value_json.type, JSONB)
    assert columns.value_json.nullable is False
    assert isinstance(columns.updated_at.type, DateTime)
    assert columns.updated_at.type.timezone is True


def test_status_like_fields_are_plain_strings_before_domain_enums() -> None:
    """Проверяет подготовку статусных полей под будущие доменные enum."""
    assert isinstance(NotificationRoute.__table__.c.notification_type.type, String)
    assert isinstance(NotificationLog.__table__.c.notification_type.type, String)
    assert isinstance(NotificationLog.__table__.c.status.type, String)
    assert isinstance(ApiError.__table__.c.status.type, String)
    assert isinstance(PlayerEvent.__table__.c.event_type.type, String)
