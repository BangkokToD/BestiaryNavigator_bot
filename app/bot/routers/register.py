"""Router команды `/register` Telegram bot."""

from dataclasses import dataclass
from typing import Protocol

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.core.settings import Settings
from app.db.models import TelegramUser
from app.domain import DomainValidationError, normalize_clan_tag
from app.services import (
    NotificationRouteClanNotFoundError,
    NotificationRouteRegistrationResult,
    NotificationRouteServiceError,
)

_REGISTER_USAGE_MESSAGE = (
    "Формат команды: /register #CLAN_TAG notification_type\nПример: /register #2ABC war_started"
)
_ADMIN_ONLY_MESSAGE = "Команду /register может использовать только админ."
_GROUP_ONLY_MESSAGE = "Команда /register работает только в группах и топиках."
_REGISTER_FAILED_MESSAGE = (
    "Не удалось зарегистрировать маршрут. Проверь тег клана и тип уведомления."
)


@dataclass(frozen=True, slots=True)
class _RegisterCommandPayload:
    """Аргументы команды `/register`."""

    clan_tag: str
    notification_type: str


class RegisterNotificationRouteService(Protocol):
    """Contract сервиса регистрации Telegram route."""

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
        """Регистрирует notification route.

        Args:
            clan_tag: Тег клана.
            notification_type: Тип уведомления.
            chat_id: Telegram chat id.
            message_thread_id: Telegram topic/thread id.
            title: Название чата.
            created_by: Админ, который зарегистрировал route.
            chat_type: Тип Telegram-чата.
            is_forum: Является ли чат форумом.

        Returns:
            Результат регистрации route.
        """


async def handle_register_route_command(
    message: Message,
    telegram_user: TelegramUser | None,
    settings: Settings,
    notification_route_service: RegisterNotificationRouteService,
) -> None:
    """Регистрирует Telegram route уведомлений.

    Args:
        message: Сообщение с командой `/register`.
        telegram_user: Пользователь из middleware.
        settings: Runtime settings.
        notification_route_service: Сервис маршрутов уведомлений.
    """
    if not _is_admin_user(telegram_user=telegram_user, settings=settings):
        await message.answer(_ADMIN_ONLY_MESSAGE)
        return

    if _is_private_chat(message):
        await message.answer(_GROUP_ONLY_MESSAGE)
        return

    payload = _parse_register_command(message.text or "")
    if payload is None:
        await message.answer(_REGISTER_USAGE_MESSAGE)
        return

    try:
        result = await notification_route_service.register_route(
            clan_tag=payload.clan_tag,
            notification_type=payload.notification_type,
            chat_id=_chat_id(message),
            message_thread_id=_message_thread_id(message),
            title=_chat_title(message),
            created_by=telegram_user,
            chat_type=_chat_type(message),
            is_forum=_is_forum_chat(message),
        )
    except NotificationRouteClanNotFoundError as exc:
        await message.answer(str(exc))
        return
    except (DomainValidationError, NotificationRouteServiceError):
        await message.answer(_REGISTER_FAILED_MESSAGE)
        return

    await message.answer(
        _format_register_success_message(
            result=result,
            clan_tag=payload.clan_tag,
        )
    )


def create_register_router() -> Router:
    """Создаёт router команды `/register`.

    Returns:
        Router с обработчиком регистрации Telegram routes.
    """
    router = Router(name="register")
    router.message.register(handle_register_route_command, Command("register"))

    return router


def _parse_register_command(text: str) -> _RegisterCommandPayload | None:
    """Парсит аргументы команды `/register`.

    Args:
        text: Полный текст сообщения.

    Returns:
        Payload команды или `None`, если формат неверный.
    """
    parts = text.split()
    if len(parts) != 3:
        return None

    command = parts[0].split("@", maxsplit=1)[0]
    if command != "/register":
        return None

    return _RegisterCommandPayload(
        clan_tag=parts[1],
        notification_type=parts[2],
    )


def _is_admin_user(*, telegram_user: TelegramUser | None, settings: Settings) -> bool:
    """Проверяет, что команду выполняет админ из settings.

    Args:
        telegram_user: TelegramUser из middleware.
        settings: Runtime settings.

    Returns:
        `True`, если пользователь является админом.
    """
    return telegram_user is not None and telegram_user.telegram_id == settings.telegram_admin_id


def _is_private_chat(message: Message) -> bool:
    """Проверяет, что команда пришла из лички.

    Args:
        message: Входящее сообщение.

    Returns:
        `True`, если чат private.
    """
    return _chat_type(message) == "private"


def _is_forum_chat(message: Message) -> bool:
    """Проверяет, является ли чат форумом.

    Args:
        message: Входящее сообщение.

    Returns:
        `True`, если Telegram chat поддерживает topics.
    """
    return bool(getattr(message.chat, "is_forum", False))


def _chat_id(message: Message) -> int:
    """Возвращает Telegram chat id.

    Args:
        message: Входящее сообщение.

    Returns:
        Telegram chat id.
    """
    return int(message.chat.id)


def _message_thread_id(message: Message) -> int | None:
    """Возвращает Telegram topic/thread id.

    Args:
        message: Входящее сообщение.

    Returns:
        Topic id или `None`.
    """
    value = getattr(message, "message_thread_id", None)
    return value if isinstance(value, int) else None


def _chat_type(message: Message) -> str:
    """Возвращает тип Telegram-чата.

    Args:
        message: Входящее сообщение.

    Returns:
        Нормализованный тип чата.
    """
    return str(getattr(message.chat, "type", "")).strip().lower()


def _chat_title(message: Message) -> str:
    """Возвращает название Telegram-чата.

    Args:
        message: Входящее сообщение.

    Returns:
        Название чата или fallback.
    """
    title = getattr(message.chat, "title", None)
    if isinstance(title, str) and title.strip():
        return title.strip()

    return f"Chat {_chat_id(message)}"


def _format_register_success_message(
    *,
    result: NotificationRouteRegistrationResult,
    clan_tag: str,
) -> str:
    """Форматирует короткий ответ после регистрации route.

    Args:
        result: Результат регистрации route.
        clan_tag: Тег клана из команды.

    Returns:
        Текст ответа.
    """
    header = "Маршрут уведомлений подключён." if result.created else "Маршрут уведомлений обновлён."
    lines = [
        header,
        f"Клан: {normalize_clan_tag(clan_tag)}",
        f"Тип: {result.route.notification_type}",
        f"Чат: {result.chat.title}",
    ]

    if result.route.message_thread_id is not None:
        lines.append(f"Топик: {result.route.message_thread_id}")

    return "\n".join(lines)


__all__ = [
    "RegisterNotificationRouteService",
    "create_register_router",
    "handle_register_route_command",
]
