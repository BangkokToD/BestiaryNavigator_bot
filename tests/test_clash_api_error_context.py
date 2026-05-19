"""Тесты mapper контекста ошибок Clash API."""

from pathlib import Path

import pytest

import app.integrations.clash.error_context as error_context_module
from app.integrations.clash import (
    ClashApiError,
    ClashApiErrorContext,
    ClashForbiddenError,
    ClashNotFoundError,
    ClashRateLimitError,
    ClashTimeoutError,
    map_clash_api_error_to_context,
)


@pytest.mark.parametrize(
    ("error_class", "status_code"),
    [
        (ClashForbiddenError, 403),
        (ClashNotFoundError, 404),
        (ClashRateLimitError, 429),
    ],
)
def test_map_clash_api_error_to_context_maps_http_errors(
    error_class: type[ClashApiError],
    status_code: int,
) -> None:
    """Проверяет mapper для 403, 404 и 429."""
    long_snippet = "x" * 3000
    error = error_class(
        f"Clash API returned HTTP {status_code} for GET clans/%232ABC.",
        endpoint="clans/%232ABC",
        method="GET",
        status_code=status_code,
        response_snippet=long_snippet,
    )

    context = map_clash_api_error_to_context(
        error,
        entity_type=" clan ",
        entity_tag=" #2ABC ",
        worker_name=" sync_clans ",
        retry_count=2,
    )

    assert isinstance(context, ClashApiErrorContext)
    assert context.endpoint == "clans/%232ABC"
    assert context.method == "GET"
    assert context.entity_type == "clan"
    assert context.entity_tag == "#2ABC"
    assert context.status_code == status_code
    assert context.exception_class == error_class.__name__
    assert context.worker_name == "sync_clans"
    assert context.retry_count == 2
    assert context.status == "unresolved"
    assert context.response_snippet == long_snippet[:2048]

    values = context.to_api_error_values()
    assert values["endpoint"] == "clans/%232ABC"
    assert values["method"] == "GET"
    assert values["entity_type"] == "clan"
    assert values["entity_tag"] == "#2ABC"
    assert values["status_code"] == status_code
    assert values["exception_class"] == error_class.__name__
    assert values["worker_name"] == "sync_clans"
    assert values["retry_count"] == 2
    assert values["status"] == "unresolved"


def test_map_clash_api_error_to_context_maps_timeout() -> None:
    """Проверяет mapper для timeout."""
    error = ClashTimeoutError(
        "Clash API request timed out.",
        endpoint="clans/%232ABC/currentwar",
        method="GET",
        status_code=None,
        response_snippet=None,
    )

    context = map_clash_api_error_to_context(
        error,
        entity_type="war",
        entity_tag="#2ABC",
        worker_name="sync_current_wars",
    )

    assert context.endpoint == "clans/%232ABC/currentwar"
    assert context.method == "GET"
    assert context.entity_type == "war"
    assert context.entity_tag == "#2ABC"
    assert context.status_code is None
    assert context.response_snippet is None
    assert context.exception_class == "ClashTimeoutError"
    assert context.worker_name == "sync_current_wars"
    assert context.retry_count == 0


def test_map_clash_api_error_to_context_rejects_negative_retry_count() -> None:
    """Проверяет запрет отрицательного retry_count."""
    error = ClashForbiddenError(
        "Clash API returned HTTP 403 for GET clans/%232ABC.",
        endpoint="clans/%232ABC",
        method="GET",
        status_code=403,
        response_snippet='{"reason":"accessDenied"}',
    )

    with pytest.raises(ValueError):
        map_clash_api_error_to_context(error, retry_count=-1)


def test_clash_api_error_context_does_not_include_sensitive_data() -> None:
    """Проверяет, что mapper не добавляет token/header-sensitive данные."""
    secret = "secret-clash-token"
    error = ClashForbiddenError(
        "Clash API returned HTTP 403 for GET clans/%232ABC.",
        endpoint="clans/%232ABC",
        method="GET",
        status_code=403,
        response_snippet='{"reason":"accessDenied"}',
    )

    context = map_clash_api_error_to_context(error)
    rendered_context = f"{context!r} {context.to_api_error_values()!r}"

    assert secret not in rendered_context
    assert "Authorization" not in rendered_context
    assert "Bearer" not in rendered_context


def test_clash_api_error_context_mapper_does_not_import_db_layer() -> None:
    """Проверяет, что mapper не зависит от DB-layer."""
    source = Path(error_context_module.__file__).read_text(encoding="utf-8")

    assert "app.db" not in source
