"""Worker job применения политик API errors."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings, get_settings
from app.services import ApiErrorPolicyResult, ApiErrorPolicyService, NotificationLogService
from app.services.notification_sender import (
    AiogramTelegramNotificationSender,
    TelegramNotificationSender,
)
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

HANDLE_API_ERROR_POLICIES_JOB_NAME = "handle_api_error_policies"


class ApiErrorPolicyProcessor(Protocol):
    """Contract processor-а API error policies."""

    async def apply_policies(
        self,
        *,
        observed_at: datetime,
        admin_chat_id: int,
        sender: TelegramNotificationSender,
        notification_log_service: NotificationLogService,
    ) -> ApiErrorPolicyResult:
        """Применяет политики API errors.

        Args:
            observed_at: Время текущего запуска.
            admin_chat_id: Telegram ID администратора.
            sender: Sender Telegram-уведомлений.
            notification_log_service: Сервис notification logs.

        Returns:
            Результат применения политик.
        """


@dataclass(frozen=True, slots=True)
class HandleApiErrorPoliciesJobResult:
    """Результат одного запуска handle api error policies job."""

    processed_count: int
    direct_admin_sent_count: int
    route_admin_sent_count: int
    notification_failed_count: int


class HandleApiErrorPoliciesJob:
    """Job применения политик обработки API errors."""

    def __init__(
        self,
        *,
        api_error_policy_processor: ApiErrorPolicyProcessor,
        notification_log_service: NotificationLogService,
        notification_sender: TelegramNotificationSender,
        admin_chat_id: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Инициализирует job.

        Args:
            api_error_policy_processor: Processor политик API errors.
            notification_log_service: Сервис notification logs.
            notification_sender: Sender Telegram-уведомлений.
            admin_chat_id: Telegram ID администратора.
            clock: Источник текущего времени для тестов.
        """
        self._api_error_policy_processor = api_error_policy_processor
        self._notification_log_service = notification_log_service
        self._notification_sender = notification_sender
        self._admin_chat_id = admin_chat_id
        self._clock = clock or _utc_now

    @classmethod
    def from_session(
        cls,
        *,
        session: AsyncSession,
        notification_sender: TelegramNotificationSender,
        settings: Settings,
    ) -> "HandleApiErrorPoliciesJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            notification_sender: Sender Telegram-уведомлений.
            settings: Runtime-настройки приложения.

        Returns:
            Настроенная job.
        """
        return cls(
            api_error_policy_processor=ApiErrorPolicyService.from_session(session=session),
            notification_log_service=NotificationLogService.from_session(session=session),
            notification_sender=notification_sender,
            admin_chat_id=settings.telegram_admin_id,
        )

    async def run(self, context: WorkerJobContext) -> HandleApiErrorPoliciesJobResult:
        """Применяет API error policies.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        result = await self._api_error_policy_processor.apply_policies(
            observed_at=self._clock(),
            admin_chat_id=self._admin_chat_id,
            sender=self._notification_sender,
            notification_log_service=self._notification_log_service,
        )

        return HandleApiErrorPoliciesJobResult(
            processed_count=result.discovered_count,
            direct_admin_sent_count=result.direct_admin_sent_count,
            route_admin_sent_count=result.route_admin_sent_count,
            notification_failed_count=result.notification_failed_count,
        )


def register_handle_api_error_policies_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует handle api error policies job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=HANDLE_API_ERROR_POLICIES_JOB_NAME)
    async def handle_api_error_policies(context: WorkerJobContext) -> None:
        """Запускает применение API error policies внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("handle_api_error_policies требует DB session.")

        settings = get_settings()
        async with AiogramTelegramNotificationSender.from_settings(settings=settings) as sender:
            job = HandleApiErrorPoliciesJob.from_session(
                session=context.session,
                notification_sender=sender,
                settings=settings,
            )
            await job.run(context)


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "HANDLE_API_ERROR_POLICIES_JOB_NAME",
    "ApiErrorPolicyProcessor",
    "HandleApiErrorPoliciesJob",
    "HandleApiErrorPoliciesJobResult",
    "register_handle_api_error_policies_job",
]
