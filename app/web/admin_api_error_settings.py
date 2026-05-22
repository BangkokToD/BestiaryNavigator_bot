"""SSR-страница Dev API errors."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiError
from app.db.session import get_db_session
from app.services.api_error_policies import (
    API_ERROR_STATUS_ADMIN_NOTIFICATION_FAILED,
    API_ERROR_STATUS_ADMIN_NOTIFIED,
    API_ERROR_STATUS_RATE_LIMITED,
    API_ERROR_STATUS_RETRY_NEXT_RUN,
    API_ERROR_STATUS_STALE,
    API_ERROR_STATUS_UNRESOLVED,
)
from app.services.api_errors import (
    API_ERROR_STATUS_RESOLVED,
    AdminApiErrorService,
    allowed_api_error_statuses,
)
from app.web.admin_api_errors import ApiErrorListMode, ApiErrorResponse
from app.web.context import WebRequestContext, build_template_context, require_admin_request
from app.web.templates import templates

router = APIRouter(prefix="/admin/settings/api-errors", tags=["admin-api-error-settings"])

_STATUS_LABELS = {
    API_ERROR_STATUS_UNRESOLVED: "Unresolved",
    API_ERROR_STATUS_ADMIN_NOTIFIED: "Admin notified",
    API_ERROR_STATUS_ADMIN_NOTIFICATION_FAILED: "Notification failed",
    API_ERROR_STATUS_STALE: "Stale",
    API_ERROR_STATUS_RATE_LIMITED: "Rate limited",
    API_ERROR_STATUS_RETRY_NEXT_RUN: "Retrying",
    API_ERROR_STATUS_RESOLVED: "Resolved",
}
_STATUS_GROUPS = {
    API_ERROR_STATUS_UNRESOLVED: "unresolved",
    API_ERROR_STATUS_ADMIN_NOTIFIED: "unresolved",
    API_ERROR_STATUS_ADMIN_NOTIFICATION_FAILED: "unresolved",
    API_ERROR_STATUS_STALE: "stale",
    API_ERROR_STATUS_RATE_LIMITED: "retrying",
    API_ERROR_STATUS_RETRY_NEXT_RUN: "retrying",
    API_ERROR_STATUS_RESOLVED: "resolved",
}
_STATUS_BADGE_VARIANTS = {
    "unresolved": "info",
    "retrying": "info",
    "stale": "muted",
    "resolved": "muted",
}


class ApiErrorSettingsService(Protocol):
    """Минимальный contract сервиса API errors для SSR-страницы."""

    async def list_errors(
        self,
        *,
        status_filter: str | None = None,
        limit: int = 50,
    ) -> tuple[ApiError, ...]:
        """Возвращает список API errors."""


@dataclass(frozen=True, slots=True)
class ApiErrorStatusOption:
    """Опция status filter.

    Attributes:
        value: Значение статуса.
        label: Подпись статуса.
    """

    value: str
    label: str


@dataclass(frozen=True, slots=True)
class ApiErrorCardView:
    """View model карточки API error.

    Attributes:
        id: DB ID ошибки.
        endpoint: Endpoint запроса.
        method: HTTP method.
        entity_type: Тип сущности.
        entity_tag: Тег сущности.
        status_code: HTTP status code.
        message: Краткое сообщение.
        worker_name: Worker/job, где ошибка обнаружена.
        retry_count: Количество retry.
        status: Технический статус.
        status_label: Подпись статуса.
        status_group: Группа статуса для UI.
        status_variant: Вариант badge.
        is_stale: Признак stale state.
        created_at_text: Время создания.
        resolved_at_text: Время закрытия.
        response_snippet: Debug response snippet.
        exception_class: Debug exception class.
        resolve_url: Backend endpoint закрытия ошибки.
        can_resolve: Можно ли показать кнопку закрытия.
    """

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
    status_label: str
    status_group: str
    status_variant: str
    is_stale: bool
    created_at_text: str
    resolved_at_text: str | None
    response_snippet: str | None
    exception_class: str | None
    resolve_url: str | None
    can_resolve: bool

    @classmethod
    def from_response(cls, response: ApiErrorResponse) -> "ApiErrorCardView":
        """Создаёт карточку из sanitized API response.

        Args:
            response: Sanitized response schema из JSON route layer.

        Returns:
            View model карточки.
        """
        status_group = _status_group(response.status)
        return cls(
            id=response.id,
            endpoint=response.endpoint,
            method=response.method,
            entity_type=response.entity_type,
            entity_tag=response.entity_tag,
            status_code=response.status_code,
            message=response.message,
            worker_name=response.worker_name,
            retry_count=response.retry_count,
            status=response.status,
            status_label=_status_label(response.status),
            status_group=status_group,
            status_variant=_STATUS_BADGE_VARIANTS.get(status_group, "muted"),
            is_stale=response.is_stale,
            created_at_text=_format_datetime(response.created_at),
            resolved_at_text=_format_optional_datetime(response.resolved_at),
            response_snippet=response.response_snippet,
            exception_class=response.exception_class,
            resolve_url=f"/admin/api-errors/{response.id}/resolve" if response.id else None,
            can_resolve=response.id is not None and response.status != API_ERROR_STATUS_RESOLVED,
        )


async def get_api_error_settings_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AsyncIterator[ApiErrorSettingsService]:
    """Создаёт сервис Dev API errors страницы.

    Args:
        session: Async SQLAlchemy session.

    Yields:
        Admin API errors service.
    """
    service = AdminApiErrorService.from_session(session=session)

    try:
        yield service
        await session.commit()
    except Exception:
        await session.rollback()
        raise


@router.get("", response_class=HTMLResponse)
async def api_error_settings_page(
    request: Request,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    api_error_service: Annotated[
        ApiErrorSettingsService,
        Depends(get_api_error_settings_service),
    ],
    status_filter: Annotated[str | None, Query(alias="status", max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Response:
    """Отдаёт admin-only страницу Dev API errors.

    Args:
        request: FastAPI request.
        _context: Admin web context.
        api_error_service: Сервис API errors.
        status_filter: Фильтр по статусу.
        limit: Максимальное количество ошибок.

    Returns:
        HTML-страница Dev API errors.
    """
    errors = await api_error_service.list_errors(
        status_filter=status_filter,
        limit=limit,
    )
    cards = [
        ApiErrorCardView.from_response(
            ApiErrorResponse.from_model(error, mode=ApiErrorListMode.DEBUG)
        )
        for error in errors
    ]

    return templates.TemplateResponse(
        request,
        "admin/api_errors/settings.html",
        build_template_context(
            request,
            page_title="Dev API errors",
            active_nav="admin_api_errors",
            errors=cards,
            status_options=_build_status_options(),
            status_filter=status_filter or "",
            limit=limit,
            total_count=len(cards),
        ),
    )


def _build_status_options() -> list[ApiErrorStatusOption]:
    """Создаёт опции status filter.

    Returns:
        Список статусов API errors.
    """
    return [
        ApiErrorStatusOption(value=status, label=_status_label(status))
        for status in allowed_api_error_statuses()
    ]


def _status_label(status: str) -> str:
    """Возвращает подпись статуса.

    Args:
        status: Технический статус.

    Returns:
        Подпись для UI.
    """
    return _STATUS_LABELS.get(status, status)


def _status_group(status: str) -> str:
    """Возвращает группу статуса для UI.

    Args:
        status: Технический статус.

    Returns:
        Группа `unresolved`, `resolved`, `retrying` или `stale`.
    """
    return _STATUS_GROUPS.get(status, "unresolved")


def _format_datetime(value: datetime) -> str:
    """Форматирует datetime для карточки.

    Args:
        value: Datetime.

    Returns:
        Текст datetime.
    """
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _format_optional_datetime(value: datetime | None) -> str | None:
    """Форматирует опциональный datetime.

    Args:
        value: Datetime или `None`.

    Returns:
        Текст datetime или `None`.
    """
    if value is None:
        return None

    return _format_datetime(value)


__all__ = [
    "ApiErrorCardView",
    "ApiErrorSettingsService",
    "ApiErrorStatusOption",
    "get_api_error_settings_service",
    "router",
]
