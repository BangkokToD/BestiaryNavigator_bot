"""Тесты команды `/register` Telegram bot."""

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.bot.routers.register import create_register_router, handle_register_route_command
from app.core.settings import Settings
from app.db.models import Clan, NotificationRoute, TelegramChat, TelegramUser
from app.domain import ClanType, NotificationType
from app.services import NotificationRouteRegistrationResult

VALID_SETTINGS = {
    "APP_ENV": "local",
    "APP_BASE_URL": "http://localhost:8000",
    "DATABASE_URL": "postgresql+asyncpg://bn:bn@postgres:5432/bestiary",
    "CLASH_API_BASE_URL": "https://api.clashofclans.com/v1",
    "CLASH_API_TOKEN": "test-clash-token-123",
    "CLASH_API_TIMEOUT_SECONDS": 7,
    "TELEGRAM_BOT_TOKEN": "test-telegram-token-123",
    "TELEGRAM_ADMIN_ID": 123456789,
    "WEB_SESSION_SECRET": "test-session-secret-value-1234567890",
    "WEB_ADMIN_COOKIE_NAME": "bn_admin_session",
    "SYNC_DEFAULT_INTERVAL_SECONDS": 900,
    "ROLE_SNAPSHOT_MAX_AGE_MINUTES": 30,
}


@dataclass(slots=True)
class FakeChat:
    """Fake Telegram chat для bot-тестов."""

    id: int
    title: str | None
    type: str
    is_forum: bool = False


@dataclass(slots=True)
class FakeMessage:
    """Fake Telegram message для `/register` тестов."""

    text: str
    chat: FakeChat
    message_thread_id: int | None = None
    answers: list[dict[str, Any]] = field(default_factory=list)

    async def answer(self, text: str, **kwargs: object) -> None:
        """Фиксирует ответ handler-а.

        Args:
            text: Текст ответа.
            kwargs: Дополнительные параметры отправки.
        """
        self.answers.append({"text": text, **kwargs})


class FakeNotificationRouteService:
    """Fake notification route service для bot-тестов."""

    def __init__(self, *, result: NotificationRouteRegistrationResult | None = None) -> None:
        """Инициализирует fake service.

        Args:
            result: Результат регистрации route.
        """
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def register_route(
        self,
        *,
        clan_tag: str,
        notification_type: str,
        chat_id: int,
        message_thread_id: int | None,
        title: str,
        created_by: TelegramUser | None = None,
        chat_type: str = "supergroup",
        is_forum: bool = False,
    ) -> NotificationRouteRegistrationResult:
        """Фиксирует вызов регистрации route."""
        self.calls.append(
            {
                "clan_tag": clan_tag,
                "notification_type": notification_type,
                "chat_id": chat_id,
                "message_thread_id": message_thread_id,
                "title": title,
                "created_by": created_by,
                "chat_type": chat_type,
                "is_forum": is_forum,
            }
        )

        if self.result is None:
            return _make_registration_result(
                clan_tag=clan_tag,
                notification_type=notification_type,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                title=title,
                created=True,
            )

        return self.result


def make_settings(*, telegram_admin_id: int = 123456789) -> Settings:
    """Создаёт settings для bot-тестов.

    Args:
        telegram_admin_id: Telegram ID администратора.

    Returns:
        Провалидированный settings.
    """
    return Settings(
        **(
            VALID_SETTINGS
            | {
                "TELEGRAM_ADMIN_ID": telegram_admin_id,
            }
        )
    )


@pytest.mark.asyncio
async def test_register_route_command_registers_admin_group_route() -> None:
    """Проверяет регистрацию route админом в группе."""
    admin = _make_admin()
    service = FakeNotificationRouteService()
    message = FakeMessage(
        text="/register #2abc war_started",
        chat=FakeChat(
            id=-100123,
            title="War chat",
            type="supergroup",
            is_forum=False,
        ),
    )

    await handle_register_route_command(
        message,  # type: ignore[arg-type]
        admin,
        make_settings(),
        service,
    )

    assert service.calls == [
        {
            "clan_tag": "#2abc",
            "notification_type": "war_started",
            "chat_id": -100123,
            "message_thread_id": None,
            "title": "War chat",
            "created_by": admin,
            "chat_type": "supergroup",
            "is_forum": False,
        }
    ]
    assert message.answers == [
        {
            "text": "\n".join(
                (
                    "Маршрут уведомлений подключён.",
                    "Клан: #2ABC",
                    "Тип: war_started",
                    "Чат: War chat",
                )
            )
        }
    ]


@pytest.mark.asyncio
async def test_register_route_command_saves_forum_topic() -> None:
    """Проверяет сохранение message_thread_id для forum topic."""
    admin = _make_admin()
    service = FakeNotificationRouteService()
    message = FakeMessage(
        text="/register #2ABC raid_12h_report",
        chat=FakeChat(
            id=-100123,
            title="Raid topics",
            type="supergroup",
            is_forum=True,
        ),
        message_thread_id=321,
    )

    await handle_register_route_command(
        message,  # type: ignore[arg-type]
        admin,
        make_settings(),
        service,
    )

    assert service.calls[0]["message_thread_id"] == 321
    assert service.calls[0]["is_forum"] is True
    assert "Топик: 321" in message.answers[0]["text"]


@pytest.mark.asyncio
async def test_register_route_command_rejects_non_admin() -> None:
    """Проверяет отказ обычному пользователю."""
    service = FakeNotificationRouteService()
    message = FakeMessage(
        text="/register #2ABC war_started",
        chat=FakeChat(
            id=-100123,
            title="War chat",
            type="supergroup",
        ),
    )

    await handle_register_route_command(
        message,  # type: ignore[arg-type]
        TelegramUser(id=404, telegram_id=777),
        make_settings(),
        service,
    )

    assert service.calls == []
    assert message.answers == [
        {
            "text": "Команду /register может использовать только админ.",
        }
    ]


@pytest.mark.asyncio
async def test_register_route_command_rejects_private_chat() -> None:
    """Проверяет запрет регистрации route в личке."""
    service = FakeNotificationRouteService()
    message = FakeMessage(
        text="/register #2ABC war_started",
        chat=FakeChat(
            id=123456789,
            title=None,
            type="private",
        ),
    )

    await handle_register_route_command(
        message,  # type: ignore[arg-type]
        _make_admin(),
        make_settings(),
        service,
    )

    assert service.calls == []
    assert message.answers == [
        {
            "text": "Команда /register работает только в группах и топиках.",
        }
    ]


@pytest.mark.asyncio
async def test_register_route_command_updates_existing_route() -> None:
    """Проверяет короткий ответ для повторной регистрации route."""
    service = FakeNotificationRouteService(
        result=_make_registration_result(
            clan_tag="#2ABC",
            notification_type="warn_created",
            chat_id=-100123,
            message_thread_id=None,
            title="Warn chat",
            created=False,
        )
    )
    message = FakeMessage(
        text="/register #2ABC warn_created",
        chat=FakeChat(
            id=-100123,
            title="Warn chat",
            type="supergroup",
        ),
    )

    await handle_register_route_command(
        message,  # type: ignore[arg-type]
        _make_admin(),
        make_settings(),
        service,
    )

    assert len(service.calls) == 1
    assert "Маршрут уведомлений обновлён." in message.answers[0]["text"]


@pytest.mark.asyncio
async def test_register_route_command_rejects_invalid_format() -> None:
    """Проверяет понятный ответ при неправильном формате."""
    service = FakeNotificationRouteService()
    message = FakeMessage(
        text="/register #2ABC",
        chat=FakeChat(
            id=-100123,
            title="War chat",
            type="supergroup",
        ),
    )

    await handle_register_route_command(
        message,  # type: ignore[arg-type]
        _make_admin(),
        make_settings(),
        service,
    )

    assert service.calls == []
    assert "/register #CLAN_TAG notification_type" in message.answers[0]["text"]


def test_create_register_router_registers_command_handler() -> None:
    """Проверяет регистрацию command handler-а."""
    router = create_register_router()

    assert router.name == "register"
    assert len(router.message.handlers) == 1


def _make_admin() -> TelegramUser:
    """Создаёт admin TelegramUser для тестов."""
    return TelegramUser(
        id=101,
        telegram_id=123456789,
        username="admin",
        display_name="Admin",
    )


def _make_registration_result(
    *,
    clan_tag: str,
    notification_type: str,
    chat_id: int,
    message_thread_id: int | None,
    title: str,
    created: bool,
) -> NotificationRouteRegistrationResult:
    """Создаёт результат регистрации route.

    Args:
        clan_tag: Тег клана.
        notification_type: Тип уведомления.
        chat_id: Telegram chat id.
        message_thread_id: Topic id.
        title: Название чата.
        created: Был ли route создан.

    Returns:
        Результат регистрации route.
    """
    clan = Clan(
        id=7,
        tag="#2ABC",
        name="Bestiary",
        type=ClanType.MAIN.value,
        is_active=True,
    )
    chat = TelegramChat(
        id=11,
        chat_id=chat_id,
        title=title,
        type="supergroup",
        is_forum=message_thread_id is not None,
    )
    route = NotificationRoute(
        id=17,
        clan_id=7,
        clan=clan,
        notification_type=NotificationType(notification_type).value,
        chat_id=chat_id,
        chat=chat,
        message_thread_id=message_thread_id,
        enabled=True,
    )
    return NotificationRouteRegistrationResult(
        route=route,
        chat=chat,
        created=created,
    )
