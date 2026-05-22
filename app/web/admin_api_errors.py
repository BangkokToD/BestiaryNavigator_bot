"""Admin JSON routes для Dev API errors."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, NoReturn, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiError
from app.db.session import get_db_session
from app.services.api_error_policies import API_ERROR_STATUS_STALE
from app.services.api_errors import (
    AdminApiErrorService,
    AdminApiErrorServiceError,
    ApiErrorNotFoundError,
)
from app.web.context import WebRequestContext, require_admin_request

router = APIRouter(prefix="/admin/api-errors", tags=["admin-api-errors"])

_SENSITIVE_DEBUG_MARKERS = frozenset(
    {
        "authorization",
        "bearer",
        "token",
        "api_key",
        "password",
        "secret",
    }
)


class ApiErrorListMode(StrEnum):
    """Режим списка API errors."""

    SUMMARY = "summary"
    DEBUG = "debug"


class AdminApiErrorServiceContract(Protocol):
    """Минимальный contract admin-сервиса API errors."""

    async def list_errors(
        self,
        *,
        status_filter: str | None = None,
        limit: int = 50,
    ) -> tuple[ApiError, ...]:
        """Возвращает список API errors."""

    async def resolve_error(
        self,
        *,
        api_error_id: int,
        resolved_at: datetime,
    ) -> ApiError:
        """Помечает API error как resolved."""


class ApiErrorResponse(BaseModel):
    """JSON-представление API error для admin UI."""

    id: int | None
    endpoint: str
    method: str
    entity_type: str | None
    entity_tag: str | None
    status_code: int | None
    message: str
    worker_name: str | None
    retry_count: int
    status: str
    is_stale: bool
    created_at: datetime
    resolved_at: datetime | None
    response_snippet: str | None = None
    exception_class: str | None = None

    @classmethod
    def from_model(
        cls,
        api_error: ApiError,
        *,
        mode: ApiErrorListMode,
    ) -> "ApiErrorResponse":
        """Создаёт response schema из модели `ApiError`.

        Args:
            api_error: Модель API error.
            mode: Режим summary/debug.

        Returns:
            JSON schema API error.
        """
        is_debug = mode == ApiErrorListMode.DEBUG

        return cls(
            id=_optional_model_id(api_error),
            endpoint=_sanitize_required(api_error.endpoint),
            method=_sanitize_required(api_error.method),
            entity_type=_sanitize_optional(api_error.entity_type),
            entity_tag=_sanitize_optional(api_error.entity_tag),
            status_code=api_error.status_code,
            message=_sanitize_required(api_error.message),
            worker_name=_sanitize_optional(api_error.worker_name),
            retry_count=api_error.retry_count,
            status=api_error.status,
            is_stale=api_error.status == API_ERROR_STATUS_STALE,
            created_at=api_error.created_at,
            resolved_at=api_error.resolved_at,
            response_snippet=_sanitize_optional(api_error.response_snippet) if is_debug else None,
            exception_class=_sanitize_optional(api_error.exception_class) if is_debug else None,
        )


class ApiErrorListResponse(BaseModel):
    """Response списка API errors."""

    ok: bool = True
    mode: ApiErrorListMode
    count: int
    limit: int
    status_filter: str | None
    errors: list[ApiErrorResponse]


class ApiErrorActionResponse(BaseModel):
    """Response действия над API error."""

    ok: bool = True
    api_error: ApiErrorResponse


async def get_admin_api_error_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AsyncIterator[AdminApiErrorServiceContract]:
    """Создаёт admin API error service с транзакционным commit/rollback.

    Args:
        session: Async SQLAlchemy session.

    Yields:
        Admin service API errors.
    """
    service = AdminApiErrorService.from_session(session=session)

    try:
        yield service
        await session.commit()
    except Exception:
        await session.rollback()
        raise


@router.get("", response_model=ApiErrorListResponse, response_model_exclude_none=True)
async def list_admin_api_errors(
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    api_error_service: Annotated[
        AdminApiErrorServiceContract,
        Depends(get_admin_api_error_service),
    ],
    mode: Annotated[ApiErrorListMode, Query()] = ApiErrorListMode.SUMMARY,
    status_filter: Annotated[str | None, Query(alias="status", max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ApiErrorListResponse:
    """Возвращает список API errors для Dev UI.

    Args:
        _context: Admin web context.
        api_error_service: Admin service API errors.
        mode: Режим summary/debug.
        status_filter: Фильтр по статусу.
        limit: Максимальное количество ошибок.

    Returns:
        JSON response списка API errors.
    """
    try:
        errors = await api_error_service.list_errors(
            status_filter=status_filter,
            limit=limit,
        )
    except Exception as exc:
        _raise_admin_api_error(exc)

    return ApiErrorListResponse(
        mode=mode,
        count=len(errors),
        limit=limit,
        status_filter=status_filter,
        errors=[ApiErrorResponse.from_model(error, mode=mode) for error in errors],
    )


@router.post(
    "/{api_error_id}/resolve",
    response_model=ApiErrorActionResponse,
    response_model_exclude_none=True,
)
async def resolve_admin_api_error(
    api_error_id: int,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    api_error_service: Annotated[
        AdminApiErrorServiceContract,
        Depends(get_admin_api_error_service),
    ],
) -> ApiErrorActionResponse:
    """Помечает API error как resolved.

    Args:
        api_error_id: DB ID ошибки.
        _context: Admin web context.
        api_error_service: Admin service API errors.

    Returns:
        JSON response с закрытой ошибкой.
    """
    try:
        api_error = await api_error_service.resolve_error(
            api_error_id=api_error_id,
            resolved_at=datetime.now(UTC),
        )
    except Exception as exc:
        _raise_admin_api_error(exc)

    return ApiErrorActionResponse(
        api_error=ApiErrorResponse.from_model(
            api_error,
            mode=ApiErrorListMode.DEBUG,
        )
    )


def _raise_admin_api_error(error: Exception) -> NoReturn:
    """Преобразует service errors в HTTPException.

    Args:
        error: Исключение нижнего слоя.

    Raises:
        HTTPException: Понятная HTTP-ошибка admin route.
    """
    if isinstance(error, ApiErrorNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "api_error_not_found",
                "message": str(error),
            },
        ) from error

    if isinstance(error, AdminApiErrorServiceError | ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_api_error_payload",
                "message": str(error),
            },
        ) from error

    raise error


def _sanitize_required(value: str) -> str:
    """Очищает обязательную строку от чувствительных данных.

    Args:
        value: Исходная строка.

    Returns:
        Безопасная строка.
    """
    normalized = value.strip()
    if _contains_sensitive_marker(normalized):
        return "[redacted]"

    return normalized


def _sanitize_optional(value: str | None) -> str | None:
    """Очищает опциональную строку от чувствительных данных.

    Args:
        value: Исходная строка или `None`.

    Returns:
        Безопасная строка или `None`.
    """
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    if _contains_sensitive_marker(normalized):
        return "[redacted]"

    return normalized


def _contains_sensitive_marker(value: str) -> bool:
    """Проверяет наличие sensitive-маркеров.

    Args:
        value: Проверяемая строка.

    Returns:
        `True`, если строка похожа на секрет.
    """
    lowered = value.lower()
    return any(marker in lowered for marker in _SENSITIVE_DEBUG_MARKERS)


def _optional_model_id(model: object) -> int | None:
    """Возвращает DB id модели, если он уже назначен.

    Args:
        model: SQLAlchemy model.

    Returns:
        Положительный id или `None`.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    return None


__all__ = [
    "AdminApiErrorServiceContract",
    "ApiErrorActionResponse",
    "ApiErrorListMode",
    "ApiErrorListResponse",
    "ApiErrorResponse",
    "get_admin_api_error_service",
    "router",
]
