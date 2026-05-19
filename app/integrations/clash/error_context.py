"""Mapper контекста ошибок Clash API для будущей записи в api_errors."""

from dataclasses import dataclass

from app.integrations.clash.exceptions import ClashApiError

_MAX_ENDPOINT_LENGTH = 512
_MAX_METHOD_LENGTH = 16
_MAX_ENTITY_TYPE_LENGTH = 64
_MAX_ENTITY_TAG_LENGTH = 128
_MAX_MESSAGE_LENGTH = 1024
_MAX_RESPONSE_SNIPPET_LENGTH = 2048
_MAX_EXCEPTION_CLASS_LENGTH = 255
_MAX_WORKER_NAME_LENGTH = 128
_MAX_STATUS_LENGTH = 32
_DEFAULT_API_ERROR_STATUS = "unresolved"


@dataclass(frozen=True, slots=True)
class ClashApiErrorContext:
    """Краткий контекст ошибки Clash API.

    Структура соответствует полям `ApiError`, но не зависит от DB-модели.
    Это позволяет worker/backend-слоям сохранять ошибку позже, не связывая
    integration layer с persistence layer.
    """

    endpoint: str
    method: str
    entity_type: str | None
    entity_tag: str | None
    status_code: int | None
    message: str
    response_snippet: str | None
    exception_class: str
    worker_name: str | None
    retry_count: int
    status: str = _DEFAULT_API_ERROR_STATUS

    def to_api_error_values(self) -> dict[str, object]:
        """Возвращает значения, пригодные для создания `ApiError`.

        Returns:
            Словарь с полями, совместимыми с текущей DB-моделью `ApiError`.
        """
        return {
            "endpoint": self.endpoint,
            "method": self.method,
            "entity_type": self.entity_type,
            "entity_tag": self.entity_tag,
            "status_code": self.status_code,
            "message": self.message,
            "response_snippet": self.response_snippet,
            "exception_class": self.exception_class,
            "worker_name": self.worker_name,
            "retry_count": self.retry_count,
            "status": self.status,
        }


def map_clash_api_error_to_context(
    error: ClashApiError,
    *,
    entity_type: str | None = None,
    entity_tag: str | None = None,
    worker_name: str | None = None,
    retry_count: int = 0,
    status: str = _DEFAULT_API_ERROR_STATUS,
) -> ClashApiErrorContext:
    """Преобразует typed Clash API exception в краткий debug-контекст.

    Mapper не импортирует worker и DB-слои. Worker name, retry count и entity
    context передаются извне тем слоем, который знает текущую job/сущность.

    Args:
        error: Typed exception Clash API.
        entity_type: Тип сущности, например `clan`, `player`, `war`.
        entity_tag: Тег сущности, если он известен.
        worker_name: Имя worker/job, если ошибка возникла в worker.
        retry_count: Количество уже выполненных повторов.
        status: Статус будущей записи `api_errors`.

    Returns:
        Нормализованный контекст ошибки.

    Raises:
        ValueError: Если `retry_count` отрицательный или status пустой.
    """
    return ClashApiErrorContext(
        endpoint=_required_string(error.endpoint, max_length=_MAX_ENDPOINT_LENGTH),
        method=_required_string(error.method.upper(), max_length=_MAX_METHOD_LENGTH),
        entity_type=_optional_string(entity_type, max_length=_MAX_ENTITY_TYPE_LENGTH),
        entity_tag=_optional_string(entity_tag, max_length=_MAX_ENTITY_TAG_LENGTH),
        status_code=error.status_code,
        message=_required_string(str(error), max_length=_MAX_MESSAGE_LENGTH),
        response_snippet=_optional_string(
            error.response_snippet,
            max_length=_MAX_RESPONSE_SNIPPET_LENGTH,
        ),
        exception_class=_required_string(
            type(error).__name__,
            max_length=_MAX_EXCEPTION_CLASS_LENGTH,
        ),
        worker_name=_optional_string(worker_name, max_length=_MAX_WORKER_NAME_LENGTH),
        retry_count=_validate_retry_count(retry_count),
        status=_required_string(status, max_length=_MAX_STATUS_LENGTH),
    )


def _required_string(value: str, *, max_length: int) -> str:
    """Нормализует обязательную строку."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("Обязательное строковое поле не может быть пустым.")

    return normalized[:max_length]


def _optional_string(value: str | None, *, max_length: int) -> str | None:
    """Нормализует опциональную строку."""
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    return normalized[:max_length]


def _validate_retry_count(value: int) -> int:
    """Проверяет количество retry.

    Args:
        value: Количество повторных попыток.

    Returns:
        Проверенное количество retry.

    Raises:
        ValueError: Если значение не является неотрицательным int.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("retry_count должен быть целым числом.")

    if value < 0:
        raise ValueError("retry_count не может быть отрицательным.")

    return value


__all__ = [
    "ClashApiErrorContext",
    "map_clash_api_error_to_context",
]
