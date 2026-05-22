"""Admin-сервис чтения и закрытия API errors."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiError
from app.services.api_error_policies import (
    API_ERROR_STATUS_ADMIN_NOTIFICATION_FAILED,
    API_ERROR_STATUS_ADMIN_NOTIFIED,
    API_ERROR_STATUS_RATE_LIMITED,
    API_ERROR_STATUS_RETRY_NEXT_RUN,
    API_ERROR_STATUS_STALE,
    API_ERROR_STATUS_UNRESOLVED,
)

API_ERROR_STATUS_RESOLVED = "resolved"
"""Статус API error, закрытой администратором."""

_ALLOWED_API_ERROR_STATUSES = frozenset(
    {
        API_ERROR_STATUS_UNRESOLVED,
        API_ERROR_STATUS_ADMIN_NOTIFIED,
        API_ERROR_STATUS_ADMIN_NOTIFICATION_FAILED,
        API_ERROR_STATUS_STALE,
        API_ERROR_STATUS_RATE_LIMITED,
        API_ERROR_STATUS_RETRY_NEXT_RUN,
        API_ERROR_STATUS_RESOLVED,
    }
)
_DEFAULT_API_ERROR_LIMIT = 50
_MAX_API_ERROR_LIMIT = 200


class AdminApiErrorServiceError(RuntimeError):
    """Базовая ошибка admin-сервиса API errors."""


class ApiErrorNotFoundError(AdminApiErrorServiceError):
    """API error не найдена."""


@dataclass(frozen=True, slots=True)
class ApiErrorListQuery:
    """Параметры списка API errors.

    Attributes:
        status_filter: Опциональный фильтр по статусу.
        limit: Максимальное количество строк.
    """

    status_filter: str | None = None
    limit: int = _DEFAULT_API_ERROR_LIMIT


class AdminApiErrorRepository(Protocol):
    """Repository contract для admin API errors."""

    async def list_api_errors(
        self,
        *,
        status_filter: str | None,
        limit: int,
    ) -> tuple[ApiError, ...]:
        """Возвращает список API errors.

        Args:
            status_filter: Фильтр по статусу или `None`.
            limit: Максимальное количество строк.

        Returns:
            Tuple API errors.
        """

    async def get_by_id(self, api_error_id: int) -> ApiError | None:
        """Возвращает API error по ID.

        Args:
            api_error_id: DB ID ошибки.

        Returns:
            Модель ошибки или `None`.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyAdminApiErrorRepository:
    """SQLAlchemy repository для admin API errors."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_api_errors(
        self,
        *,
        status_filter: str | None,
        limit: int,
    ) -> tuple[ApiError, ...]:
        """Возвращает API errors в стабильном порядке."""
        query = select(ApiError)

        if status_filter is not None:
            query = query.where(ApiError.status == status_filter)

        query = query.order_by(ApiError.created_at.desc(), ApiError.id.desc()).limit(limit)
        result = await self._session.execute(query)

        return tuple(result.scalars().all())

    async def get_by_id(self, api_error_id: int) -> ApiError | None:
        """Возвращает API error по ID."""
        result = await self._session.execute(select(ApiError).where(ApiError.id == api_error_id))
        return result.scalar_one_or_none()

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class AdminApiErrorService:
    """Сервис admin-операций над API errors.

    Сервис отвечает только за чтение ошибок для Dev UI и ручное закрытие.
    Политики обработки 403/404/429/5xx остаются в `ApiErrorPolicyService`.
    """

    def __init__(self, *, repository: AdminApiErrorRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository API errors.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "AdminApiErrorService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyAdminApiErrorRepository(session))

    async def list_errors(
        self,
        *,
        status_filter: str | None = None,
        limit: int = _DEFAULT_API_ERROR_LIMIT,
    ) -> tuple[ApiError, ...]:
        """Возвращает список API errors для admin UI.

        Args:
            status_filter: Опциональный status-фильтр.
            limit: Максимальное количество строк.

        Returns:
            Tuple API errors.
        """
        normalized_status = normalize_api_error_status_filter(status_filter)
        normalized_limit = validate_api_error_limit(limit)

        return await self._repository.list_api_errors(
            status_filter=normalized_status,
            limit=normalized_limit,
        )

    async def resolve_error(
        self,
        *,
        api_error_id: int,
        resolved_at: datetime,
    ) -> ApiError:
        """Помечает API error как resolved.

        Args:
            api_error_id: DB ID ошибки.
            resolved_at: Время ручного закрытия.

        Returns:
            Обновлённая API error.

        Raises:
            ApiErrorNotFoundError: Если ошибка не найдена.
            AdminApiErrorServiceError: Если `resolved_at` не timezone-aware.
        """
        normalized_id = validate_api_error_id(api_error_id)
        normalized_resolved_at = validate_resolved_at(resolved_at)

        api_error = await self._repository.get_by_id(normalized_id)
        if api_error is None:
            raise ApiErrorNotFoundError(f"API error {normalized_id} не найдена.")

        api_error.status = API_ERROR_STATUS_RESOLVED
        api_error.resolved_at = normalized_resolved_at

        await self._repository.flush()
        return api_error


def normalize_api_error_status_filter(value: str | None) -> str | None:
    """Нормализует status-фильтр API errors.

    Args:
        value: Статус или `None`.

    Returns:
        Нормализованный статус или `None`.

    Raises:
        AdminApiErrorServiceError: Если статус неизвестен.
    """
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    if normalized not in _ALLOWED_API_ERROR_STATUSES:
        allowed = ", ".join(sorted(_ALLOWED_API_ERROR_STATUSES))
        raise AdminApiErrorServiceError(f"status должен быть одним из: {allowed}.")

    return normalized


def validate_api_error_limit(value: int) -> int:
    """Проверяет limit списка API errors.

    Args:
        value: Limit.

    Returns:
        Проверенный limit.

    Raises:
        AdminApiErrorServiceError: Если limit вне диапазона.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdminApiErrorServiceError("limit должен быть целым числом.")

    if value <= 0 or value > _MAX_API_ERROR_LIMIT:
        raise AdminApiErrorServiceError(f"limit должен быть от 1 до {_MAX_API_ERROR_LIMIT}.")

    return value


def validate_api_error_id(value: int) -> int:
    """Проверяет DB ID API error.

    Args:
        value: DB ID.

    Returns:
        Проверенный ID.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdminApiErrorServiceError("api_error_id должен быть целым числом.")

    if value <= 0:
        raise AdminApiErrorServiceError("api_error_id должен быть положительным числом.")

    return value


def validate_resolved_at(value: datetime) -> datetime:
    """Проверяет timezone-aware resolved_at.

    Args:
        value: Время закрытия ошибки.

    Returns:
        Исходное timezone-aware значение.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise AdminApiErrorServiceError("resolved_at должен быть timezone-aware datetime.")

    return value


def allowed_api_error_statuses() -> Sequence[str]:
    """Возвращает допустимые статусы API errors.

    Returns:
        Отсортированный список статусов.
    """
    return tuple(sorted(_ALLOWED_API_ERROR_STATUSES))


__all__ = [
    "API_ERROR_STATUS_RESOLVED",
    "AdminApiErrorRepository",
    "AdminApiErrorService",
    "AdminApiErrorServiceError",
    "ApiErrorListQuery",
    "ApiErrorNotFoundError",
    "SqlAlchemyAdminApiErrorRepository",
    "allowed_api_error_statuses",
    "normalize_api_error_status_filter",
    "validate_api_error_id",
    "validate_api_error_limit",
    "validate_resolved_at",
]
