"""Тесты сервиса маршрутов Telegram-уведомлений."""

import pytest

from app.db.models import Clan, NotificationRoute, TelegramChat, TelegramUser
from app.domain import DomainValidationError, NotificationType
from app.services import (
    NotificationRouteRegistrationResult,
    NotificationRouteService,
    NotificationRouteServiceError,
)


class InMemoryNotificationRouteRepository:
    """In-memory repository для unit-тестов NotificationRouteService."""

    def __init__(
        self,
        *,
        clans: list[Clan] | None = None,
        chats: list[TelegramChat] | None = None,
        routes: list[NotificationRoute] | None = None,
    ) -> None:
        """Инициализирует repository.

        Args:
            clans: Начальный набор кланов.
            chats: Начальный набор Telegram-чатов.
            routes: Начальный набор маршрутов.
        """
        self.clans = {clan.tag: clan for clan in clans or []}
        self.chats = {chat.chat_id: chat for chat in chats or []}
        self.routes = routes or []
        self.added_chats: list[TelegramChat] = []
        self.added_routes: list[NotificationRoute] = []
        self.flush_count = 0

    async def get_clan_by_tag(self, clan_tag: str) -> Clan | None:
        """Возвращает клан по нормализованному тегу."""
        return self.clans.get(clan_tag)

    async def get_chat_by_id(self, chat_id: int) -> TelegramChat | None:
        """Возвращает TelegramChat по chat_id."""
        return self.chats.get(chat_id)

    def add_chat(self, chat: TelegramChat) -> None:
        """Добавляет TelegramChat в in-memory storage."""
        self.added_chats.append(chat)
        self.chats[chat.chat_id] = chat

    async def get_route(
        self,
        *,
        clan_id: int,
        notification_type: str,
        chat_id: int,
        message_thread_id: int | None,
    ) -> NotificationRoute | None:
        """Возвращает route по unique key."""
        for route in self.routes:
            if (
                route.clan_id == clan_id
                and route.notification_type == notification_type
                and route.chat_id == chat_id
                and route.message_thread_id == message_thread_id
            ):
                return route

        return None

    async def get_route_by_id(self, route_id: int) -> NotificationRoute | None:
        """Возвращает route по DB ID."""
        for route in self.routes:
            if route.id == route_id:
                return route

        return None

    async def list_all_routes(self, *, include_disabled: bool) -> tuple[NotificationRoute, ...]:
        """Возвращает все routes."""
        return tuple(route for route in self.routes if include_disabled or route.enabled)

    async def list_routes_by_clan_and_type(
        self,
        *,
        clan_id: int,
        notification_type: str,
        include_disabled: bool,
    ) -> list[NotificationRoute]:
        """Возвращает routes по клану и типу уведомления."""
        return [
            route
            for route in self.routes
            if route.clan_id == clan_id
            and route.notification_type == notification_type
            and (include_disabled or route.enabled)
        ]

    def add_route(self, route: NotificationRoute) -> None:
        """Добавляет route в in-memory storage."""
        self.added_routes.append(route)
        self.routes.append(route)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


def make_clan(*, clan_id: int = 7, tag: str = "#2ABC") -> Clan:
    """Создаёт Clan для unit-тестов."""
    return Clan(
        id=clan_id,
        tag=tag,
        name="Bestiary",
        type="main",
        is_active=True,
    )


def make_admin(*, user_id: int = 101, telegram_id: int = 42) -> TelegramUser:
    """Создаёт TelegramUser для unit-тестов."""
    return TelegramUser(
        id=user_id,
        telegram_id=telegram_id,
        username="admin",
        display_name="Admin",
    )


def make_chat(
    *,
    chat_id: int = -100123,
    title: str = "Old title",
    chat_type: str = "supergroup",
    is_forum: bool = True,
) -> TelegramChat:
    """Создаёт TelegramChat для unit-тестов."""
    return TelegramChat(
        chat_id=chat_id,
        title=title,
        type=chat_type,
        is_forum=is_forum,
    )


def make_route(
    *,
    route_id: int = 1,
    clan_id: int = 7,
    notification_type: NotificationType = NotificationType.WAR_STARTED,
    chat_id: int = -100123,
    message_thread_id: int | None = 321,
    enabled: bool = True,
) -> NotificationRoute:
    """Создаёт NotificationRoute для unit-тестов."""
    return NotificationRoute(
        id=route_id,
        clan_id=clan_id,
        notification_type=notification_type.value,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        enabled=enabled,
    )


@pytest.mark.asyncio
async def test_notification_route_service_registers_new_route_and_chat() -> None:
    """Проверяет регистрацию нового маршрута и TelegramChat."""
    clan = make_clan()
    admin = make_admin()
    repository = InMemoryNotificationRouteRepository(clans=[clan])
    service = NotificationRouteService(repository=repository)

    result = await service.register_route(
        clan_tag="2abc",
        notification_type="war_started",
        chat_id=-100123,
        message_thread_id=321,
        title=" War topic ",
        created_by=admin,
        chat_type="supergroup",
        is_forum=True,
    )

    assert isinstance(result, NotificationRouteRegistrationResult)
    assert result.created is True
    assert repository.flush_count == 1
    assert repository.added_chats == [result.chat]
    assert repository.added_routes == [result.route]

    assert result.chat.chat_id == -100123
    assert result.chat.title == "War topic"
    assert result.chat.type == "supergroup"
    assert result.chat.is_forum is True

    assert result.route.clan_id == 7
    assert result.route.clan is clan
    assert result.route.notification_type == NotificationType.WAR_STARTED.value
    assert result.route.chat_id == -100123
    assert result.route.chat is result.chat
    assert result.route.message_thread_id == 321
    assert result.route.enabled is True
    assert result.route.created_by_telegram_user_id == 101
    assert result.route.created_by_user is admin


@pytest.mark.asyncio
async def test_notification_route_service_reregisters_disabled_route_without_duplicate() -> None:
    """Проверяет повторную регистрацию disabled route без дубля."""
    clan = make_clan()
    chat = make_chat(title="Old title")
    route = make_route(enabled=False)
    admin = make_admin(user_id=202, telegram_id=777)
    repository = InMemoryNotificationRouteRepository(
        clans=[clan],
        chats=[chat],
        routes=[route],
    )
    service = NotificationRouteService(repository=repository)

    result = await service.register_route(
        clan_tag="#2abc",
        notification_type=NotificationType.WAR_STARTED,
        chat_id=-100123,
        message_thread_id=321,
        title="New title",
        created_by=admin,
        chat_type="supergroup",
        is_forum=True,
    )

    assert result.created is False
    assert result.route is route
    assert result.chat is chat
    assert repository.added_chats == []
    assert repository.added_routes == []
    assert repository.flush_count == 1

    assert chat.title == "New title"
    assert route.enabled is True
    assert route.created_by_telegram_user_id == 202
    assert route.created_by_user is admin
    assert route.chat is chat


@pytest.mark.asyncio
async def test_notification_route_service_lists_enabled_routes_by_default() -> None:
    """Проверяет поиск routes по clan + notification type."""
    clan = make_clan()
    enabled_route = make_route(route_id=1, enabled=True)
    disabled_route = make_route(route_id=2, chat_id=-100456, enabled=False)
    other_type_route = make_route(
        route_id=3,
        notification_type=NotificationType.RAID_STARTED,
        enabled=True,
    )
    repository = InMemoryNotificationRouteRepository(
        clans=[clan],
        routes=[enabled_route, disabled_route, other_type_route],
    )
    service = NotificationRouteService(repository=repository)

    enabled_routes = await service.list_routes(
        clan_tag="#2ABC",
        notification_type="war_started",
    )
    all_routes = await service.list_routes(
        clan_tag="#2ABC",
        notification_type="war_started",
        include_disabled=True,
    )

    assert enabled_routes == [enabled_route]
    assert all_routes == [enabled_route, disabled_route]


@pytest.mark.asyncio
async def test_notification_route_service_lists_all_routes_for_admin_ui() -> None:
    """Проверяет список всех routes для admin UI."""
    enabled_route = make_route(route_id=1, enabled=True)
    disabled_route = make_route(route_id=2, chat_id=-100456, enabled=False)
    repository = InMemoryNotificationRouteRepository(
        routes=[enabled_route, disabled_route],
    )
    service = NotificationRouteService(repository=repository)

    all_routes = await service.list_all_routes()
    enabled_routes = await service.list_all_routes(include_disabled=False)

    assert all_routes == (enabled_route, disabled_route)
    assert enabled_routes == (enabled_route,)


@pytest.mark.asyncio
async def test_notification_route_service_gets_route_by_id() -> None:
    """Проверяет public lookup route по DB ID."""
    route = make_route(route_id=7)
    repository = InMemoryNotificationRouteRepository(routes=[route])
    service = NotificationRouteService(repository=repository)

    assert await service.get_route_by_id(route_id=7) is route


@pytest.mark.asyncio
async def test_notification_route_service_disables_and_enables_route() -> None:
    """Проверяет disable/enable route."""
    route = make_route(route_id=1, enabled=True)
    repository = InMemoryNotificationRouteRepository(routes=[route])
    service = NotificationRouteService(repository=repository)

    disabled_result = await service.disable_route(route_id=1)

    assert disabled_result.route is route
    assert disabled_result.changed is True
    assert route.enabled is False
    assert repository.flush_count == 1

    repeated_disable_result = await service.disable_route(route_id=1)

    assert repeated_disable_result.route is route
    assert repeated_disable_result.changed is False
    assert repository.flush_count == 1

    enabled_result = await service.enable_route(route_id=1)

    assert enabled_result.route is route
    assert enabled_result.changed is True
    assert route.enabled is True
    assert repository.flush_count == 2


@pytest.mark.asyncio
async def test_notification_route_service_rejects_invalid_notification_type() -> None:
    """Проверяет валидацию notification type."""
    repository = InMemoryNotificationRouteRepository(clans=[make_clan()])
    service = NotificationRouteService(repository=repository)

    with pytest.raises(DomainValidationError):
        await service.register_route(
            clan_tag="#2ABC",
            notification_type="unknown_type",
            chat_id=-100123,
            message_thread_id=None,
            title="Chat",
        )

    assert repository.added_chats == []
    assert repository.added_routes == []
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_notification_route_service_rejects_zero_message_thread_id() -> None:
    """Проверяет запрет sentinel message_thread_id=0."""
    repository = InMemoryNotificationRouteRepository(clans=[make_clan()])
    service = NotificationRouteService(repository=repository)

    with pytest.raises(DomainValidationError):
        await service.register_route(
            clan_tag="#2ABC",
            notification_type=NotificationType.WAR_STARTED,
            chat_id=-100123,
            message_thread_id=0,
            title="Chat",
        )

    assert repository.added_chats == []
    assert repository.added_routes == []
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_notification_route_service_rejects_zero_chat_id() -> None:
    """Проверяет запрет chat_id=0."""
    repository = InMemoryNotificationRouteRepository(clans=[make_clan()])
    service = NotificationRouteService(repository=repository)

    with pytest.raises(NotificationRouteServiceError):
        await service.register_route(
            clan_tag="#2ABC",
            notification_type=NotificationType.WAR_STARTED,
            chat_id=0,
            message_thread_id=None,
            title="Chat",
        )

    assert repository.added_chats == []
    assert repository.added_routes == []
    assert repository.flush_count == 0
