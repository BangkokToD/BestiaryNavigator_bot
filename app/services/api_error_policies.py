"""Сервис политик обработки ошибок внешних API."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha1
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiError, NotificationRoute
from app.domain import NotificationType, build_notification_event_key
from app.services.notification_logs import NotificationLogService
from app.services.notification_rendering import (
    ApiErrorAdminPayload,
    ApiErrorNotificationItem,
    NotificationRenderer,
    RenderedNotification,
)
from app.services.notification_sender import TelegramNotificationSender

API_ERROR_STATUS_UNRESOLVED = "unresolved"
API_ERROR_STATUS_ADMIN_NOTIFIED = "admin_notified"
API_ERROR_STATUS_ADMIN_NOTIFICATION_FAILED = "admin_notification_failed"
API_ERROR_STATUS_STALE = "stale"
API_ERROR_STATUS_RATE_LIMITED = "rate_limited"
API_ERROR_STATUS_RETRY_NEXT_RUN = "retry_next_run"

_POLICY_FORBIDDEN = "forbidden"
_POLICY_STALE = "stale"
_POLICY_RATE_LIMITED = "rate_limited"
_POLICY_RETRY_NEXT_RUN = "retry_next_run"
_POLICY_KEEP_UNRESOLVED = "keep_unresolved"

_TIMEOUT_EXCEPTION_CLASSES = frozenset({"ClashTimeoutError"})


class ApiErrorPolicyError(RuntimeError):
    """Базовая ошибка сервиса политик API errors."""


@dataclass(frozen=True, slots=True)
class ApiErrorPolicyResult:
    """Результат одного применения политик API errors."""

    discovered_count: int
    forbidden_count: int
    stale_count: int
    rate_limited_count: int
    retry_next_run_count: int
    direct_admin_sent_count: int
    route_admin_sent_count: int
    notification_failed_count: int


@dataclass(frozen=True, slots=True)
class _ForbiddenNotificationResult:
    """Результат уведомления админа о 403."""

    sent: bool
    direct_admin_sent_count: int
    route_admin_sent_count: int
    failed_count: int


class ApiErrorPolicyRepository(Protocol):
    """Repository contract для политик API errors."""

    async def list_unresolved_api_errors(self) -> tuple[ApiError, ...]:
        """Возвращает unresolved API errors.

        Returns:
            Tuple ошибок, ожидающих обработки.
        """

    async def list_admin_notification_routes(self) -> tuple[NotificationRoute, ...]:
        """Возвращает enabled routes для `api_errors_admin`.

        Returns:
            Tuple enabled routes.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyApiErrorPolicyRepository:
    """SQLAlchemy repository для политик API errors."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_unresolved_api_errors(self) -> tuple[ApiError, ...]:
        """Возвращает unresolved API errors.

        Returns:
            Tuple ошибок в стабильном порядке.
        """
        result = await self._session.execute(
            select(ApiError)
            .where(ApiError.status == API_ERROR_STATUS_UNRESOLVED)
            .order_by(ApiError.created_at, ApiError.id)
        )
        return tuple(result.scalars().all())

    async def list_admin_notification_routes(self) -> tuple[NotificationRoute, ...]:
        """Возвращает enabled routes для API errors admin notifications.

        Returns:
            Tuple enabled routes.
        """
        result = await self._session.execute(
            select(NotificationRoute)
            .where(
                NotificationRoute.notification_type == NotificationType.API_ERRORS_ADMIN.value,
                NotificationRoute.enabled.is_(True),
            )
            .order_by(NotificationRoute.clan_id, NotificationRoute.id)
        )
        return tuple(result.scalars().all())

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class ApiErrorPolicyService:
    """Сервис применения политик обработки API errors.

    Сервис не удаляет исторические данные доменных моделей. Он меняет только
    статус записей `api_errors` и отправляет админское уведомление для 403.
    """

    def __init__(self, *, repository: ApiErrorPolicyRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository API errors.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "ApiErrorPolicyService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyApiErrorPolicyRepository(session))

    async def apply_policies(
        self,
        *,
        observed_at: datetime,
        admin_chat_id: int,
        sender: TelegramNotificationSender,
        notification_log_service: NotificationLogService,
        renderer: NotificationRenderer | None = None,
    ) -> ApiErrorPolicyResult:
        """Применяет политики обработки unresolved API errors.

        Args:
            observed_at: Время текущего запуска worker.
            admin_chat_id: Telegram ID админа для direct notification.
            sender: Sender Telegram-уведомлений.
            notification_log_service: Сервис notification logs.
            renderer: Renderer уведомлений.

        Returns:
            Сводка применения политик.
        """
        _validate_admin_chat_id(admin_chat_id)
        notification_renderer = renderer or NotificationRenderer()
        api_errors = await self._repository.list_unresolved_api_errors()

        forbidden_errors: list[ApiError] = []
        stale_count = 0
        rate_limited_count = 0
        retry_next_run_count = 0

        for api_error in api_errors:
            policy = classify_api_error_policy(api_error)
            if policy == _POLICY_FORBIDDEN:
                forbidden_errors.append(api_error)
                continue

            if policy == _POLICY_STALE:
                api_error.status = API_ERROR_STATUS_STALE
                stale_count += 1
                continue

            if policy == _POLICY_RATE_LIMITED:
                api_error.status = API_ERROR_STATUS_RATE_LIMITED
                rate_limited_count += 1
                continue

            if policy == _POLICY_RETRY_NEXT_RUN:
                api_error.status = API_ERROR_STATUS_RETRY_NEXT_RUN
                retry_next_run_count += 1

        notification_result = _ForbiddenNotificationResult(
            sent=False,
            direct_admin_sent_count=0,
            route_admin_sent_count=0,
            failed_count=0,
        )
        if forbidden_errors:
            notification_result = await self._notify_forbidden_errors(
                api_errors=tuple(forbidden_errors),
                observed_at=observed_at,
                admin_chat_id=admin_chat_id,
                sender=sender,
                notification_log_service=notification_log_service,
                renderer=notification_renderer,
            )
            forbidden_status = (
                API_ERROR_STATUS_ADMIN_NOTIFIED
                if notification_result.sent
                else API_ERROR_STATUS_ADMIN_NOTIFICATION_FAILED
            )
            for api_error in forbidden_errors:
                api_error.status = forbidden_status

        await self._repository.flush()

        return ApiErrorPolicyResult(
            discovered_count=len(api_errors),
            forbidden_count=len(forbidden_errors),
            stale_count=stale_count,
            rate_limited_count=rate_limited_count,
            retry_next_run_count=retry_next_run_count,
            direct_admin_sent_count=notification_result.direct_admin_sent_count,
            route_admin_sent_count=notification_result.route_admin_sent_count,
            notification_failed_count=notification_result.failed_count,
        )

    async def _notify_forbidden_errors(
        self,
        *,
        api_errors: tuple[ApiError, ...],
        observed_at: datetime,
        admin_chat_id: int,
        sender: TelegramNotificationSender,
        notification_log_service: NotificationLogService,
        renderer: NotificationRenderer,
    ) -> _ForbiddenNotificationResult:
        """Уведомляет админа о 403 errors.

        Args:
            api_errors: Ошибки 403.
            observed_at: Время текущего запуска.
            admin_chat_id: Telegram ID админа.
            sender: Sender Telegram-уведомлений.
            notification_log_service: Сервис notification logs.
            renderer: Renderer уведомлений.

        Returns:
            Результат уведомления.
        """
        notification = renderer.render_api_errors_admin(
            ApiErrorAdminPayload(
                errors=[_api_error_to_notification_item(error) for error in api_errors]
            )
        )
        batch_key = _build_api_error_batch_key(api_errors)
        direct_event_key = build_notification_event_key(
            NotificationType.API_ERRORS_ADMIN,
            "403",
            "direct_admin",
            admin_chat_id,
            batch_key,
        )

        direct_sent = await self._try_send_direct_admin_notification(
            event_key=direct_event_key,
            admin_chat_id=admin_chat_id,
            notification=notification,
            sender=sender,
            notification_log_service=notification_log_service,
        )
        if direct_sent:
            return _ForbiddenNotificationResult(
                sent=True,
                direct_admin_sent_count=1,
                route_admin_sent_count=0,
                failed_count=0,
            )

        route_sent_count = 0
        failed_count = 1
        routes = await self._repository.list_admin_notification_routes()

        for route in routes:
            route_event_key = build_notification_event_key(
                NotificationType.API_ERRORS_ADMIN,
                "403",
                "route",
                batch_key,
                _required_model_id(route, model_name="NotificationRoute"),
            )
            if await notification_log_service.has_sent(event_key=route_event_key):
                route_sent_count += 1
                continue

            try:
                send_result = await sender.send(route=route, notification=notification)
            except Exception as exc:
                await notification_log_service.record_failed(
                    route=route,
                    notification_type=NotificationType.API_ERRORS_ADMIN,
                    event_key=route_event_key,
                    payload_summary=notification.payload_summary,
                    error_text=f"{type(exc).__name__}: {exc}",
                )
                failed_count += 1
                continue

            await notification_log_service.record_sent(
                route=route,
                notification_type=NotificationType.API_ERRORS_ADMIN,
                event_key=route_event_key,
                telegram_message_id=send_result.message_id,
                payload_summary=notification.payload_summary,
            )
            route_sent_count += 1

        return _ForbiddenNotificationResult(
            sent=route_sent_count > 0,
            direct_admin_sent_count=0,
            route_admin_sent_count=route_sent_count,
            failed_count=failed_count,
        )

    async def _try_send_direct_admin_notification(
        self,
        *,
        event_key: str,
        admin_chat_id: int,
        notification: RenderedNotification,
        sender: TelegramNotificationSender,
        notification_log_service: NotificationLogService,
    ) -> bool:
        """Пробует отправить direct notification админу.

        Args:
            event_key: Event key direct-уведомления.
            admin_chat_id: Telegram ID админа.
            notification: Отрендеренное уведомление.
            sender: Telegram sender.
            notification_log_service: Сервис notification logs.

        Returns:
            `True`, если direct notification уже была sent или успешно отправлена.
        """
        if await notification_log_service.has_sent(event_key=event_key):
            return True

        try:
            send_result = await sender.send_to_chat(
                chat_id=admin_chat_id,
                notification=notification,
            )
        except Exception as exc:
            await notification_log_service.record_failed(
                notification_type=NotificationType.API_ERRORS_ADMIN,
                event_key=event_key,
                payload_summary=notification.payload_summary,
                error_text=f"{type(exc).__name__}: {exc}",
                chat_id=admin_chat_id,
                message_thread_id=None,
            )
            return False

        await notification_log_service.record_sent(
            notification_type=NotificationType.API_ERRORS_ADMIN,
            event_key=event_key,
            telegram_message_id=send_result.message_id,
            payload_summary=notification.payload_summary,
            chat_id=admin_chat_id,
            message_thread_id=None,
        )
        return True


def classify_api_error_policy(api_error: ApiError) -> str:
    """Классифицирует API error по policy-правилу.

    Args:
        api_error: Модель ошибки API.

    Returns:
        Внутренний policy code.
    """
    if api_error.status_code == 403:
        return _POLICY_FORBIDDEN

    if api_error.status_code == 404:
        return _POLICY_STALE

    if api_error.status_code == 429:
        return _POLICY_RATE_LIMITED

    if api_error.status_code is not None and api_error.status_code >= 500:
        return _POLICY_RETRY_NEXT_RUN

    if _is_timeout_or_network_error(api_error):
        return _POLICY_RETRY_NEXT_RUN

    return _POLICY_KEEP_UNRESOLVED


def _is_timeout_or_network_error(api_error: ApiError) -> bool:
    """Проверяет timeout/network ошибку.

    Args:
        api_error: Модель API error.

    Returns:
        `True`, если ошибку нужно повторить на следующем worker-run.
    """
    exception_class = (api_error.exception_class or "").strip()
    if exception_class in _TIMEOUT_EXCEPTION_CLASSES:
        return True

    return api_error.status_code is None


def _api_error_to_notification_item(api_error: ApiError) -> ApiErrorNotificationItem:
    """Преобразует ApiError в item админского уведомления.

    Args:
        api_error: Модель API error.

    Returns:
        Item для renderer-а.
    """
    return ApiErrorNotificationItem(
        endpoint=api_error.endpoint,
        status_code=api_error.status_code,
        message=api_error.message,
    )


def _build_api_error_batch_key(api_errors: tuple[ApiError, ...]) -> str:
    """Строит короткий stable key группы API errors.

    Args:
        api_errors: Ошибки одной группы уведомления.

    Returns:
        SHA1 key группы.
    """
    parts: list[str] = []
    for api_error in api_errors:
        api_error_id = getattr(api_error, "id", None)
        if isinstance(api_error_id, int) and api_error_id > 0:
            parts.append(f"id:{api_error_id}")
            continue

        parts.append(
            "|".join(
                (
                    api_error.endpoint,
                    str(api_error.status_code),
                    api_error.exception_class or "",
                    api_error.message,
                )
            )
        )

    return sha1("\n".join(sorted(parts)).encode("utf-8")).hexdigest()


def _validate_admin_chat_id(value: int) -> int:
    """Проверяет Telegram admin chat id.

    Args:
        value: Telegram ID админа.

    Returns:
        Проверенный ID.

    Raises:
        ApiErrorPolicyError: Если ID некорректен.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiErrorPolicyError("admin_chat_id должен быть целым числом.")

    if value <= 0:
        raise ApiErrorPolicyError("admin_chat_id должен быть положительным числом.")

    return value


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise ApiErrorPolicyError(f"{model_name} должен быть сохранён в БД.")


__all__ = [
    "API_ERROR_STATUS_ADMIN_NOTIFICATION_FAILED",
    "API_ERROR_STATUS_ADMIN_NOTIFIED",
    "API_ERROR_STATUS_RATE_LIMITED",
    "API_ERROR_STATUS_RETRY_NEXT_RUN",
    "API_ERROR_STATUS_STALE",
    "API_ERROR_STATUS_UNRESOLVED",
    "ApiErrorPolicyError",
    "ApiErrorPolicyRepository",
    "ApiErrorPolicyResult",
    "ApiErrorPolicyService",
    "SqlAlchemyApiErrorPolicyRepository",
    "classify_api_error_policy",
]
