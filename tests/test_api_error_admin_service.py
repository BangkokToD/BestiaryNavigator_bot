"""Тесты admin-сервиса API errors."""

from datetime import UTC, datetime

import pytest

from app.db.models import ApiError
from app.services.api_error_policies import (
    API_ERROR_STATUS_RATE_LIMITED,
    API_ERROR_STATUS_STALE,
    API_ERROR_STATUS_UNRESOLVED,
)
from app.services.api_errors import (
    API_ERROR_STATUS_RESOLVED,
    AdminApiErrorService,
    AdminApiErrorServiceError,
    ApiErrorNotFoundError,
)


class InMemoryAdminApiErrorRepository:
    """In-memory repository для admin API errors."""

    def __init__(self, errors: list[ApiError] | None = None) -> None:
        """Инициализирует repository.

        Args:
            errors: Начальный список API errors.
        """
        self.errors = errors or []
        self.flush_count = 0
        self.list_calls: list[tuple[str | None, int]] = []

    async def list_api_errors(
        self,
        *,
        status_filter: str | None,
        limit: int,
    ) -> tuple[ApiError, ...]:
        """Возвращает API errors из памяти."""
        self.list_calls.append((status_filter, limit))
        errors = [
            api_error
            for api_error in self.errors
            if status_filter is None or api_error.status == status_filter
        ]
        errors.sort(
            key=lambda api_error: (
                api_error.created_at,
                api_error.id or 0,
            ),
            reverse=True,
        )

        return tuple(errors[:limit])

    async def get_by_id(self, api_error_id: int) -> ApiError | None:
        """Возвращает API error по ID."""
        for api_error in self.errors:
            if api_error.id == api_error_id:
                return api_error

        return None

    async def flush(self) -> None:
        """Фиксирует flush."""
        self.flush_count += 1


@pytest.mark.asyncio
async def test_admin_api_error_service_lists_errors_with_status_and_limit() -> None:
    """Проверяет список API errors с фильтром и limit."""
    stale_error = _make_api_error(
        api_error_id=1,
        status=API_ERROR_STATUS_STALE,
        created_at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
    )
    unresolved_error = _make_api_error(
        api_error_id=2,
        status=API_ERROR_STATUS_UNRESOLVED,
        created_at=datetime(2026, 5, 22, 13, 0, tzinfo=UTC),
    )
    repository = InMemoryAdminApiErrorRepository([stale_error, unresolved_error])
    service = AdminApiErrorService(repository=repository)

    errors = await service.list_errors(
        status_filter=API_ERROR_STATUS_STALE,
        limit=50,
    )

    assert errors == (stale_error,)
    assert repository.list_calls == [(API_ERROR_STATUS_STALE, 50)]
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_admin_api_error_service_resolves_error() -> None:
    """Проверяет ручное закрытие API error."""
    api_error = _make_api_error(
        api_error_id=1,
        status=API_ERROR_STATUS_UNRESOLVED,
    )
    repository = InMemoryAdminApiErrorRepository([api_error])
    service = AdminApiErrorService(repository=repository)
    resolved_at = datetime(2026, 5, 22, 14, 0, tzinfo=UTC)

    resolved_error = await service.resolve_error(
        api_error_id=1,
        resolved_at=resolved_at,
    )

    assert resolved_error is api_error
    assert api_error.status == API_ERROR_STATUS_RESOLVED
    assert api_error.resolved_at == resolved_at
    assert repository.flush_count == 1


@pytest.mark.asyncio
async def test_admin_api_error_service_raises_for_missing_error() -> None:
    """Проверяет ошибку при закрытии отсутствующей API error."""
    repository = InMemoryAdminApiErrorRepository()
    service = AdminApiErrorService(repository=repository)

    with pytest.raises(ApiErrorNotFoundError):
        await service.resolve_error(
            api_error_id=404,
            resolved_at=datetime(2026, 5, 22, 14, 0, tzinfo=UTC),
        )

    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_admin_api_error_service_rejects_unknown_status_filter() -> None:
    """Проверяет валидацию status filter."""
    repository = InMemoryAdminApiErrorRepository()
    service = AdminApiErrorService(repository=repository)

    with pytest.raises(AdminApiErrorServiceError):
        await service.list_errors(status_filter="unknown", limit=50)

    assert repository.list_calls == []


@pytest.mark.asyncio
async def test_admin_api_error_service_rejects_invalid_limit() -> None:
    """Проверяет валидацию limit."""
    repository = InMemoryAdminApiErrorRepository()
    service = AdminApiErrorService(repository=repository)

    with pytest.raises(AdminApiErrorServiceError):
        await service.list_errors(status_filter=None, limit=201)

    assert repository.list_calls == []


@pytest.mark.asyncio
async def test_admin_api_error_service_rejects_naive_resolved_at() -> None:
    """Проверяет запрет naive resolved_at."""
    api_error = _make_api_error(api_error_id=1, status=API_ERROR_STATUS_UNRESOLVED)
    repository = InMemoryAdminApiErrorRepository([api_error])
    service = AdminApiErrorService(repository=repository)

    with pytest.raises(AdminApiErrorServiceError):
        await service.resolve_error(
            api_error_id=1,
            resolved_at=datetime(2026, 5, 22, 14, 0),
        )

    assert repository.flush_count == 0


def _make_api_error(
    *,
    api_error_id: int,
    status: str = API_ERROR_STATUS_UNRESOLVED,
    status_code: int | None = 500,
    created_at: datetime | None = None,
) -> ApiError:
    """Создаёт ApiError для unit-тестов.

    Args:
        api_error_id: DB ID ошибки.
        status: Статус ошибки.
        status_code: HTTP status code.
        created_at: Время создания.

    Returns:
        Модель ApiError.
    """
    return ApiError(
        id=api_error_id,
        endpoint=f"clans/%23MAIN/error-{api_error_id}",
        method="GET",
        entity_type="clan",
        entity_tag="#MAIN",
        status_code=status_code,
        message=f"API error {api_error_id}",
        response_snippet='{"reason":"error"}',
        exception_class="ClashApiError",
        worker_name="sync_clans",
        retry_count=1,
        status=status or API_ERROR_STATUS_RATE_LIMITED,
        created_at=created_at or datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
    )
