"""Сервис логирования Telegram-уведомлений."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import NotificationLog, NotificationRoute
from app.domain import NotificationType, normalize_message_thread_id, require_domain_enum_value

_NOTIFICATION_STATUS_SENT = "sent"
_NOTIFICATION_STATUS_FAILED = "failed"
_NOTIFICATION_STATUS_SKIPPED = "skipped"
_ALREADY_SENT_REASON = "already_sent"
_EVENT_KEY_MAX_LENGTH = 255
_PAYLOAD_SUMMARY_MAX_LENGTH = 2048
_ERROR_TEXT_MAX_LENGTH = 2048
_SENSITIVE_PAYLOAD_MARKERS = frozenset(
    {
        "token",
        "bearer",
        "authorization",
        "api_key",
        "password",
    }
)


class NotificationLogServiceError(RuntimeError):
    """Базовая ошибка сервиса логирования уведомлений."""


@dataclass(frozen=True, slots=True)
class NotificationLogResult:
    """Результат записи notification log."""

    log: NotificationLog
    created: bool
    updated: bool
    skipped: bool = False
    reason: str | None = None


class NotificationLogRepository(Protocol):
    """Repository contract для notification logs."""

    async def get_by_event_key(self, event_key: str) -> NotificationLog | None:
        """Возвращает notification log по event key.

        Args:
            event_key: Идемпотентный ключ уведомления.

        Returns:
            NotificationLog или `None`.
        """

    def add(self, notification_log: NotificationLog) -> None:
        """Добавляет notification log в unit of work.

        Args:
            notification_log: Новая модель лога.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyNotificationLogRepository:
    """SQLAlchemy-реализация repository для notification logs."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_event_key(self, event_key: str) -> NotificationLog | None:
        """Возвращает notification log по event key.

        Args:
            event_key: Идемпотентный ключ уведомления.

        Returns:
            NotificationLog или `None`.
        """
        result = await self._session.execute(
            select(NotificationLog).where(NotificationLog.event_key == event_key)
        )
        return result.scalar_one_or_none()

    def add(self, notification_log: NotificationLog) -> None:
        """Добавляет notification log в текущую session.

        Args:
            notification_log: Новая модель лога.
        """
        self._session.add(notification_log)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class NotificationLogService:
    """Сервис логирования результатов отправки Telegram-уведомлений.

    Сервис не отправляет сообщения в Telegram. Он фиксирует состояние отправки,
    защищает `payload_summary` от очевидных чувствительных данных и выполняет
    dedup по `event_key`.
    """

    def __init__(self, *, repository: NotificationLogRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository notification logs.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "NotificationLogService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyNotificationLogRepository(session))

    async def has_sent(self, *, event_key: str) -> bool:
        """Проверяет, был ли event уже успешно отправлен.

        Args:
            event_key: Идемпотентный ключ уведомления.

        Returns:
            `True`, если по ключу уже есть log со статусом `sent`.
        """
        normalized_event_key = _normalize_required_event_key(event_key)
        existing_log = await self._repository.get_by_event_key(normalized_event_key)

        return existing_log is not None and existing_log.status == _NOTIFICATION_STATUS_SENT

    async def record_sent(
        self,
        *,
        notification_type: NotificationType | str,
        payload_summary: str,
        telegram_message_id: int,
        event_key: str | None = None,
        route: NotificationRoute | None = None,
        chat_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> NotificationLogResult:
        """Фиксирует успешную отправку уведомления.

        Если по `event_key` уже есть failed log, он обновляется до `sent`.
        Если по `event_key` уже есть sent log, повторная запись пропускается.

        Args:
            notification_type: Тип уведомления.
            payload_summary: Безопасное краткое описание payload.
            telegram_message_id: ID сообщения Telegram.
            event_key: Идемпотентный ключ уведомления.
            route: Route, через который отправлялось уведомление.
            chat_id: Telegram chat id, если route не передан.
            message_thread_id: Topic/thread id, если route не передан.

        Returns:
            Результат записи лога.
        """
        normalized_event_key = _normalize_optional_event_key(event_key)
        normalized_summary = _normalize_payload_summary(payload_summary)
        normalized_message_id = _validate_positive_int(
            telegram_message_id,
            field_name="telegram_message_id",
        )
        target = _resolve_delivery_target(
            route=route,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
        )

        if normalized_event_key is not None:
            existing_log = await self._repository.get_by_event_key(normalized_event_key)
            if existing_log is not None:
                if existing_log.status == _NOTIFICATION_STATUS_SENT:
                    return NotificationLogResult(
                        log=existing_log,
                        created=False,
                        updated=False,
                        skipped=True,
                        reason=_ALREADY_SENT_REASON,
                    )

                _apply_log_values(
                    existing_log,
                    route=route,
                    notification_type=notification_type,
                    event_key=normalized_event_key,
                    chat_id=target.chat_id,
                    message_thread_id=target.message_thread_id,
                    status=_NOTIFICATION_STATUS_SENT,
                    telegram_message_id=normalized_message_id,
                    payload_summary=normalized_summary,
                    error_text=None,
                )
                await self._repository.flush()

                return NotificationLogResult(
                    log=existing_log,
                    created=False,
                    updated=True,
                )

        notification_log = _build_notification_log(
            route=route,
            notification_type=notification_type,
            event_key=normalized_event_key,
            chat_id=target.chat_id,
            message_thread_id=target.message_thread_id,
            status=_NOTIFICATION_STATUS_SENT,
            telegram_message_id=normalized_message_id,
            payload_summary=normalized_summary,
            error_text=None,
        )
        self._repository.add(notification_log)
        await self._repository.flush()

        return NotificationLogResult(
            log=notification_log,
            created=True,
            updated=False,
        )

    async def record_failed(
        self,
        *,
        notification_type: NotificationType | str,
        payload_summary: str,
        error_text: str,
        event_key: str | None = None,
        route: NotificationRoute | None = None,
        chat_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> NotificationLogResult:
        """Фиксирует ошибку отправки уведомления.

        Failed log с `event_key` может быть позже обновлён успешной отправкой
        через `record_sent`.

        Args:
            notification_type: Тип уведомления.
            payload_summary: Безопасное краткое описание payload.
            error_text: Текст ошибки отправки.
            event_key: Идемпотентный ключ уведомления.
            route: Route, через который отправлялось уведомление.
            chat_id: Telegram chat id, если route не передан.
            message_thread_id: Topic/thread id, если route не передан.

        Returns:
            Результат записи лога.
        """
        normalized_event_key = _normalize_optional_event_key(event_key)
        normalized_summary = _normalize_payload_summary(payload_summary)
        normalized_error = _normalize_required_text(
            error_text,
            field_name="error_text",
            max_length=_ERROR_TEXT_MAX_LENGTH,
        )
        target = _resolve_delivery_target(
            route=route,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
        )

        if normalized_event_key is not None:
            existing_log = await self._repository.get_by_event_key(normalized_event_key)
            if existing_log is not None:
                if existing_log.status == _NOTIFICATION_STATUS_SENT:
                    return NotificationLogResult(
                        log=existing_log,
                        created=False,
                        updated=False,
                        skipped=True,
                        reason=_ALREADY_SENT_REASON,
                    )

                _apply_log_values(
                    existing_log,
                    route=route,
                    notification_type=notification_type,
                    event_key=normalized_event_key,
                    chat_id=target.chat_id,
                    message_thread_id=target.message_thread_id,
                    status=_NOTIFICATION_STATUS_FAILED,
                    telegram_message_id=None,
                    payload_summary=normalized_summary,
                    error_text=normalized_error,
                )
                await self._repository.flush()

                return NotificationLogResult(
                    log=existing_log,
                    created=False,
                    updated=True,
                )

        notification_log = _build_notification_log(
            route=route,
            notification_type=notification_type,
            event_key=normalized_event_key,
            chat_id=target.chat_id,
            message_thread_id=target.message_thread_id,
            status=_NOTIFICATION_STATUS_FAILED,
            telegram_message_id=None,
            payload_summary=normalized_summary,
            error_text=normalized_error,
        )
        self._repository.add(notification_log)
        await self._repository.flush()

        return NotificationLogResult(
            log=notification_log,
            created=True,
            updated=False,
        )

    async def record_skipped(
        self,
        *,
        notification_type: NotificationType | str,
        payload_summary: str,
        event_key: str | None = None,
        route: NotificationRoute | None = None,
        chat_id: int | None = None,
        message_thread_id: int | None = None,
        reason: str | None = None,
    ) -> NotificationLogResult:
        """Фиксирует осознанно пропущенное уведомление.

        Args:
            notification_type: Тип уведомления.
            payload_summary: Безопасное краткое описание payload.
            event_key: Идемпотентный ключ уведомления.
            route: Route, через который уведомление должно было уйти.
            chat_id: Telegram chat id, если route не передан.
            message_thread_id: Topic/thread id, если route не передан.
            reason: Причина пропуска.

        Returns:
            Результат записи лога.
        """
        normalized_event_key = _normalize_optional_event_key(event_key)
        normalized_summary = _normalize_payload_summary(payload_summary)
        normalized_reason = _normalize_optional_text(reason, max_length=_ERROR_TEXT_MAX_LENGTH)
        target = _resolve_delivery_target(
            route=route,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
        )

        if normalized_event_key is not None:
            existing_log = await self._repository.get_by_event_key(normalized_event_key)
            if existing_log is not None:
                if existing_log.status == _NOTIFICATION_STATUS_SENT:
                    return NotificationLogResult(
                        log=existing_log,
                        created=False,
                        updated=False,
                        skipped=True,
                        reason=_ALREADY_SENT_REASON,
                    )

                _apply_log_values(
                    existing_log,
                    route=route,
                    notification_type=notification_type,
                    event_key=normalized_event_key,
                    chat_id=target.chat_id,
                    message_thread_id=target.message_thread_id,
                    status=_NOTIFICATION_STATUS_SKIPPED,
                    telegram_message_id=None,
                    payload_summary=normalized_summary,
                    error_text=normalized_reason,
                )
                await self._repository.flush()

                return NotificationLogResult(
                    log=existing_log,
                    created=False,
                    updated=True,
                )

        notification_log = _build_notification_log(
            route=route,
            notification_type=notification_type,
            event_key=normalized_event_key,
            chat_id=target.chat_id,
            message_thread_id=target.message_thread_id,
            status=_NOTIFICATION_STATUS_SKIPPED,
            telegram_message_id=None,
            payload_summary=normalized_summary,
            error_text=normalized_reason,
        )
        self._repository.add(notification_log)
        await self._repository.flush()

        return NotificationLogResult(
            log=notification_log,
            created=True,
            updated=False,
        )


@dataclass(frozen=True, slots=True)
class _DeliveryTarget:
    """Нормализованная цель доставки уведомления."""

    chat_id: int
    message_thread_id: int | None


def _build_notification_log(
    *,
    route: NotificationRoute | None,
    notification_type: NotificationType | str,
    event_key: str | None,
    chat_id: int,
    message_thread_id: int | None,
    status: str,
    telegram_message_id: int | None,
    payload_summary: str,
    error_text: str | None,
) -> NotificationLog:
    """Создаёт модель NotificationLog.

    Args:
        route: Route, связанный с отправкой.
        notification_type: Тип уведомления.
        event_key: Идемпотентный ключ.
        chat_id: Telegram chat id.
        message_thread_id: Telegram topic/thread id.
        status: Статус отправки.
        telegram_message_id: ID Telegram message для successful send.
        payload_summary: Безопасное краткое описание payload.
        error_text: Текст ошибки или причина пропуска.

    Returns:
        Новая модель NotificationLog.
    """
    notification_log = NotificationLog()
    _apply_log_values(
        notification_log,
        route=route,
        notification_type=notification_type,
        event_key=event_key,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        status=status,
        telegram_message_id=telegram_message_id,
        payload_summary=payload_summary,
        error_text=error_text,
    )

    return notification_log


def _apply_log_values(
    notification_log: NotificationLog,
    *,
    route: NotificationRoute | None,
    notification_type: NotificationType | str,
    event_key: str | None,
    chat_id: int,
    message_thread_id: int | None,
    status: str,
    telegram_message_id: int | None,
    payload_summary: str,
    error_text: str | None,
) -> None:
    """Применяет значения к модели NotificationLog.

    Args:
        notification_log: Модель лога.
        route: Route, связанный с отправкой.
        notification_type: Тип уведомления.
        event_key: Идемпотентный ключ.
        chat_id: Telegram chat id.
        message_thread_id: Telegram topic/thread id.
        status: Статус отправки.
        telegram_message_id: ID Telegram message для successful send.
        payload_summary: Безопасное краткое описание payload.
        error_text: Текст ошибки или причина пропуска.
    """
    notification_log.route_id = _optional_model_id(route)
    notification_log.route = route
    notification_log.notification_type = _normalize_notification_type(notification_type).value
    notification_log.event_key = event_key
    notification_log.chat_id = chat_id
    notification_log.message_thread_id = message_thread_id
    notification_log.status = status
    notification_log.telegram_message_id = telegram_message_id
    notification_log.payload_summary = payload_summary
    notification_log.error_text = error_text


def _resolve_delivery_target(
    *,
    route: NotificationRoute | None,
    chat_id: int | None,
    message_thread_id: int | None,
) -> _DeliveryTarget:
    """Определяет цель доставки по route или явным аргументам.

    Args:
        route: Route уведомления.
        chat_id: Telegram chat id, если route не передан.
        message_thread_id: Telegram topic/thread id, если route не передан.

    Returns:
        Нормализованная цель доставки.

    Raises:
        NotificationLogServiceError: Если chat id нельзя определить.
    """
    if route is not None:
        return _DeliveryTarget(
            chat_id=_validate_chat_id(route.chat_id),
            message_thread_id=normalize_message_thread_id(route.message_thread_id),
        )

    if chat_id is None:
        raise NotificationLogServiceError("chat_id обязателен, если route не передан.")

    return _DeliveryTarget(
        chat_id=_validate_chat_id(chat_id),
        message_thread_id=normalize_message_thread_id(message_thread_id),
    )


def _normalize_notification_type(value: NotificationType | str) -> NotificationType:
    """Валидирует notification type через доменный enum.

    Args:
        value: Тип уведомления.

    Returns:
        Enum member `NotificationType`.
    """
    return require_domain_enum_value(NotificationType, value, field_name="notification_type")


def _normalize_payload_summary(value: str) -> str:
    """Нормализует payload summary и проверяет sensitive markers.

    Args:
        value: Краткое описание payload.

    Returns:
        Обрезанное краткое описание.

    Raises:
        NotificationLogServiceError: Если summary пустой или похож на sensitive data.
    """
    normalized = _normalize_required_text(
        value,
        field_name="payload_summary",
        max_length=_PAYLOAD_SUMMARY_MAX_LENGTH,
    )
    lowered = normalized.lower()

    for marker in _SENSITIVE_PAYLOAD_MARKERS:
        if marker in lowered:
            raise NotificationLogServiceError(
                f"payload_summary содержит чувствительный маркер: {marker}."
            )

    return normalized


def _normalize_required_event_key(value: str) -> str:
    """Нормализует обязательный event key.

    Args:
        value: Event key.

    Returns:
        Нормализованный event key.
    """
    normalized = _normalize_optional_event_key(value)
    if normalized is None:
        raise NotificationLogServiceError("event_key не может быть пустым.")

    return normalized


def _normalize_optional_event_key(value: str | None) -> str | None:
    """Нормализует опциональный event key.

    Args:
        value: Event key или `None`.

    Returns:
        Нормализованный event key или `None`.

    Raises:
        NotificationLogServiceError: Если event key слишком длинный.
    """
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    if len(normalized) > _EVENT_KEY_MAX_LENGTH:
        raise NotificationLogServiceError("event_key слишком длинный.")

    return normalized


def _normalize_required_text(value: str, *, field_name: str, max_length: int) -> str:
    """Нормализует обязательную строку.

    Args:
        value: Сырое значение.
        field_name: Имя поля для текста ошибки.
        max_length: Максимальная длина.

    Returns:
        Нормализованная строка.

    Raises:
        NotificationLogServiceError: Если строка пустая.
    """
    if not isinstance(value, str):
        raise NotificationLogServiceError(f"{field_name} должен быть строкой.")

    normalized = value.strip()
    if not normalized:
        raise NotificationLogServiceError(f"{field_name} не может быть пустым.")

    return normalized[:max_length]


def _normalize_optional_text(value: str | None, *, max_length: int) -> str | None:
    """Нормализует опциональную строку.

    Args:
        value: Сырое значение.
        max_length: Максимальная длина.

    Returns:
        Нормализованная строка или `None`.
    """
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    return normalized[:max_length]


def _validate_chat_id(value: int) -> int:
    """Проверяет Telegram chat id.

    Telegram group/supergroup chat id может быть отрицательным, поэтому
    запрещаем только `0`, bool и не-int значения.

    Args:
        value: Telegram chat id.

    Returns:
        Проверенный chat id.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise NotificationLogServiceError("chat_id должен быть целым числом.")

    if value == 0:
        raise NotificationLogServiceError("chat_id не может быть 0.")

    return value


def _validate_positive_int(value: int, *, field_name: str) -> int:
    """Проверяет положительный integer.

    Args:
        value: Проверяемое значение.
        field_name: Имя поля для текста ошибки.

    Returns:
        Проверенное значение.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise NotificationLogServiceError(f"{field_name} должен быть целым числом.")

    if value <= 0:
        raise NotificationLogServiceError(f"{field_name} должен быть положительным числом.")

    return value


def _optional_model_id(model: object | None) -> int | None:
    """Достаёт опциональный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model или `None`.

    Returns:
        Положительный DB id или `None`.
    """
    if model is None:
        return None

    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    return None


__all__ = [
    "NotificationLogRepository",
    "NotificationLogResult",
    "NotificationLogService",
    "NotificationLogServiceError",
    "SqlAlchemyNotificationLogRepository",
]
