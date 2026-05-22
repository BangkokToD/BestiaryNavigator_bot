"""Request context и access helpers для web UI.

Модуль задаёт минимальную модель web-доступа для SSR-страниц. Это не RBAC:
в проекте пока есть только anonymous readonly mode и admin mode. Реальная
проверка Telegram Login и admin cookie добавляется отдельным коммитом.
"""

from dataclasses import dataclass
from typing import Self

from fastapi import HTTPException, Request, status

_WEB_CONTEXT_STATE_KEY = "web_context"


@dataclass(frozen=True, slots=True)
class CurrentWebUser:
    """Текущий пользователь web-интерфейса.

    Attributes:
        telegram_id: Telegram ID пользователя или `None` для anonymous.
        username: Telegram username без обязательного `@`.
        display_name: Отображаемое имя пользователя.
        is_authenticated: Признак пользователя после Telegram Login.
        is_admin: Признак администратора приложения.
    """

    telegram_id: int | None
    username: str | None
    display_name: str | None
    is_authenticated: bool
    is_admin: bool

    @classmethod
    def anonymous(cls) -> Self:
        """Создаёт anonymous web-user для публичного режима просмотра.

        Returns:
            Пользователь без Telegram Login и без admin-прав.
        """
        return cls(
            telegram_id=None,
            username=None,
            display_name=None,
            is_authenticated=False,
            is_admin=False,
        )

    @classmethod
    def from_telegram_identity(
        cls,
        *,
        telegram_id: int,
        username: str | None,
        display_name: str | None,
        is_admin: bool,
    ) -> Self:
        """Создаёт web-user из Telegram identity.

        Метод не проверяет подпись Telegram Login и не сравнивает ID с
        настройками. Эти действия относятся к web-auth слою следующего коммита.

        Args:
            telegram_id: Telegram ID пользователя.
            username: Telegram username.
            display_name: Отображаемое имя.
            is_admin: Результат admin-check, посчитанный внешним слоем.

        Returns:
            Authenticated web-user.

        Raises:
            ValueError: Если `telegram_id` некорректен.
        """
        if isinstance(telegram_id, bool) or telegram_id <= 0:
            raise ValueError("telegram_id должен быть положительным целым числом.")

        return cls(
            telegram_id=telegram_id,
            username=_normalize_optional_string(username),
            display_name=_normalize_optional_string(display_name),
            is_authenticated=True,
            is_admin=is_admin,
        )

    @property
    def label(self) -> str:
        """Возвращает короткую подпись пользователя для шаблонов.

        Returns:
            Username, display name или подпись anonymous-режима.
        """
        if self.username:
            return f"@{self.username}"

        if self.display_name:
            return self.display_name

        return "Гость"


@dataclass(frozen=True, slots=True)
class WebRequestContext:
    """Контекст текущего web-запроса.

    Attributes:
        current_user: Текущий пользователь web-интерфейса.
    """

    current_user: CurrentWebUser

    @property
    def is_admin(self) -> bool:
        """Проверяет, является ли текущий пользователь админом.

        Returns:
            `True`, если пользователь имеет admin mode.
        """
        return self.current_user.is_admin

    @property
    def is_authenticated(self) -> bool:
        """Проверяет, был ли выполнен Telegram Login.

        Returns:
            `True`, если пользователь аутентифицирован.
        """
        return self.current_user.is_authenticated

    @property
    def is_anonymous(self) -> bool:
        """Проверяет anonymous mode.

        Returns:
            `True`, если пользователь не прошёл Telegram Login.
        """
        return not self.current_user.is_authenticated

    @property
    def is_readonly(self) -> bool:
        """Проверяет режим только для чтения.

        В MVP все non-admin пользователи считаются readonly. Это сохраняет
        границу ТЗ: полноценная RBAC-система сейчас не реализуется.

        Returns:
            `True`, если текущему пользователю нельзя выполнять admin actions.
        """
        return not self.is_admin

    @property
    def role_label(self) -> str:
        """Возвращает человекочитаемую роль для SSR-шаблонов.

        Returns:
            Подпись режима доступа.
        """
        if self.is_admin:
            return "Админ"

        if self.is_authenticated:
            return "Пользователь"

        return "Просмотр"


def build_web_request_context(
    current_user: CurrentWebUser | None = None,
) -> WebRequestContext:
    """Создаёт web request context.

    Args:
        current_user: Текущий пользователь. Если не передан, создаётся
            anonymous readonly context.

    Returns:
        Web context текущего запроса.
    """
    return WebRequestContext(current_user=current_user or CurrentWebUser.anonymous())


def get_web_request_context(request: Request) -> WebRequestContext:
    """Возвращает web context из `request.state` или создаёт anonymous context.

    Args:
        request: FastAPI request.

    Returns:
        Web context текущего запроса.
    """
    existing_context = getattr(request.state, _WEB_CONTEXT_STATE_KEY, None)
    if isinstance(existing_context, WebRequestContext):
        return existing_context

    context = build_web_request_context()
    set_web_request_context(request, context)
    return context


def set_web_request_context(request: Request, context: WebRequestContext) -> None:
    """Сохраняет web context в `request.state`.

    Args:
        request: FastAPI request.
        context: Контекст, рассчитанный auth/access слоем.
    """
    setattr(request.state, _WEB_CONTEXT_STATE_KEY, context)


def get_current_web_user(request: Request) -> CurrentWebUser:
    """Возвращает текущего web-user.

    Args:
        request: FastAPI request.

    Returns:
        Текущий пользователь web-интерфейса.
    """
    return get_web_request_context(request).current_user


def is_admin_request(request: Request) -> bool:
    """Проверяет admin mode для request.

    Args:
        request: FastAPI request.

    Returns:
        `True`, если текущий request имеет admin context.
    """
    return get_web_request_context(request).is_admin


def require_admin_context(context: WebRequestContext) -> WebRequestContext:
    """Требует admin mode для будущих admin routes.

    Args:
        context: Web context текущего запроса.

    Returns:
        Исходный context, если пользователь admin.

    Raises:
        HTTPException: Если пользователь не является админом.
    """
    if not context.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ только для администратора.",
        )

    return context


def require_admin_request(request: Request) -> WebRequestContext:
    """FastAPI-compatible helper для проверки admin mode.

    Args:
        request: FastAPI request.

    Returns:
        Web context admin-пользователя.

    Raises:
        HTTPException: Если пользователь не является админом.
    """
    return require_admin_context(get_web_request_context(request))


def build_template_context(request: Request, **values: object) -> dict[str, object]:
    """Собирает базовый context для Jinja2 template response.

    Args:
        request: FastAPI request.
        **values: Дополнительные значения конкретной страницы.

    Returns:
        Словарь context для `TemplateResponse`.
    """
    context: dict[str, object] = {
        "web_context": get_web_request_context(request),
    }
    context.update(values)
    return context


def web_template_context_processor(request: Request) -> dict[str, object]:
    """Jinja context processor для общего web context.

    Args:
        request: FastAPI request.

    Returns:
        Значения, автоматически доступные во всех SSR-шаблонах.
    """
    return {
        "web_context": get_web_request_context(request),
    }


def _normalize_optional_string(value: str | None) -> str | None:
    """Нормализует опциональную строку.

    Args:
        value: Исходная строка или `None`.

    Returns:
        Строка без пробелов по краям или `None`.
    """
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


__all__ = [
    "CurrentWebUser",
    "WebRequestContext",
    "build_template_context",
    "build_web_request_context",
    "get_current_web_user",
    "get_web_request_context",
    "is_admin_request",
    "require_admin_context",
    "require_admin_request",
    "set_web_request_context",
    "web_template_context_processor",
]
