"""Сервис маршрутов Telegram-уведомлений."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Clan, NotificationRoute, TelegramChat, TelegramUser
from app.domain import (
    NotificationType,
    normalize_clan_tag,
    normalize_message_thread_id,
    require_domain_enum_value,
)


class NotificationRouteServiceError(RuntimeError):
    """Базовая ошибка сервиса маршрутов уведомлений."""


class NotificationRouteClanNotFoundError(NotificationRouteServiceError):
    """Клан для маршрута уведомлений не найден."""


class NotificationRouteNotFoundError(NotificationRouteServiceError):
    """Маршрут уведомлений не найден."""


@dataclass(frozen=True, slots=True)
class NotificationRouteRegistrationResult:
    """Результат регистрации маршрута уведомлений."""

    route: NotificationRoute
    chat: TelegramChat
    created: bool


@dataclass(frozen=True, slots=True)
class NotificationRouteStateResult:
    """Результат включения или отключения маршрута."""

    route: NotificationRoute
    changed: bool


@dataclass(frozen=True, slots=True)
class NotificationRouteMutationResult:
    """Совместимый alias результата изменения состояния route."""

    route: NotificationRoute
    changed: bool


class NotificationRouteRepository(Protocol):
    """Repository contract для маршрутов уведомлений."""

    async def get_clan_by_tag(self, clan_tag: str) -> Clan | None:
        """Возвращает клан по нормализованному тегу.

        Args:
            clan_tag: Нормализованный тег клана.

        Returns:
            Клан или `None`.
        """

    async def get_chat_by_id(self, chat_id: int) -> TelegramChat | None:
        """Возвращает TelegramChat по chat_id.

        Args:
            chat_id: Telegram chat id.

        Returns:
            TelegramChat или `None`.
        """

    def add_chat(self, chat: TelegramChat) -> None:
        """Добавляет TelegramChat в unit of work.

        Args:
            chat: Новая модель Telegram-чата.
        """

    async def get_route(
        self,
        *,
        clan_id: int,
        notification_type: str,
        chat_id: int,
        message_thread_id: int | None,
    ) -> NotificationRoute | None:
        """Возвращает route по unique key.

        Args:
            clan_id: DB ID клана.
            notification_type: Тип уведомления.
            chat_id: Telegram chat id.
            message_thread_id: Telegram topic/thread id или `None`.

        Returns:
            Route или `None`.
        """

    async def get_route_by_id(self, route_id: int) -> NotificationRoute | None:
        """Возвращает route по DB ID.

        Args:
            route_id: DB ID маршрута.

        Returns:
            Route или `None`.
        """

    async def list_all_routes(self, *, include_disabled: bool) -> tuple[NotificationRoute, ...]:
        """Возвращает все маршруты уведомлений.

        Args:
            include_disabled: Возвращать ли disabled routes.

        Returns:
            Tuple маршрутов уведомлений.
        """

    async def list_routes_by_clan_and_type(
        self,
        *,
        clan_id: int,
        notification_type: str,
        include_disabled: bool,
    ) -> list[NotificationRoute]:
        """Возвращает routes по клану и типу уведомления.

        Args:
            clan_id: DB ID клана.
            notification_type: Тип уведомления.
            include_disabled: Возвращать ли disabled routes.

        Returns:
            Список маршрутов.
        """

    def add_route(self, route: NotificationRoute) -> None:
        """Добавляет route в unit of work.

        Args:
            route: Новая модель маршрута.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyNotificationRouteRepository:
    """SQLAlchemy-реализация repository для маршрутов уведомлений."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_clan_by_tag(self, clan_tag: str) -> Clan | None:
        """Возвращает клан по нормализованному тегу."""
        result = await self._session.execute(select(Clan).where(Clan.tag == clan_tag))
        return result.scalar_one_or_none()

    async def get_chat_by_id(self, chat_id: int) -> TelegramChat | None:
        """Возвращает TelegramChat по chat_id."""
        result = await self._session.execute(
            select(TelegramChat).where(TelegramChat.chat_id == chat_id)
        )
        return result.scalar_one_or_none()

    def add_chat(self, chat: TelegramChat) -> None:
        """Добавляет TelegramChat в текущую session."""
        self._session.add(chat)

    async def get_route(
        self,
        *,
        clan_id: int,
        notification_type: str,
        chat_id: int,
        message_thread_id: int | None,
    ) -> NotificationRoute | None:
        """Возвращает route по unique key."""
        query = select(NotificationRoute).where(
            NotificationRoute.clan_id == clan_id,
            NotificationRoute.notification_type == notification_type,
            NotificationRoute.chat_id == chat_id,
        )

        if message_thread_id is None:
            query = query.where(NotificationRoute.message_thread_id.is_(None))
        else:
            query = query.where(NotificationRoute.message_thread_id == message_thread_id)

        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def get_route_by_id(self, route_id: int) -> NotificationRoute | None:
        """Возвращает route по DB ID."""
        result = await self._session.execute(
            select(NotificationRoute)
            .options(
                selectinload(NotificationRoute.clan),
                selectinload(NotificationRoute.chat),
            )
            .where(NotificationRoute.id == route_id)
        )
        return result.scalar_one_or_none()

    async def list_all_routes(self, *, include_disabled: bool) -> tuple[NotificationRoute, ...]:
        """Возвращает все маршруты уведомлений в стабильном порядке."""
        query = select(NotificationRoute).options(
            selectinload(NotificationRoute.clan),
            selectinload(NotificationRoute.chat),
        )

        if not include_disabled:
            query = query.where(NotificationRoute.enabled.is_(True))

        query = query.order_by(
            NotificationRoute.clan_id,
            NotificationRoute.notification_type,
            NotificationRoute.chat_id,
            NotificationRoute.message_thread_id,
        )
        result = await self._session.execute(query)
        return tuple(result.scalars().all())

    async def list_routes_by_clan_and_type(
        self,
        *,
        clan_id: int,
        notification_type: str,
        include_disabled: bool,
    ) -> list[NotificationRoute]:
        """Возвращает routes по клану и типу уведомления."""
        query = select(NotificationRoute).where(
            NotificationRoute.clan_id == clan_id,
            NotificationRoute.notification_type == notification_type,
        )

        if not include_disabled:
            query = query.where(NotificationRoute.enabled.is_(True))

        result = await self._session.execute(query)
        return list(result.scalars().all())

    def add_route(self, route: NotificationRoute) -> None:
        """Добавляет route в текущую session."""
        self._session.add(route)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class NotificationRouteService:
    """Сервис управления маршрутами уведомлений.

    Сервис регистрирует Telegram chat/topic routes, upsert-ит TelegramChat и
    обеспечивает идемпотентную регистрацию по unique key маршрута.
    """

    def __init__(self, *, repository: NotificationRouteRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository маршрутов уведомлений.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "NotificationRouteService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyNotificationRouteRepository(session))

    async def register_route(
        self,
        *,
        clan_tag: str,
        notification_type: NotificationType | str,
        chat_id: int,
        message_thread_id: int | None,
        title: str,
        created_by: TelegramUser | None = None,
        chat_type: str = "supergroup",
        is_forum: bool = False,
    ) -> NotificationRouteRegistrationResult:
        """Регистрирует или повторно включает route уведомлений.

        Args:
            clan_tag: Тег клана.
            notification_type: Тип уведомления.
            chat_id: Telegram chat id.
            message_thread_id: Telegram topic/thread id или `None`.
            title: Название Telegram-чата.
            created_by: TelegramUser администратора, если известен.
            chat_type: Тип Telegram-чата.
            is_forum: Является ли чат форумом с topics.

        Returns:
            Результат регистрации route.

        Raises:
            NotificationRouteClanNotFoundError: Если клан не найден.
        """
        clan = await self._get_required_clan(clan_tag)
        normalized_notification_type = _normalize_notification_type(notification_type)
        normalized_chat_id = _validate_chat_id(chat_id)
        normalized_thread_id = normalize_message_thread_id(message_thread_id)
        chat = await self._upsert_chat(
            chat_id=normalized_chat_id,
            title=title,
            chat_type=chat_type,
            is_forum=is_forum,
        )

        existing_route = await self._repository.get_route(
            clan_id=_required_model_id(clan, model_name="Clan"),
            notification_type=normalized_notification_type.value,
            chat_id=normalized_chat_id,
            message_thread_id=normalized_thread_id,
        )
        created_by_id = _optional_model_id(created_by, model_name="TelegramUser")

        if existing_route is not None:
            existing_route.enabled = True
            existing_route.created_by_telegram_user_id = created_by_id
            existing_route.created_by_user = created_by
            existing_route.chat = chat

            await self._repository.flush()

            return NotificationRouteRegistrationResult(
                route=existing_route,
                chat=chat,
                created=False,
            )

        route = NotificationRoute(
            clan_id=_required_model_id(clan, model_name="Clan"),
            clan=clan,
            notification_type=normalized_notification_type.value,
            chat_id=normalized_chat_id,
            chat=chat,
            message_thread_id=normalized_thread_id,
            enabled=True,
            created_by_telegram_user_id=created_by_id,
            created_by_user=created_by,
        )
        self._repository.add_route(route)
        await self._repository.flush()

        return NotificationRouteRegistrationResult(route=route, chat=chat, created=True)

    async def list_routes(
        self,
        *,
        clan_tag: str,
        notification_type: NotificationType | str,
        include_disabled: bool = False,
    ) -> list[NotificationRoute]:
        """Возвращает routes по клану и типу уведомления.

        По умолчанию возвращаются только enabled routes, чтобы sender не мог
        случайно отправить уведомление в отключённый маршрут.

        Args:
            clan_tag: Тег клана.
            notification_type: Тип уведомления.
            include_disabled: Вернуть ли disabled routes.

        Returns:
            Список маршрутов.
        """
        clan = await self._get_required_clan(clan_tag)
        normalized_notification_type = _normalize_notification_type(notification_type)

        return await self._repository.list_routes_by_clan_and_type(
            clan_id=_required_model_id(clan, model_name="Clan"),
            notification_type=normalized_notification_type.value,
            include_disabled=include_disabled,
        )

    async def list_all_routes(
        self,
        *,
        include_disabled: bool = True,
    ) -> tuple[NotificationRoute, ...]:
        """Возвращает все notification routes для admin UI.

        Args:
            include_disabled: Возвращать ли disabled routes.

        Returns:
            Tuple маршрутов уведомлений.
        """
        return await self._repository.list_all_routes(include_disabled=include_disabled)

    async def get_route_by_id(self, *, route_id: int) -> NotificationRoute:
        """Возвращает route по DB ID.

        Args:
            route_id: DB ID маршрута.

        Returns:
            Route уведомлений.

        Raises:
            NotificationRouteNotFoundError: Если route не найден.
        """
        return await self._get_required_route(route_id)

    async def disable_route(self, *, route_id: int) -> NotificationRouteStateResult:
        """Отключает route уведомлений.

        Args:
            route_id: DB ID маршрута.

        Returns:
            Результат отключения.
        """
        route = await self._get_required_route(route_id)
        if not route.enabled:
            return NotificationRouteStateResult(route=route, changed=False)

        route.enabled = False
        await self._repository.flush()

        return NotificationRouteStateResult(route=route, changed=True)

    async def enable_route(self, *, route_id: int) -> NotificationRouteStateResult:
        """Включает route уведомлений.

        Args:
            route_id: DB ID маршрута.

        Returns:
            Результат включения.
        """
        route = await self._get_required_route(route_id)
        if route.enabled:
            return NotificationRouteStateResult(route=route, changed=False)

        route.enabled = True
        await self._repository.flush()

        return NotificationRouteStateResult(route=route, changed=True)

    async def _upsert_chat(
        self,
        *,
        chat_id: int,
        title: str,
        chat_type: str,
        is_forum: bool,
    ) -> TelegramChat:
        """Создаёт или обновляет TelegramChat.

        Args:
            chat_id: Telegram chat id.
            title: Название Telegram-чата.
            chat_type: Тип Telegram-чата.
            is_forum: Является ли чат форумом.

        Returns:
            TelegramChat.
        """
        normalized_title = _normalize_required_text(title, field_name="title", max_length=255)
        normalized_type = _normalize_required_text(chat_type, field_name="chat_type", max_length=64)

        chat = await self._repository.get_chat_by_id(chat_id)
        if chat is None:
            chat = TelegramChat(
                chat_id=chat_id,
                title=normalized_title,
                type=normalized_type,
                is_forum=is_forum,
            )
            self._repository.add_chat(chat)
        else:
            chat.title = normalized_title
            chat.type = normalized_type
            chat.is_forum = is_forum

        return chat

    async def _get_required_clan(self, clan_tag: str) -> Clan:
        """Возвращает клан или выбрасывает service error.

        Args:
            clan_tag: Тег клана.

        Returns:
            Клан.

        Raises:
            NotificationRouteClanNotFoundError: Если клан не найден.
        """
        normalized_clan_tag = normalize_clan_tag(clan_tag)
        clan = await self._repository.get_clan_by_tag(normalized_clan_tag)
        if clan is None:
            raise NotificationRouteClanNotFoundError(f"Клан {normalized_clan_tag} не найден.")

        return clan

    async def _get_required_route(self, route_id: int) -> NotificationRoute:
        """Возвращает route или выбрасывает service error.

        Args:
            route_id: DB ID route.

        Returns:
            Route.

        Raises:
            NotificationRouteNotFoundError: Если route не найден.
        """
        normalized_route_id = _validate_positive_int(route_id, field_name="route_id")
        route = await self._repository.get_route_by_id(normalized_route_id)
        if route is None:
            raise NotificationRouteNotFoundError(f"Notification route {route_id} не найден.")

        return route


def _normalize_notification_type(value: NotificationType | str) -> NotificationType:
    """Валидирует notification type через доменный enum.

    Args:
        value: Тип уведомления.

    Returns:
        Enum member `NotificationType`.
    """
    return require_domain_enum_value(NotificationType, value, field_name="notification_type")


def _validate_chat_id(value: int) -> int:
    """Проверяет Telegram chat id.

    Telegram group/supergroup chat id может быть отрицательным, поэтому
    запрещаем только `0`, bool и не-int значения.

    Args:
        value: Telegram chat id.

    Returns:
        Проверенный chat id.

    Raises:
        NotificationRouteServiceError: Если chat id некорректный.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise NotificationRouteServiceError("chat_id должен быть целым числом.")

    if value == 0:
        raise NotificationRouteServiceError("chat_id не может быть 0.")

    return value


def _validate_positive_int(value: int, *, field_name: str) -> int:
    """Проверяет положительный integer.

    Args:
        value: Проверяемое значение.
        field_name: Название поля для текста ошибки.

    Returns:
        Проверенное значение.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise NotificationRouteServiceError(f"{field_name} должен быть целым числом.")

    if value <= 0:
        raise NotificationRouteServiceError(f"{field_name} должен быть положительным числом.")

    return value


def _normalize_required_text(value: str, *, field_name: str, max_length: int) -> str:
    """Нормализует обязательную строку.

    Args:
        value: Сырое значение.
        field_name: Имя поля для текста ошибки.
        max_length: Максимальная длина.

    Returns:
        Нормализованная строка.

    Raises:
        NotificationRouteServiceError: Если строка пустая.
    """
    if not isinstance(value, str):
        raise NotificationRouteServiceError(f"{field_name} должен быть строкой.")

    normalized = value.strip()
    if not normalized:
        raise NotificationRouteServiceError(f"{field_name} не может быть пустым.")

    return normalized[:max_length]


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.

    Raises:
        NotificationRouteServiceError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise NotificationRouteServiceError(f"{model_name} должен быть сохранён в БД.")


def _optional_model_id(model: object | None, *, model_name: str) -> int | None:
    """Достаёт опциональный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model или `None`.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id или `None`.
    """
    if model is None:
        return None

    return _required_model_id(model, model_name=model_name)


__all__ = [
    "NotificationRouteClanNotFoundError",
    "NotificationRouteMutationResult",
    "NotificationRouteNotFoundError",
    "NotificationRouteRegistrationResult",
    "NotificationRouteRepository",
    "NotificationRouteService",
    "NotificationRouteServiceError",
    "NotificationRouteStateResult",
    "SqlAlchemyNotificationRouteRepository",
]
