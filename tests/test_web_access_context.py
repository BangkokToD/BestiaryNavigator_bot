"""Тесты request context и access helpers web-слоя."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.web.context import (
    CurrentWebUser,
    WebRequestContext,
    build_template_context,
    build_web_request_context,
    get_current_web_user,
    get_web_request_context,
    is_admin_request,
    require_admin_context,
    require_admin_request,
    set_web_request_context,
    web_template_context_processor,
)


def test_default_web_request_context_is_anonymous_readonly() -> None:
    """Проверяет anonymous readonly context по умолчанию."""
    context = build_web_request_context()

    assert context.current_user == CurrentWebUser.anonymous()
    assert context.is_anonymous is True
    assert context.is_authenticated is False
    assert context.is_admin is False
    assert context.is_readonly is True
    assert context.role_label == "Просмотр"


def test_admin_web_request_context_is_not_readonly() -> None:
    """Проверяет admin context без полноценной RBAC-системы."""
    admin_user = CurrentWebUser.from_telegram_identity(
        telegram_id=123456789,
        username="admin",
        display_name="Admin User",
        is_admin=True,
    )

    context = build_web_request_context(admin_user)

    assert context.current_user == admin_user
    assert context.is_anonymous is False
    assert context.is_authenticated is True
    assert context.is_admin is True
    assert context.is_readonly is False
    assert context.role_label == "Админ"


def test_non_admin_authenticated_user_stays_readonly() -> None:
    """Проверяет, что обычный authenticated user остаётся readonly."""
    user = CurrentWebUser.from_telegram_identity(
        telegram_id=987654321,
        username="member",
        display_name="Member User",
        is_admin=False,
    )

    context = build_web_request_context(user)

    assert context.is_authenticated is True
    assert context.is_admin is False
    assert context.is_readonly is True
    assert context.role_label == "Пользователь"


def test_get_web_request_context_caches_context_on_request_state() -> None:
    """Проверяет кеширование context внутри request.state."""
    request = _build_request()

    first_context = get_web_request_context(request)
    second_context = get_web_request_context(request)

    assert first_context is second_context
    assert first_context.is_readonly is True


def test_set_web_request_context_overrides_request_context() -> None:
    """Проверяет ручную установку context для будущего auth-слоя."""
    request = _build_request()
    admin_context = build_web_request_context(
        CurrentWebUser.from_telegram_identity(
            telegram_id=123456789,
            username="admin",
            display_name="Admin User",
            is_admin=True,
        )
    )

    set_web_request_context(request, admin_context)

    assert get_web_request_context(request) is admin_context
    assert get_current_web_user(request) == admin_context.current_user
    assert is_admin_request(request) is True


def test_require_admin_context_rejects_anonymous_context() -> None:
    """Проверяет запрет admin-действий для anonymous context."""
    context = build_web_request_context()

    with pytest.raises(HTTPException) as exc_info:
        require_admin_context(context)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Доступ только для администратора."


def test_require_admin_request_returns_admin_context() -> None:
    """Проверяет успешный admin-check для request."""
    request = _build_request()
    admin_context = build_web_request_context(
        CurrentWebUser.from_telegram_identity(
            telegram_id=123456789,
            username="admin",
            display_name="Admin User",
            is_admin=True,
        )
    )
    set_web_request_context(request, admin_context)

    assert require_admin_request(request) is admin_context


def test_build_template_context_adds_web_context() -> None:
    """Проверяет базовый template context для SSR-страниц."""
    request = _build_request()

    context = build_template_context(request, page_title="Dashboard")

    assert context["page_title"] == "Dashboard"
    assert isinstance(context["web_context"], WebRequestContext)


def test_web_template_context_processor_uses_request_context() -> None:
    """Проверяет Jinja context processor для общего web context."""
    request = _build_request()
    custom_context = build_web_request_context(
        CurrentWebUser.from_telegram_identity(
            telegram_id=123456789,
            username="admin",
            display_name="Admin User",
            is_admin=True,
        )
    )
    set_web_request_context(request, custom_context)

    context = web_template_context_processor(request)

    assert context == {"web_context": custom_context}


def test_telegram_identity_rejects_invalid_telegram_id() -> None:
    """Проверяет базовую валидацию Telegram identity."""
    with pytest.raises(ValueError, match="telegram_id"):
        CurrentWebUser.from_telegram_identity(
            telegram_id=0,
            username=None,
            display_name=None,
            is_admin=False,
        )


def _build_request() -> Request:
    """Создаёт минимальный Starlette request для unit-тестов.

    Returns:
        Request без привязки к реальному ASGI-приложению.
    """
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
    )
