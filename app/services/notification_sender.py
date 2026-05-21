"""Sender-сервис Telegram-уведомлений."""

from dataclasses import dataclass
from typing import Any, Protocol

from app.core.settings import Settings, get_settings
from app.db.models import NotificationRoute
from app.services.notification_rendering import RenderedNotification


class TelegramNotificationSenderError(RuntimeError):
    """Базовая ошибка отправки Telegram-уведомления."""


@dataclass(frozen=True, slots=True)
class TelegramSendResult:
    """Результат успешной отправки Telegram-сообщения."""

    message_id: int


class TelegramNotificationSender(Protocol):
    """Contract sender-а Telegram-уведомлений."""

    async def send(
        self,
        *,
        route: NotificationRoute,
        notification: RenderedNotification,
    ) -> TelegramSendResult:
        """Отправляет уведомление в Telegram route.

        Args:
            route: Маршрут доставки уведомления.
            notification: Отрендеренное уведомление.

        Returns:
            Результат отправки.
        """


class AiogramTelegramNotificationSender:
    """Telegram sender на базе aiogram Bot.

    Импорт aiogram выполняется лениво в `from_settings()`, чтобы unit-тесты и
    import service layer не зависели от инициализации Telegram SDK.
    """

    def __init__(self, *, bot: Any) -> None:
        """Инициализирует sender.

        Args:
            bot: Экземпляр aiogram Bot или совместимый объект.
        """
        self._bot = bot

    @classmethod
    def from_settings(
        cls,
        *,
        settings: Settings | None = None,
    ) -> "AiogramTelegramNotificationSender":
        """Создаёт sender из runtime settings.

        Args:
            settings: Настройки приложения. Если не переданы, берутся через `get_settings()`.

        Returns:
            Настроенный sender.

        Raises:
            TelegramNotificationSenderError: Если aiogram недоступен.
        """
        runtime_settings = settings or get_settings()
        try:
            from aiogram import Bot
        except ImportError as exc:
            raise TelegramNotificationSenderError("aiogram не установлен.") from exc

        return cls(bot=Bot(token=runtime_settings.telegram_bot_token.get_secret_value()))

    async def __aenter__(self) -> "AiogramTelegramNotificationSender":
        """Возвращает sender для async context manager.

        Returns:
            Текущий sender.
        """
        return self

    async def __aexit__(self, *_: object) -> None:
        """Закрывает HTTP-сессию Telegram bot, если SDK это поддерживает."""
        session = getattr(self._bot, "session", None)
        close = getattr(session, "close", None)
        if close is not None:
            await close()

    async def send(
        self,
        *,
        route: NotificationRoute,
        notification: RenderedNotification,
    ) -> TelegramSendResult:
        """Отправляет plain text уведомление в Telegram.

        Args:
            route: Маршрут уведомления.
            notification: Отрендеренное уведомление.

        Returns:
            Результат отправки.

        Raises:
            TelegramNotificationSenderError: Если Telegram не вернул message_id.
        """
        kwargs: dict[str, object] = {
            "chat_id": route.chat_id,
            "text": notification.text,
        }
        if route.message_thread_id is not None:
            kwargs["message_thread_id"] = route.message_thread_id
        if notification.parse_mode is not None:
            kwargs["parse_mode"] = notification.parse_mode

        message = await self._bot.send_message(**kwargs)
        message_id = getattr(message, "message_id", None)
        if not isinstance(message_id, int) or message_id <= 0:
            raise TelegramNotificationSenderError("Telegram не вернул корректный message_id.")

        return TelegramSendResult(message_id=message_id)


__all__ = [
    "AiogramTelegramNotificationSender",
    "TelegramNotificationSender",
    "TelegramNotificationSenderError",
    "TelegramSendResult",
]
