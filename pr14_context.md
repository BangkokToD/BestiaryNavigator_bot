# PR-14 context



## FILE: app/api/main.py

```python
"""Runtime entrypoint backend-сервиса."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.logging import configure_logging, get_logger
from app.web import STATIC_DIR, web_router

SERVICE_NAME = "backend"

configure_logging(SERVICE_NAME)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Управляет lifecycle backend-приложения.

    Args:
        _: Экземпляр FastAPI, который пока не требует доступа.

    Yields:
        Управление runtime-серверу FastAPI.
    """
    logger.info("Backend service started")
    try:
        yield
    finally:
        logger.info("Backend service stopped")


app = FastAPI(
    title="BestiaryNavigator_bot",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(web_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Возвращает состояние backend-сервиса.

    Returns:
        Минимальный health payload.
    """
    return {
        "status": "ok",
        "service": SERVICE_NAME,
    }

```


## FILE: app/web/__init__.py

```python
"""Web UI слой приложения."""

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
from app.web.routes import router as web_router
from app.web.templates import STATIC_DIR, TEMPLATES_DIR, templates

__all__ = [
    "STATIC_DIR",
    "TEMPLATES_DIR",
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
    "templates",
    "web_router",
    "web_template_context_processor",
]

```


## FILE: app/web/routes.py

```python
"""Базовые web routes приложения."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.web.admin_api_error_settings import router as admin_api_error_settings_router
from app.web.admin_api_errors import router as admin_api_errors_router
from app.web.admin_clan_settings import router as admin_clan_settings_router
from app.web.admin_clans import router as admin_clans_router
from app.web.admin_notification_routes import router as admin_notification_routes_router
from app.web.admin_telegram_settings import router as admin_telegram_settings_router
from app.web.auth import router as auth_router
from app.web.context import build_template_context
from app.web.templates import templates

router = APIRouter(include_in_schema=False)
router.include_router(auth_router)
router.include_router(admin_api_errors_router)
router.include_router(admin_api_error_settings_router)
router.include_router(admin_clan_settings_router)
router.include_router(admin_clans_router)
router.include_router(admin_telegram_settings_router)
router.include_router(admin_notification_routes_router)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> Response:
    """Отдаёт пустой dashboard skeleton.

    Args:
        request: FastAPI request, необходимый Jinja2 для `url_for`.

    Returns:
        HTML-страница dashboard.
    """
    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        build_template_context(
            request,
            page_title="Dashboard",
            active_nav="dashboard",
        ),
    )

```


## FILE: app/web/templates.py

```python
"""Jinja2 и static paths для web UI."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.web.context import web_template_context_processor

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = PROJECT_ROOT / "frontend" / "templates"
STATIC_DIR = PROJECT_ROOT / "frontend" / "static"

templates = Jinja2Templates(
    directory=TEMPLATES_DIR,
    context_processors=[web_template_context_processor],
)
"""Единый Jinja2Templates instance для SSR-страниц."""

```


## FILE: app/web/context.py

```python
"""Request context и access helpers для web UI.

Модуль задаёт минимальную модель web-доступа для SSR-страниц. Это не RBAC:
в проекте пока есть только anonymous readonly mode и admin mode. Реальная
проверка Telegram Login и admin cookie добавляется отдельным коммитом.
"""

from dataclasses import dataclass
from typing import Self

from fastapi import HTTPException, Request, status
from pydantic import ValidationError

from app.core.settings import get_settings
from app.web.security import verify_admin_cookie_value

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

    context = _build_context_from_admin_cookie(request) or build_web_request_context()
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


def _build_context_from_admin_cookie(request: Request) -> WebRequestContext | None:
    """Строит admin context из подписанной admin-cookie.

    Функция не делает запросов в БД. Cookie даёт только admin mode и только
    если она подписана `WEB_SESSION_SECRET`, а Telegram ID внутри совпадает с
    `TELEGRAM_ADMIN_ID` из settings.

    Args:
        request: FastAPI request.

    Returns:
        Admin context или `None`, если cookie отсутствует/невалидна.
    """
    if not request.cookies:
        return None

    try:
        settings = get_settings()
    except ValidationError:
        return None

    cookie_value = request.cookies.get(settings.web_admin_cookie_name)
    if not cookie_value:
        return None

    telegram_id = verify_admin_cookie_value(
        cookie_value,
        secret=settings.web_session_secret,
    )
    if telegram_id != settings.telegram_admin_id:
        return None

    return build_web_request_context(
        CurrentWebUser.from_telegram_identity(
            telegram_id=telegram_id,
            username=None,
            display_name=None,
            is_admin=True,
        )
    )


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

```


## FILE: app/web/auth.py

```python
"""Routes и зависимости Telegram Login для web UI."""

from collections.abc import AsyncIterator
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings, get_settings
from app.db.models import TelegramUser
from app.db.session import get_db_session
from app.services import TelegramUserService
from app.web.security import (
    TelegramLoginVerificationError,
    build_admin_cookie_value,
    verify_telegram_login_payload,
)

_ADMIN_COOKIE_MAX_AGE_SECONDS = 10 * 365 * 24 * 60 * 60
_ADMIN_COOKIE_PATH = "/"

router = APIRouter()


class TelegramLoginUserService(Protocol):
    """Минимальный contract сервиса Telegram-пользователей для web-auth."""

    async def upsert_telegram_user(
        self,
        *,
        telegram_id: int,
        username: str | None,
        display_name: str | None,
    ) -> TelegramUser:
        """Создаёт или обновляет Telegram-пользователя.

        Args:
            telegram_id: Telegram ID пользователя.
            username: Telegram username.
            display_name: Отображаемое имя.

        Returns:
            Модель TelegramUser.
        """

    def has_admin_access(self, telegram_user: TelegramUser | None) -> bool:
        """Проверяет admin-доступ пользователя.

        Args:
            telegram_user: Модель TelegramUser или `None`.

        Returns:
            `True`, если пользователь является админом.
        """


def get_web_auth_settings() -> Settings:
    """Возвращает settings для web-auth routes.

    Returns:
        Runtime settings приложения.
    """
    return get_settings()


async def get_telegram_login_user_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_web_auth_settings)],
) -> AsyncIterator[TelegramLoginUserService]:
    """Создаёт TelegramUserService с транзакционным commit/rollback.

    Args:
        session: Async SQLAlchemy session.
        settings: Runtime settings приложения.

    Yields:
        Сервис Telegram-пользователей для web-auth.
    """
    service = TelegramUserService.from_session(
        session=session,
        settings=settings,
    )

    try:
        yield service
        await session.commit()
    except Exception:
        await session.rollback()
        raise


@router.get("/auth/telegram", response_class=RedirectResponse)
async def telegram_login(
    request: Request,
    settings: Annotated[Settings, Depends(get_web_auth_settings)],
    telegram_users: Annotated[
        TelegramLoginUserService,
        Depends(get_telegram_login_user_service),
    ],
) -> RedirectResponse:
    """Обрабатывает callback Telegram Login Widget.

    Args:
        request: FastAPI request с query params Telegram Login.
        settings: Runtime settings.
        telegram_users: Сервис Telegram-пользователей.

    Returns:
        Redirect на Dashboard.

    Raises:
        HTTPException: Если Telegram Login payload невалиден.
    """
    try:
        payload = verify_telegram_login_payload(
            dict(request.query_params),
            bot_token=settings.telegram_bot_token,
        )
    except TelegramLoginVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    telegram_user = await telegram_users.upsert_telegram_user(
        telegram_id=payload.telegram_id,
        username=payload.username,
        display_name=payload.display_name,
    )

    response = _redirect_to_dashboard()
    if telegram_users.has_admin_access(telegram_user):
        _set_admin_cookie(
            response=response,
            settings=settings,
            telegram_id=telegram_user.telegram_id,
        )
    else:
        _delete_admin_cookie(response=response, settings=settings)

    return response


@router.api_route("/auth/logout", methods=["GET", "POST"], response_class=RedirectResponse)
async def telegram_logout(
    settings: Annotated[Settings, Depends(get_web_auth_settings)],
) -> RedirectResponse:
    """Очищает admin-cookie и возвращает пользователя на Dashboard.

    Args:
        settings: Runtime settings.

    Returns:
        Redirect на Dashboard без admin-cookie.
    """
    response = _redirect_to_dashboard()
    _delete_admin_cookie(response=response, settings=settings)

    return response


def _redirect_to_dashboard() -> RedirectResponse:
    """Создаёт redirect на Dashboard.

    Returns:
        Redirect response.
    """
    return RedirectResponse(
        url="/",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _set_admin_cookie(
    *,
    response: Response,
    settings: Settings,
    telegram_id: int,
) -> None:
    """Устанавливает подписанную admin-cookie.

    Args:
        response: Response, куда добавляется cookie.
        settings: Runtime settings с именем cookie и secret.
        telegram_id: Telegram ID администратора.
    """
    response.set_cookie(
        key=settings.web_admin_cookie_name,
        value=build_admin_cookie_value(
            telegram_id=telegram_id,
            secret=settings.web_session_secret,
        ),
        max_age=_ADMIN_COOKIE_MAX_AGE_SECONDS,
        path=_ADMIN_COOKIE_PATH,
        httponly=True,
        secure=_should_use_secure_cookie(settings),
        samesite="lax",
    )


def _delete_admin_cookie(*, response: Response, settings: Settings) -> None:
    """Удаляет admin-cookie.

    Args:
        response: Response, куда добавляется Set-Cookie на удаление.
        settings: Runtime settings с именем cookie.
    """
    response.delete_cookie(
        key=settings.web_admin_cookie_name,
        path=_ADMIN_COOKIE_PATH,
        secure=_should_use_secure_cookie(settings),
        httponly=True,
        samesite="lax",
    )


def _should_use_secure_cookie(settings: Settings) -> bool:
    """Определяет флаг Secure для admin-cookie.

    Args:
        settings: Runtime settings приложения.

    Returns:
        `True` только для production-окружения.
    """
    return settings.app_env == "prod"


__all__ = [
    "TelegramLoginUserService",
    "get_telegram_login_user_service",
    "get_web_auth_settings",
    "router",
]

```


## FILE: app/web/security.py

```python
"""Security helpers для Telegram Login и admin cookie.

Модуль не зависит от FastAPI routes, БД и шаблонов. Здесь находятся только
чистые функции проверки Telegram Login payload и подписи admin-cookie.
"""

import hmac
import time
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from pydantic import SecretStr

_TELEGRAM_LOGIN_MAX_AUTH_AGE_SECONDS = 24 * 60 * 60


class TelegramLoginVerificationError(ValueError):
    """Ошибка проверки Telegram Login payload."""


@dataclass(frozen=True, slots=True)
class TelegramLoginPayload:
    """Проверенный payload Telegram Login.

    Attributes:
        telegram_id: Telegram ID пользователя.
        auth_date: Unix timestamp авторизации.
        first_name: Имя из Telegram Login payload.
        last_name: Фамилия из Telegram Login payload.
        username: Username без обязательного `@`.
        photo_url: URL аватара, если Telegram его передал.
    """

    telegram_id: int
    auth_date: int
    first_name: str | None
    last_name: str | None
    username: str | None
    photo_url: str | None

    @property
    def display_name(self) -> str | None:
        """Собирает отображаемое имя пользователя.

        Returns:
            Имя из first/last name или username, если имени нет.
        """
        name_parts = [
            part
            for part in (
                self.first_name,
                self.last_name,
            )
            if part
        ]
        if name_parts:
            return " ".join(name_parts)

        return self.username


def verify_telegram_login_payload(
    payload: Mapping[str, object],
    *,
    bot_token: SecretStr | str,
    max_auth_age_seconds: int | None = _TELEGRAM_LOGIN_MAX_AUTH_AGE_SECONDS,
    now_ts: int | None = None,
) -> TelegramLoginPayload:
    """Проверяет подпись Telegram Login payload.

    Алгоритм соответствует Telegram Login Widget: из payload исключается
    `hash`, оставшиеся пары сортируются, склеиваются через `\\n`, затем
    проверяются HMAC-SHA256 с ключом `sha256(bot_token)`.

    Args:
        payload: Query/body поля Telegram Login.
        bot_token: Токен Telegram-бота из settings.
        max_auth_age_seconds: Максимальный возраст payload. `None` отключает
            проверку устаревания.
        now_ts: Текущий Unix timestamp для тестов.

    Returns:
        Проверенный и нормализованный Telegram Login payload.

    Raises:
        TelegramLoginVerificationError: Если подпись, обязательные поля или
            timestamp невалидны.
    """
    if not payload:
        raise TelegramLoginVerificationError("Telegram Login payload пустой.")

    actual_hash = _required_text(payload, "hash").lower()
    signed_fields = _build_signed_fields(payload)
    expected_hash = _build_telegram_login_hash(
        signed_fields,
        bot_token=bot_token,
    )

    if not hmac.compare_digest(actual_hash, expected_hash):
        raise TelegramLoginVerificationError("Подпись Telegram Login payload невалидна.")

    telegram_id = _required_positive_int(payload, "id")
    auth_date = _required_positive_int(payload, "auth_date")
    _validate_auth_date(
        auth_date,
        max_auth_age_seconds=max_auth_age_seconds,
        now_ts=now_ts,
    )

    return TelegramLoginPayload(
        telegram_id=telegram_id,
        auth_date=auth_date,
        first_name=_optional_text(payload.get("first_name")),
        last_name=_optional_text(payload.get("last_name")),
        username=_normalize_username(payload.get("username")),
        photo_url=_optional_text(payload.get("photo_url")),
    )


def build_admin_cookie_value(
    *,
    telegram_id: int,
    secret: SecretStr | str,
) -> str:
    """Создаёт подписанное значение admin-cookie.

    Args:
        telegram_id: Telegram ID администратора.
        secret: `WEB_SESSION_SECRET` из settings.

    Returns:
        Значение cookie в формате `{telegram_id}.{signature}`.

    Raises:
        ValueError: Если Telegram ID или secret невалидны.
    """
    normalized_telegram_id = _validate_positive_int(telegram_id, field_name="telegram_id")
    payload = str(normalized_telegram_id)
    signature = _build_admin_cookie_signature(payload, secret=secret)

    return f"{payload}.{signature}"


def verify_admin_cookie_value(
    value: str,
    *,
    secret: SecretStr | str,
) -> int | None:
    """Проверяет подписанное значение admin-cookie.

    Args:
        value: Значение cookie.
        secret: `WEB_SESSION_SECRET` из settings.

    Returns:
        Telegram ID из cookie или `None`, если cookie невалидна.
    """
    if not isinstance(value, str):
        return None

    payload, separator, signature = value.partition(".")
    if not separator or not payload or not signature:
        return None

    expected_signature = _build_admin_cookie_signature(payload, secret=secret)
    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        return _validate_positive_int(int(payload), field_name="telegram_id")
    except ValueError:
        return None


def _build_telegram_login_hash(
    signed_fields: Mapping[str, str],
    *,
    bot_token: SecretStr | str,
) -> str:
    """Строит ожидаемый hash Telegram Login payload."""
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(signed_fields.items()))
    secret_key = sha256(_secret_value(bot_token, field_name="bot_token").encode("utf-8")).digest()

    return hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        sha256,
    ).hexdigest()


def _build_admin_cookie_signature(payload: str, *, secret: SecretStr | str) -> str:
    """Строит HMAC-подпись admin-cookie payload."""
    normalized_payload = payload.strip()
    if not normalized_payload:
        raise ValueError("Admin cookie payload не может быть пустым.")

    return hmac.new(
        _secret_value(secret, field_name="web_session_secret").encode("utf-8"),
        normalized_payload.encode("utf-8"),
        sha256,
    ).hexdigest()


def _build_signed_fields(payload: Mapping[str, object]) -> dict[str, str]:
    """Возвращает поля, участвующие в Telegram Login подписи."""
    fields: dict[str, str] = {}

    for raw_key, raw_value in payload.items():
        key = str(raw_key).strip()
        if not key or key == "hash" or raw_value is None:
            continue

        fields[key] = str(raw_value)

    if not fields:
        raise TelegramLoginVerificationError("Telegram Login payload не содержит signed fields.")

    return fields


def _validate_auth_date(
    auth_date: int,
    *,
    max_auth_age_seconds: int | None,
    now_ts: int | None,
) -> None:
    """Проверяет свежесть Telegram Login payload."""
    if max_auth_age_seconds is None:
        return

    if isinstance(max_auth_age_seconds, bool) or max_auth_age_seconds <= 0:
        raise TelegramLoginVerificationError(
            "max_auth_age_seconds должен быть положительным числом или None."
        )

    current_ts = int(time.time()) if now_ts is None else now_ts
    if current_ts - auth_date > max_auth_age_seconds:
        raise TelegramLoginVerificationError("Telegram Login payload устарел.")


def _required_text(payload: Mapping[str, object], field_name: str) -> str:
    """Достаёт обязательное текстовое поле."""
    value = payload.get(field_name)
    if value is None:
        raise TelegramLoginVerificationError(f"Поле {field_name} обязательно.")

    normalized = str(value).strip()
    if not normalized:
        raise TelegramLoginVerificationError(f"Поле {field_name} не может быть пустым.")

    return normalized


def _required_positive_int(payload: Mapping[str, object], field_name: str) -> int:
    """Достаёт обязательное положительное целое число."""
    raw_value = _required_text(payload, field_name)

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise TelegramLoginVerificationError(f"Поле {field_name} должно быть числом.") from exc

    try:
        return _validate_positive_int(value, field_name=field_name)
    except ValueError as exc:
        raise TelegramLoginVerificationError(str(exc)) from exc


def _validate_positive_int(value: int, *, field_name: str) -> int:
    """Проверяет положительное целое число."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} должен быть целым числом.")

    if value <= 0:
        raise ValueError(f"{field_name} должен быть положительным числом.")

    return value


def _optional_text(value: object) -> str | None:
    """Нормализует опциональное текстовое значение."""
    if value is None:
        return None

    normalized = str(value).strip()
    return normalized or None


def _normalize_username(value: object) -> str | None:
    """Нормализует Telegram username."""
    normalized = _optional_text(value)
    if normalized is None:
        return None

    return normalized.removeprefix("@") or None


def _secret_value(value: SecretStr | str, *, field_name: str) -> str:
    """Достаёт secret value без логирования."""
    raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
    normalized = raw_value.strip()

    if not normalized:
        raise ValueError(f"{field_name} не может быть пустым.")

    return normalized


__all__ = [
    "TelegramLoginPayload",
    "TelegramLoginVerificationError",
    "build_admin_cookie_value",
    "verify_admin_cookie_value",
    "verify_telegram_login_payload",
]

```


## FILE: app/web/admin_clans.py

```python
"""Admin JSON routes управления отслеживаемыми кланами."""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, NoReturn, Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings, get_settings
from app.db.models import Clan
from app.db.session import get_db_session
from app.domain import ClanType, DomainValidationError
from app.integrations.clash import ClashApiClient, ClashApiError, ClashClan, ClashNotFoundError
from app.services import ClanManagementService
from app.services import ClanNotFoundError as ServiceClanNotFoundError
from app.web.context import WebRequestContext, require_admin_request

router = APIRouter(prefix="/admin/clans", tags=["admin-clans"])


class AdminClanService(Protocol):
    """Минимальный contract сервиса кланов для admin routes."""

    async def list_clans(self) -> tuple[Clan, ...]:
        """Возвращает список отслеживаемых кланов."""

    async def check_clan(self, *, clan_tag: str) -> ClashClan:
        """Проверяет клан через Clash API без сохранения."""

    async def add_clan(self, *, clan_tag: str, clan_type: ClanType | str) -> Clan:
        """Добавляет или реактивирует клан."""

    async def refresh_clan(self, *, clan_tag: str) -> Clan:
        """Обновляет данные клана из Clash API."""

    async def update_clan_type(self, *, clan_tag: str, clan_type: ClanType | str) -> Clan:
        """Меняет тип клана."""

    async def deactivate_clan(self, *, clan_tag: str) -> Clan:
        """Отключает мониторинг клана без физического удаления."""


class ClanTagRequest(BaseModel):
    """Payload с тегом клана."""

    clan_tag: str = Field(min_length=1, max_length=32)


class ClanMutationRequest(ClanTagRequest):
    """Payload создания/изменения клана."""

    clan_type: ClanType


class ClanResponse(BaseModel):
    """JSON-представление локального клана."""

    id: int | None
    tag: str
    name: str
    type: ClanType
    level: int | None
    badge_url: str | None
    is_active: bool
    last_sync_at: datetime | None
    sync_status: str | None

    @classmethod
    def from_model(cls, clan: Clan) -> "ClanResponse":
        """Создаёт response schema из модели `Clan`.

        Args:
            clan: SQLAlchemy model клана.

        Returns:
            JSON schema клана.
        """
        return cls(
            id=_optional_model_id(clan),
            tag=clan.tag,
            name=clan.name,
            type=ClanType(clan.type),
            level=clan.level,
            badge_url=clan.badge_url,
            is_active=clan.is_active,
            last_sync_at=clan.last_sync_at,
            sync_status=clan.sync_status,
        )


class VerifiedClanResponse(BaseModel):
    """JSON-представление клана, проверенного через Clash API."""

    tag: str
    name: str
    level: int | None
    badge_url: str | None
    members_count: int | None

    @classmethod
    def from_dto(cls, clan: ClashClan) -> "VerifiedClanResponse":
        """Создаёт response schema из DTO Clash API.

        Args:
            clan: DTO клана.

        Returns:
            JSON schema проверенного клана.
        """
        return cls(
            tag=clan.tag,
            name=clan.name,
            level=clan.level,
            badge_url=clan.badge_url,
            members_count=clan.members_count,
        )


class ClanListResponse(BaseModel):
    """Response списка кланов."""

    ok: bool = True
    clans: list[ClanResponse]


class ClanActionResponse(BaseModel):
    """Response действия над локальным кланом."""

    ok: bool = True
    clan: ClanResponse


class ClanCheckResponse(BaseModel):
    """Response проверки клана через Clash API."""

    ok: bool = True
    clan: VerifiedClanResponse


def get_admin_clan_settings() -> Settings:
    """Возвращает settings для admin clan routes.

    Returns:
        Runtime settings приложения.
    """
    return get_settings()


async def get_admin_clan_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_admin_clan_settings)],
) -> AsyncIterator[AdminClanService]:
    """Создаёт service управления кланами с транзакционным commit/rollback.

    Args:
        session: Async SQLAlchemy session.
        settings: Runtime settings приложения.

    Yields:
        Сервис управления кланами.
    """
    async with ClashApiClient.from_settings(settings) as clash_client:
        service = ClanManagementService.from_session(
            session=session,
            clash_client=clash_client,
        )

        try:
            yield service
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@router.get("", response_model=ClanListResponse)
async def list_admin_clans(
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    clan_service: Annotated[AdminClanService, Depends(get_admin_clan_service)],
) -> ClanListResponse:
    """Возвращает список кланов для admin UI.

    Args:
        _context: Admin web context.
        clan_service: Сервис кланов.

    Returns:
        JSON response со списком кланов.
    """
    clans = await clan_service.list_clans()

    return ClanListResponse(
        clans=[ClanResponse.from_model(clan) for clan in clans],
    )


@router.post("/check", response_model=ClanCheckResponse)
async def check_admin_clan(
    payload: ClanTagRequest,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    clan_service: Annotated[AdminClanService, Depends(get_admin_clan_service)],
) -> ClanCheckResponse:
    """Проверяет тег клана через Clash API без сохранения.

    Args:
        payload: Payload с тегом клана.
        _context: Admin web context.
        clan_service: Сервис кланов.

    Returns:
        JSON response с данными Clash API.
    """
    try:
        clan = await clan_service.check_clan(clan_tag=payload.clan_tag)
    except Exception as exc:
        _raise_admin_clan_error(exc)

    return ClanCheckResponse(clan=VerifiedClanResponse.from_dto(clan))


@router.post("", response_model=ClanActionResponse)
async def add_admin_clan(
    payload: ClanMutationRequest,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    clan_service: Annotated[AdminClanService, Depends(get_admin_clan_service)],
) -> ClanActionResponse:
    """Добавляет или реактивирует отслеживаемый клан.

    Args:
        payload: Payload с тегом и типом клана.
        _context: Admin web context.
        clan_service: Сервис кланов.

    Returns:
        JSON response с сохранённым кланом.
    """
    try:
        clan = await clan_service.add_clan(
            clan_tag=payload.clan_tag,
            clan_type=payload.clan_type,
        )
    except Exception as exc:
        _raise_admin_clan_error(exc)

    return ClanActionResponse(clan=ClanResponse.from_model(clan))


@router.post("/type", response_model=ClanActionResponse)
async def update_admin_clan_type(
    payload: ClanMutationRequest,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    clan_service: Annotated[AdminClanService, Depends(get_admin_clan_service)],
) -> ClanActionResponse:
    """Меняет тип отслеживаемого клана.

    Args:
        payload: Payload с тегом и новым типом клана.
        _context: Admin web context.
        clan_service: Сервис кланов.

    Returns:
        JSON response с обновлённым кланом.
    """
    try:
        clan = await clan_service.update_clan_type(
            clan_tag=payload.clan_tag,
            clan_type=payload.clan_type,
        )
    except Exception as exc:
        _raise_admin_clan_error(exc)

    return ClanActionResponse(clan=ClanResponse.from_model(clan))


@router.post("/refresh", response_model=ClanActionResponse)
async def refresh_admin_clan(
    payload: ClanTagRequest,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    clan_service: Annotated[AdminClanService, Depends(get_admin_clan_service)],
) -> ClanActionResponse:
    """Вручную обновляет локальные данные клана из Clash API.

    Args:
        payload: Payload с тегом клана.
        _context: Admin web context.
        clan_service: Сервис кланов.

    Returns:
        JSON response с обновлённым кланом.
    """
    try:
        clan = await clan_service.refresh_clan(clan_tag=payload.clan_tag)
    except Exception as exc:
        _raise_admin_clan_error(exc)

    return ClanActionResponse(clan=ClanResponse.from_model(clan))


@router.post("/deactivate", response_model=ClanActionResponse)
async def deactivate_admin_clan(
    payload: ClanTagRequest,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    clan_service: Annotated[AdminClanService, Depends(get_admin_clan_service)],
) -> ClanActionResponse:
    """Отключает мониторинг клана без физического удаления.

    Args:
        payload: Payload с тегом клана.
        _context: Admin web context.
        clan_service: Сервис кланов.

    Returns:
        JSON response с деактивированным кланом.
    """
    try:
        clan = await clan_service.deactivate_clan(clan_tag=payload.clan_tag)
    except Exception as exc:
        _raise_admin_clan_error(exc)

    return ClanActionResponse(clan=ClanResponse.from_model(clan))


def _raise_admin_clan_error(error: Exception) -> NoReturn:
    """Преобразует доменные и интеграционные ошибки в HTTPException.

    Args:
        error: Исключение нижнего слоя.

    Raises:
        HTTPException: Понятная HTTP-ошибка admin route.
    """
    if isinstance(error, ServiceClanNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "clan_not_found",
                "message": str(error),
            },
        ) from error

    if isinstance(error, ClashNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "clash_clan_not_found",
                "message": "Клан не найден в Clash of Clans API.",
                "clash_status_code": error.status_code,
            },
        ) from error

    if isinstance(error, ClashApiError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "clash_api_error",
                "message": "Clash API временно не выполнил запрос.",
                "clash_status_code": error.status_code,
            },
        ) from error

    if isinstance(error, DomainValidationError | ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_clan_payload",
                "message": str(error),
            },
        ) from error

    raise error


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
    "AdminClanService",
    "ClanActionResponse",
    "ClanCheckResponse",
    "ClanListResponse",
    "ClanMutationRequest",
    "ClanResponse",
    "ClanTagRequest",
    "VerifiedClanResponse",
    "get_admin_clan_service",
    "get_admin_clan_settings",
    "router",
]

```


## FILE: app/web/admin_clan_settings.py

```python
"""SSR-страница настроек отслеживаемых кланов."""

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.db.models import Clan
from app.domain import ClanType, DomainValidationError, normalize_clan_tag
from app.integrations.clash import ClashApiError, ClashClan, ClashNotFoundError
from app.services import ClanNotFoundError as ServiceClanNotFoundError
from app.web.admin_clans import AdminClanService, get_admin_clan_service
from app.web.context import WebRequestContext, build_template_context, require_admin_request
from app.web.templates import templates

router = APIRouter(prefix="/admin/settings/clans", tags=["admin-clan-settings"])

_CLAN_TYPE_LABELS = {
    ClanType.MAIN: "Основа",
    ClanType.ACADEMY: "Академия",
    ClanType.FREEZER: "Морозилка",
}
_NOTICE_MESSAGES = {
    "clan_added": "Клан добавлен или повторно включён в мониторинг.",
    "clan_type_updated": "Тип клана обновлён.",
    "clan_refreshed": "Данные клана обновлены из Clash API.",
    "clan_deactivated": "Мониторинг клана отключён. История сохранена.",
}


@dataclass(frozen=True, slots=True)
class ClanTypeOption:
    """Вариант типа клана для HTML-form.

    Attributes:
        value: Значение enum, отправляемое формой.
        label: Человекочитаемое название.
    """

    value: str
    label: str


@dataclass(frozen=True, slots=True)
class ClanCardView:
    """View model карточки клана.

    Attributes:
        tag: Нормализованный тег клана.
        name: Название клана.
        type: Тип клана.
        type_label: Человекочитаемый тип клана.
        level: Уровень клана.
        badge_url: URL badge клана.
        is_active: Активен ли мониторинг.
        last_sync_text: Текст времени последней синхронизации.
        sync_status: Статус синхронизации.
        type_action_target: Target ошибки формы смены типа.
        refresh_action_target: Target ошибки refresh-действия.
        deactivate_action_target: Target ошибки deactivate-действия.
    """

    tag: str
    name: str
    type: str
    type_label: str
    level: int | None
    badge_url: str | None
    is_active: bool
    last_sync_text: str
    sync_status: str
    type_action_target: str
    refresh_action_target: str
    deactivate_action_target: str


@dataclass(frozen=True, slots=True)
class VerifiedClanView:
    """View model результата проверки клана через Clash API.

    Attributes:
        tag: Тег клана.
        name: Название клана.
        level: Уровень клана.
        badge_url: URL badge.
        members_count: Количество участников.
    """

    tag: str
    name: str
    level: int | None
    badge_url: str | None
    members_count: int | None


@dataclass(frozen=True, slots=True)
class ClanSettingsFeedback:
    """Сообщение рядом с конкретным действием страницы.

    Attributes:
        target: Идентификатор формы/действия.
        message: Текст сообщения.
        variant: Вариант отображения: `error` или `success`.
    """

    target: str
    message: str
    variant: str = "error"


@router.get("", response_class=HTMLResponse)
async def clan_settings_page(
    request: Request,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    clan_service: Annotated[AdminClanService, Depends(get_admin_clan_service)],
    notice: str | None = None,
) -> Response:
    """Отдаёт admin-only страницу настроек кланов.

    Args:
        request: FastAPI request.
        _context: Admin web context.
        clan_service: Сервис управления кланами.
        notice: Код успешного действия после redirect.

    Returns:
        HTML-страница настроек кланов.
    """
    return await _render_clan_settings_page(
        request=request,
        clan_service=clan_service,
        notice=_normalize_notice(notice),
    )


@router.post("/check", response_class=HTMLResponse)
async def check_clan_settings_action(
    request: Request,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    clan_service: Annotated[AdminClanService, Depends(get_admin_clan_service)],
) -> Response:
    """Проверяет клан через Clash API и показывает результат на странице.

    Args:
        request: FastAPI request с form body.
        _context: Admin web context.
        clan_service: Сервис управления кланами.

    Returns:
        HTML-страница с результатом проверки или ошибкой.
    """
    form = await _read_urlencoded_form(request)

    try:
        clan_tag = _required_form_value(form, "clan_tag")
        verified_clan = await clan_service.check_clan(clan_tag=clan_tag)
    except Exception as exc:
        return await _render_clan_settings_page(
            request=request,
            clan_service=clan_service,
            form_values=form,
            feedback=ClanSettingsFeedback(
                target="check",
                message=_format_clan_action_error(exc),
            ),
            status_code=400,
        )

    return await _render_clan_settings_page(
        request=request,
        clan_service=clan_service,
        form_values=form,
        verified_clan=VerifiedClanView.from_dto(verified_clan),
        feedback=ClanSettingsFeedback(
            target="check",
            message="Клан найден в Clash API. Теперь его можно добавить.",
            variant="success",
        ),
    )


@router.post("/add", response_class=HTMLResponse)
async def add_clan_settings_action(
    request: Request,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    clan_service: Annotated[AdminClanService, Depends(get_admin_clan_service)],
) -> Response:
    """Добавляет или реактивирует клан через HTML-form.

    Args:
        request: FastAPI request с form body.
        _context: Admin web context.
        clan_service: Сервис управления кланами.

    Returns:
        Redirect после успеха или HTML-страница с ошибкой формы.
    """
    form = await _read_urlencoded_form(request)

    try:
        await clan_service.add_clan(
            clan_tag=_required_form_value(form, "clan_tag"),
            clan_type=_required_form_value(form, "clan_type"),
        )
    except Exception as exc:
        return await _render_clan_settings_page(
            request=request,
            clan_service=clan_service,
            form_values=form,
            feedback=ClanSettingsFeedback(
                target="add",
                message=_format_clan_action_error(exc),
            ),
            status_code=400,
        )

    return _redirect_with_notice("clan_added")


@router.post("/type", response_class=HTMLResponse)
async def update_clan_type_settings_action(
    request: Request,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    clan_service: Annotated[AdminClanService, Depends(get_admin_clan_service)],
) -> Response:
    """Меняет тип клана через HTML-form.

    Args:
        request: FastAPI request с form body.
        _context: Admin web context.
        clan_service: Сервис управления кланами.

    Returns:
        Redirect после успеха или HTML-страница с ошибкой действия.
    """
    form = await _read_urlencoded_form(request)
    clan_tag = form.get("clan_tag", "")
    target = _build_action_target("type", clan_tag)

    try:
        await clan_service.update_clan_type(
            clan_tag=_required_form_value(form, "clan_tag"),
            clan_type=_required_form_value(form, "clan_type"),
        )
    except Exception as exc:
        return await _render_clan_settings_page(
            request=request,
            clan_service=clan_service,
            form_values=form,
            feedback=ClanSettingsFeedback(
                target=target,
                message=_format_clan_action_error(exc),
            ),
            status_code=400,
        )

    return _redirect_with_notice("clan_type_updated")


@router.post("/refresh", response_class=HTMLResponse)
async def refresh_clan_settings_action(
    request: Request,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    clan_service: Annotated[AdminClanService, Depends(get_admin_clan_service)],
) -> Response:
    """Вручную обновляет клан через HTML-form.

    Args:
        request: FastAPI request с form body.
        _context: Admin web context.
        clan_service: Сервис управления кланами.

    Returns:
        Redirect после успеха или HTML-страница с ошибкой действия.
    """
    form = await _read_urlencoded_form(request)
    clan_tag = form.get("clan_tag", "")
    target = _build_action_target("refresh", clan_tag)

    try:
        await clan_service.refresh_clan(
            clan_tag=_required_form_value(form, "clan_tag"),
        )
    except Exception as exc:
        return await _render_clan_settings_page(
            request=request,
            clan_service=clan_service,
            form_values=form,
            feedback=ClanSettingsFeedback(
                target=target,
                message=_format_clan_action_error(exc),
            ),
            status_code=400,
        )

    return _redirect_with_notice("clan_refreshed")


@router.post("/deactivate", response_class=HTMLResponse)
async def deactivate_clan_settings_action(
    request: Request,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    clan_service: Annotated[AdminClanService, Depends(get_admin_clan_service)],
) -> Response:
    """Отключает мониторинг клана без удаления истории.

    Args:
        request: FastAPI request с form body.
        _context: Admin web context.
        clan_service: Сервис управления кланами.

    Returns:
        Redirect после успеха или HTML-страница с ошибкой действия.
    """
    form = await _read_urlencoded_form(request)
    clan_tag = form.get("clan_tag", "")
    target = _build_action_target("deactivate", clan_tag)

    try:
        await clan_service.deactivate_clan(
            clan_tag=_required_form_value(form, "clan_tag"),
        )
    except Exception as exc:
        return await _render_clan_settings_page(
            request=request,
            clan_service=clan_service,
            form_values=form,
            feedback=ClanSettingsFeedback(
                target=target,
                message=_format_clan_action_error(exc),
            ),
            status_code=400,
        )

    return _redirect_with_notice("clan_deactivated")


async def _render_clan_settings_page(
    *,
    request: Request,
    clan_service: AdminClanService,
    form_values: Mapping[str, str] | None = None,
    verified_clan: VerifiedClanView | None = None,
    feedback: ClanSettingsFeedback | None = None,
    notice: str | None = None,
    status_code: int = 200,
) -> Response:
    """Рендерит страницу настроек кланов.

    Args:
        request: FastAPI request.
        clan_service: Сервис управления кланами.
        form_values: Значения последней формы.
        verified_clan: Результат проверки через Clash API.
        feedback: Сообщение возле действия.
        notice: Глобальное сообщение после redirect.
        status_code: HTTP status code.

    Returns:
        HTML response.
    """
    clans = await clan_service.list_clans()
    context = build_template_context(
        request,
        page_title="Настройки кланов",
        active_nav="admin_clans",
        clans=[ClanCardView.from_model(clan) for clan in clans],
        clan_types=_build_clan_type_options(),
        form_values=dict(form_values or {}),
        verified_clan=verified_clan,
        action_feedback=feedback,
        notice=notice,
    )

    return templates.TemplateResponse(
        request,
        "admin/clans/settings.html",
        context,
        status_code=status_code,
    )


async def _read_urlencoded_form(request: Request) -> dict[str, str]:
    """Читает `application/x-www-form-urlencoded` body без python-multipart.

    Args:
        request: FastAPI request.

    Returns:
        Словарь последнего значения каждого поля формы.
    """
    raw_body = await request.body()
    if not raw_body:
        return {}

    parsed = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1].strip() if values else "" for key, values in parsed.items()}


def _required_form_value(form: Mapping[str, str], field_name: str) -> str:
    """Достаёт обязательное значение формы.

    Args:
        form: Данные формы.
        field_name: Имя поля.

    Returns:
        Непустое значение.

    Raises:
        ValueError: Если поле отсутствует или пустое.
    """
    value = form.get(field_name, "").strip()
    if value:
        return value

    if field_name == "clan_tag":
        raise ValueError("Тег клана обязателен.")

    if field_name == "clan_type":
        raise ValueError("Тип клана обязателен.")

    raise ValueError(f"Поле {field_name} обязательно.")


def _format_clan_action_error(error: Exception) -> str:
    """Преобразует ошибку clan action в безопасный текст для формы.

    Args:
        error: Исключение нижнего слоя.

    Returns:
        Текст ошибки для страницы.
    """
    if isinstance(error, ServiceClanNotFoundError):
        return str(error)

    if isinstance(error, ClashNotFoundError):
        return "Клан не найден в Clash of Clans API."

    if isinstance(error, ClashApiError):
        return "Clash API временно не выполнил запрос. Попробуй позже."

    if isinstance(error, DomainValidationError | ValueError):
        return str(error)

    return "Не удалось выполнить действие. Подробности смотри в логах backend."


def _redirect_with_notice(notice: str) -> RedirectResponse:
    """Создаёт redirect на страницу настроек кланов.

    Args:
        notice: Код сообщения.

    Returns:
        Redirect response.
    """
    return RedirectResponse(
        url=f"/admin/settings/clans?notice={notice}",
        status_code=303,
    )


def _normalize_notice(value: str | None) -> str | None:
    """Нормализует код success-сообщения.

    Args:
        value: Код из query params.

    Returns:
        Текст сообщения или `None`.
    """
    if value is None:
        return None

    return _NOTICE_MESSAGES.get(value.strip())


def _build_clan_type_options() -> list[ClanTypeOption]:
    """Создаёт список типов кланов для select.

    Returns:
        Опции типов клана.
    """
    return [
        ClanTypeOption(value=clan_type.value, label=_CLAN_TYPE_LABELS[clan_type])
        for clan_type in ClanType
    ]


def _build_action_target(action: str, clan_tag: str) -> str:
    """Строит target сообщения для карточки клана.

    Args:
        action: Название действия.
        clan_tag: Тег клана.

    Returns:
        Stable target вида `action:#TAG`.
    """
    normalized_action = action.strip()
    normalized_tag = clan_tag.strip()

    with suppress(Exception):
        normalized_tag = normalize_clan_tag(normalized_tag)

    return f"{normalized_action}:{normalized_tag}"


def _format_last_sync(clan: Clan) -> str:
    """Форматирует время последней синхронизации.

    Args:
        clan: Модель клана.

    Returns:
        Текст для карточки.
    """
    if clan.last_sync_at is None:
        return "ещё не было"

    return clan.last_sync_at.strftime("%Y-%m-%d %H:%M UTC")


def _clan_type_label(value: str) -> str:
    """Возвращает подпись типа клана.

    Args:
        value: Строковый тип клана.

    Returns:
        Человекочитаемый тип.
    """
    try:
        return _CLAN_TYPE_LABELS[ClanType(value)]
    except ValueError:
        return value


def _sync_status_label(value: str | None) -> str:
    """Возвращает подпись sync status.

    Args:
        value: Статус синхронизации.

    Returns:
        Человекочитаемый статус.
    """
    if value == "ok":
        return "ok"

    return value or "нет данных"


@classmethod
def _clan_card_from_model(cls: type[ClanCardView], clan: Clan) -> ClanCardView:
    """Создаёт view model карточки клана.

    Args:
        cls: Класс view model.
        clan: Модель клана.

    Returns:
        View model карточки.
    """
    return cls(
        tag=clan.tag,
        name=clan.name,
        type=clan.type,
        type_label=_clan_type_label(clan.type),
        level=clan.level,
        badge_url=clan.badge_url,
        is_active=clan.is_active,
        last_sync_text=_format_last_sync(clan),
        sync_status=_sync_status_label(clan.sync_status),
        type_action_target=_build_action_target("type", clan.tag),
        refresh_action_target=_build_action_target("refresh", clan.tag),
        deactivate_action_target=_build_action_target("deactivate", clan.tag),
    )


@classmethod
def _verified_clan_from_dto(cls: type[VerifiedClanView], clan: ClashClan) -> VerifiedClanView:
    """Создаёт view model результата Clash API check.

    Args:
        cls: Класс view model.
        clan: DTO Clash API.

    Returns:
        View model проверенного клана.
    """
    return cls(
        tag=clan.tag,
        name=clan.name,
        level=clan.level,
        badge_url=clan.badge_url,
        members_count=clan.members_count,
    )


ClanCardView.from_model = _clan_card_from_model  # type: ignore[attr-defined]
VerifiedClanView.from_dto = _verified_clan_from_dto  # type: ignore[attr-defined]


__all__ = [
    "ClanCardView",
    "ClanSettingsFeedback",
    "ClanTypeOption",
    "VerifiedClanView",
    "router",
]

```


## FILE: app/web/admin_telegram_settings.py

```python
"""SSR-страница настроек Telegram routes."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Clan, NotificationRoute
from app.db.session import get_db_session
from app.domain import ClanType, NotificationType
from app.integrations.clash import ClashClan
from app.services import ClanManagementService, NotificationRouteService
from app.web.context import WebRequestContext, build_template_context, require_admin_request
from app.web.templates import templates

router = APIRouter(prefix="/admin/settings/telegram", tags=["admin-telegram-settings"])


_NOTIFICATION_TYPE_LABELS = {
    NotificationType.WAR_PREPARATION_STARTED: "Подготовка войны",
    NotificationType.WAR_STARTED: "Война началась",
    NotificationType.WAR_6H_REMINDER: "Война: прошло 6 часов",
    NotificationType.WAR_12H_REMINDER: "Война: прошло 12 часов",
    NotificationType.WAR_3H_LEFT: "Война: осталось 3 часа",
    NotificationType.WAR_1H_LEFT: "Война: остался 1 час",
    NotificationType.WAR_ENDED: "Война закончилась",
    NotificationType.RAID_STARTED: "Рейды начались",
    NotificationType.RAID_LAUNCHED: "Рейды запущены",
    NotificationType.RAID_12H_REPORT: "Рейдовый отчёт",
    NotificationType.CWL_STARTED: "ЛВК началась",
    NotificationType.CWL_ROUND_REPORT: "Отчёт по раунду ЛВК",
    NotificationType.WARN_CREATED: "Warn создан",
    NotificationType.WARN_CANCELLED: "Warn отменён",
    NotificationType.KICK_CANDIDATES_EVENING: "Вечерний список кандидатов на кик",
    NotificationType.UNLINKED_ACCOUNTS_EVENING: "Вечерний список непривязанных",
    NotificationType.API_ERRORS_ADMIN: "Ошибки API для админа",
    NotificationType.DAILY_ADMIN_REPORT: "Ежедневный админский отчёт",
}
_CLAN_TYPE_LABELS = {
    ClanType.MAIN: "Основа",
    ClanType.ACADEMY: "Академия",
    ClanType.FREEZER: "Морозилка",
}


class TelegramSettingsClanService(Protocol):
    """Минимальный contract сервиса кланов для страницы Telegram settings."""

    async def list_clans(self) -> tuple[Clan, ...]:
        """Возвращает список кланов."""


class TelegramSettingsRouteService(Protocol):
    """Минимальный contract сервиса notification routes для страницы."""

    async def list_all_routes(
        self,
        *,
        include_disabled: bool = True,
    ) -> tuple[NotificationRoute, ...]:
        """Возвращает все маршруты уведомлений."""


@dataclass(frozen=True, slots=True)
class TelegramSettingsDependencies:
    """Набор зависимостей страницы Telegram settings."""

    clan_service: TelegramSettingsClanService
    route_service: TelegramSettingsRouteService


@dataclass(frozen=True, slots=True)
class TelegramRouteView:
    """View model одного Telegram route.

    Attributes:
        id: DB ID route.
        chat_id: Telegram chat id.
        chat_title: Название Telegram-чата.
        message_thread_id: Telegram topic/thread id.
        enabled: Включён ли route.
        status_label: Человекочитаемый статус.
        status_variant: Вариант badge.
        test_url: URL backend endpoint для тестовой отправки.
        disable_url: URL backend endpoint для отключения.
    """

    id: int
    chat_id: int
    chat_title: str
    message_thread_id: int | None
    enabled: bool
    status_label: str
    status_variant: str
    test_url: str
    disable_url: str

    @classmethod
    def from_model(cls, route: NotificationRoute) -> "TelegramRouteView":
        """Создаёт view model из `NotificationRoute`.

        Args:
            route: Модель маршрута.

        Returns:
            View model route.
        """
        route_id = _required_model_id(route, model_name="NotificationRoute")
        chat = route.__dict__.get("chat")
        chat_title = _optional_string(getattr(chat, "title", None)) or "Без названия"

        return cls(
            id=route_id,
            chat_id=route.chat_id,
            chat_title=chat_title,
            message_thread_id=route.message_thread_id,
            enabled=route.enabled,
            status_label="Подключён" if route.enabled else "Отключён",
            status_variant="info" if route.enabled else "muted",
            test_url=f"/admin/notification-routes/{route_id}/test",
            disable_url=f"/admin/notification-routes/{route_id}/disable",
        )


@dataclass(frozen=True, slots=True)
class NotificationTypeRouteView:
    """View model строки notification type.

    Attributes:
        value: Строковый тип уведомления.
        label: Человекочитаемое название.
        command: Готовая команда `/register`.
        routes: Все routes для пары clan + notification type.
        status_label: Итоговый статус типа.
        status_variant: Вариант badge.
    """

    value: str
    label: str
    command: str
    routes: tuple[TelegramRouteView, ...]
    status_label: str
    status_variant: str


@dataclass(frozen=True, slots=True)
class TelegramClanCardView:
    """View model карточки клана Telegram settings.

    Attributes:
        id: DB ID клана.
        tag: Тег клана.
        name: Название клана.
        type_label: Человекочитаемый тип клана.
        badge_url: URL badge.
        notification_types: Строки notification types.
    """

    id: int
    tag: str
    name: str
    type_label: str
    badge_url: str | None
    notification_types: tuple[NotificationTypeRouteView, ...] = field(default_factory=tuple)


class _UnavailableClashClanProvider:
    """Clash provider-заглушка для read-only страницы Telegram settings."""

    async def get_clan(self, clan_tag: str) -> ClashClan:
        """Запрещает Clash API вызовы на read-only странице.

        Args:
            clan_tag: Тег клана.

        Raises:
            RuntimeError: Всегда, если page layer ошибочно вызвал Clash API.
        """
        raise RuntimeError(
            f"Clash API не должен вызываться страницей Telegram settings: {clan_tag}."
        )


async def get_telegram_settings_dependencies(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AsyncIterator[TelegramSettingsDependencies]:
    """Создаёт зависимости страницы Telegram settings.

    Args:
        session: Async SQLAlchemy session.

    Yields:
        Набор read-only сервисов страницы.
    """
    dependencies = TelegramSettingsDependencies(
        clan_service=ClanManagementService.from_session(
            session=session,
            clash_client=_UnavailableClashClanProvider(),
        ),
        route_service=NotificationRouteService.from_session(session=session),
    )

    try:
        yield dependencies
        await session.commit()
    except Exception:
        await session.rollback()
        raise


@router.get("", response_class=HTMLResponse)
async def telegram_settings_page(
    request: Request,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    dependencies: Annotated[
        TelegramSettingsDependencies,
        Depends(get_telegram_settings_dependencies),
    ],
) -> Response:
    """Отдаёт admin-only страницу Telegram notification routes.

    Args:
        request: FastAPI request.
        _context: Admin web context.
        dependencies: Read-only зависимости страницы.

    Returns:
        HTML-страница Telegram settings.
    """
    clans = await dependencies.clan_service.list_clans()
    routes = await dependencies.route_service.list_all_routes(include_disabled=True)

    return templates.TemplateResponse(
        request,
        "admin/telegram/settings.html",
        build_template_context(
            request,
            page_title="Настройки Telegram",
            active_nav="admin_telegram",
            clans=_build_clan_cards(clans=clans, routes=routes),
            notification_type_count=len(NotificationType),
        ),
    )


def _build_clan_cards(
    *,
    clans: tuple[Clan, ...],
    routes: tuple[NotificationRoute, ...],
) -> list[TelegramClanCardView]:
    """Собирает карточки кланов с notification routes.

    Args:
        clans: Кланы.
        routes: Все notification routes.

    Returns:
        Список view model карточек.
    """
    routes_by_key = _group_routes_by_clan_and_type(routes)

    return [
        _build_clan_card(
            clan=clan,
            routes_by_key=routes_by_key,
        )
        for clan in clans
    ]


def _build_clan_card(
    *,
    clan: Clan,
    routes_by_key: dict[tuple[int, str], list[NotificationRoute]],
) -> TelegramClanCardView:
    """Собирает одну карточку клана.

    Args:
        clan: Модель клана.
        routes_by_key: Routes, сгруппированные по clan id и notification type.

    Returns:
        View model карточки.
    """
    clan_id = _required_model_id(clan, model_name="Clan")
    notification_types = []

    for notification_type in NotificationType:
        type_routes = tuple(
            TelegramRouteView.from_model(route)
            for route in routes_by_key.get((clan_id, notification_type.value), [])
        )
        notification_types.append(
            _build_notification_type_view(
                clan=clan,
                notification_type=notification_type,
                routes=type_routes,
            )
        )

    return TelegramClanCardView(
        id=clan_id,
        tag=clan.tag,
        name=clan.name,
        type_label=_clan_type_label(clan.type),
        badge_url=clan.badge_url,
        notification_types=tuple(notification_types),
    )


def _build_notification_type_view(
    *,
    clan: Clan,
    notification_type: NotificationType,
    routes: tuple[TelegramRouteView, ...],
) -> NotificationTypeRouteView:
    """Собирает строку notification type.

    Args:
        clan: Модель клана.
        notification_type: Тип уведомления.
        routes: Routes для этой пары clan/type.

    Returns:
        View model notification type.
    """
    enabled_routes = [route for route in routes if route.enabled]
    disabled_routes = [route for route in routes if not route.enabled]

    if enabled_routes:
        status_label = "Подключено"
        status_variant = "info"
    elif disabled_routes:
        status_label = "Отключено"
        status_variant = "muted"
    else:
        status_label = "Не подключено"
        status_variant = "muted"

    return NotificationTypeRouteView(
        value=notification_type.value,
        label=_notification_type_label(notification_type),
        command=_build_register_command(clan_tag=clan.tag, notification_type=notification_type),
        routes=routes,
        status_label=status_label,
        status_variant=status_variant,
    )


def _group_routes_by_clan_and_type(
    routes: tuple[NotificationRoute, ...],
) -> dict[tuple[int, str], list[NotificationRoute]]:
    """Группирует routes по clan_id и notification_type.

    Args:
        routes: Notification routes.

    Returns:
        Словарь групп routes.
    """
    grouped: dict[tuple[int, str], list[NotificationRoute]] = {}

    for route in routes:
        grouped.setdefault((route.clan_id, route.notification_type), []).append(route)

    return grouped


def _build_register_command(
    *,
    clan_tag: str,
    notification_type: NotificationType,
) -> str:
    """Собирает команду регистрации route.

    Args:
        clan_tag: Тег клана.
        notification_type: Тип уведомления.

    Returns:
        Команда `/register`.
    """
    return f"/register {clan_tag} {notification_type.value}"


def _notification_type_label(notification_type: NotificationType) -> str:
    """Возвращает человекочитаемое название notification type.

    Args:
        notification_type: Тип уведомления.

    Returns:
        Название для UI.
    """
    return _NOTIFICATION_TYPE_LABELS.get(notification_type, notification_type.value)


def _clan_type_label(value: str) -> str:
    """Возвращает человекочитаемый тип клана.

    Args:
        value: Значение типа клана.

    Returns:
        Название типа клана.
    """
    try:
        return _CLAN_TYPE_LABELS[ClanType(value)]
    except ValueError:
        return value


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id модели.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.

    Raises:
        RuntimeError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise RuntimeError(f"{model_name} должен быть сохранён в БД.")


def _optional_string(value: object) -> str | None:
    """Нормализует опциональную строку.

    Args:
        value: Исходное значение.

    Returns:
        Непустая строка или `None`.
    """
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    return normalized or None


__all__ = [
    "NotificationTypeRouteView",
    "TelegramClanCardView",
    "TelegramRouteView",
    "TelegramSettingsDependencies",
    "get_telegram_settings_dependencies",
    "router",
]

```


## FILE: app/web/admin_notification_routes.py

```python
"""Admin JSON routes управления Telegram notification routes."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, NoReturn, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings, get_settings
from app.db.models import NotificationRoute
from app.db.session import get_db_session
from app.domain import (
    ClanType,
    DomainValidationError,
    NotificationType,
    normalize_clan_tag,
    require_domain_enum_value,
)
from app.services import (
    AiogramTelegramNotificationSender,
    NotificationLogService,
    NotificationRouteNotFoundError,
    NotificationRouteService,
    NotificationRouteServiceError,
    NotificationRouteStateResult,
    RenderedNotification,
    TelegramNotificationSender,
    TelegramNotificationSenderError,
)
from app.web.context import WebRequestContext, require_admin_request

router = APIRouter(prefix="/admin/notification-routes", tags=["admin-notification-routes"])


class AdminNotificationRouteService(Protocol):
    """Минимальный contract сервиса notification routes для admin endpoints."""

    async def list_all_routes(
        self,
        *,
        include_disabled: bool = True,
    ) -> tuple[NotificationRoute, ...]:
        """Возвращает все маршруты уведомлений."""

    async def get_route_by_id(self, *, route_id: int) -> NotificationRoute:
        """Возвращает route по DB ID."""

    async def disable_route(self, *, route_id: int) -> NotificationRouteStateResult:
        """Отключает route без удаления истории."""


class AdminNotificationLogService(Protocol):
    """Минимальный contract сервиса notification logs для test endpoint."""

    async def record_sent(
        self,
        *,
        notification_type: NotificationType | str,
        payload_summary: str,
        telegram_message_id: int,
        event_key: str | None = None,
        route: NotificationRoute | None = None,
        chat_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> object:
        """Фиксирует successful test notification."""

    async def record_failed(
        self,
        *,
        notification_type: NotificationType | str,
        payload_summary: str,
        error_text: str,
        event_key: str | None = None,
        route: NotificationRoute | None = None,
        chat_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> object:
        """Фиксирует failed test notification."""


@dataclass(frozen=True, slots=True)
class AdminNotificationRouteDependencies:
    """Набор зависимостей admin notification route endpoints."""

    route_service: AdminNotificationRouteService
    log_service: AdminNotificationLogService
    sender: TelegramNotificationSender


class NotificationRegisterCommandResponse(BaseModel):
    """Response команды регистрации Telegram route."""

    ok: bool = True
    clan_tag: str
    notification_type: NotificationType
    command: str


class NotificationRouteResponse(BaseModel):
    """JSON-представление notification route."""

    id: int | None
    clan_id: int
    clan_tag: str | None
    clan_name: str | None
    clan_type: ClanType | None
    notification_type: NotificationType
    chat_id: int
    chat_title: str | None
    message_thread_id: int | None
    enabled: bool
    register_command: str | None

    @classmethod
    def from_model(cls, route: NotificationRoute) -> "NotificationRouteResponse":
        """Создаёт response schema из модели `NotificationRoute`.

        Args:
            route: SQLAlchemy model маршрута.

        Returns:
            JSON schema маршрута.
        """
        clan = route.__dict__.get("clan")
        chat = route.__dict__.get("chat")
        clan_tag = _optional_attr(clan, "tag")
        clan_type = _optional_clan_type(_optional_attr(clan, "type"))

        return cls(
            id=_optional_model_id(route),
            clan_id=route.clan_id,
            clan_tag=clan_tag,
            clan_name=_optional_attr(clan, "name"),
            clan_type=clan_type,
            notification_type=NotificationType(route.notification_type),
            chat_id=route.chat_id,
            chat_title=_optional_attr(chat, "title"),
            message_thread_id=route.message_thread_id,
            enabled=route.enabled,
            register_command=_build_register_command(
                clan_tag=clan_tag,
                notification_type=route.notification_type,
            )
            if clan_tag is not None
            else None,
        )


class NotificationRouteListResponse(BaseModel):
    """Response списка notification routes."""

    ok: bool = True
    routes: list[NotificationRouteResponse]


class NotificationRouteStatusResponse(BaseModel):
    """Response статуса notification route."""

    ok: bool = True
    route: NotificationRouteResponse


class NotificationRouteDisableResponse(BaseModel):
    """Response отключения notification route."""

    ok: bool = True
    changed: bool
    route: NotificationRouteResponse


class NotificationRouteTestResponse(BaseModel):
    """Response тестовой отправки notification route."""

    ok: bool = True
    route: NotificationRouteResponse
    telegram_message_id: int


def get_admin_notification_route_settings() -> Settings:
    """Возвращает settings для admin notification route endpoints.

    Returns:
        Runtime settings приложения.
    """
    return get_settings()


async def get_admin_notification_route_dependencies(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_admin_notification_route_settings)],
) -> AsyncIterator[AdminNotificationRouteDependencies]:
    """Создаёт зависимости admin notification route endpoints.

    Args:
        session: Async SQLAlchemy session.
        settings: Runtime settings приложения.

    Yields:
        Набор сервисов и sender для endpoint-ов.
    """
    async with AiogramTelegramNotificationSender.from_settings(settings=settings) as sender:
        dependencies = AdminNotificationRouteDependencies(
            route_service=NotificationRouteService.from_session(session=session),
            log_service=NotificationLogService.from_session(session=session),
            sender=sender,
        )

        try:
            yield dependencies
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@router.get("", response_model=NotificationRouteListResponse)
async def list_admin_notification_routes(
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    dependencies: Annotated[
        AdminNotificationRouteDependencies,
        Depends(get_admin_notification_route_dependencies),
    ],
    include_disabled: bool = Query(default=True),
) -> NotificationRouteListResponse:
    """Возвращает список notification routes.

    Args:
        _context: Admin web context.
        dependencies: Сервисы admin notification routes.
        include_disabled: Возвращать ли disabled routes.

    Returns:
        JSON response со списком маршрутов.
    """
    routes = await dependencies.route_service.list_all_routes(
        include_disabled=include_disabled,
    )

    return NotificationRouteListResponse(
        routes=[NotificationRouteResponse.from_model(route) for route in routes],
    )


@router.get("/register-command", response_model=NotificationRegisterCommandResponse)
async def get_admin_notification_register_command(
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    clan_tag: Annotated[str, Query(min_length=1, max_length=32)],
    notification_type: Annotated[NotificationType, Query()],
) -> NotificationRegisterCommandResponse:
    """Возвращает команду регистрации route для копирования в Telegram.

    Args:
        _context: Admin web context.
        clan_tag: Тег клана.
        notification_type: Тип уведомления.

    Returns:
        JSON response с готовой командой `/register`.
    """
    normalized_clan_tag = normalize_clan_tag(clan_tag)
    command = _build_register_command(
        clan_tag=normalized_clan_tag,
        notification_type=notification_type.value,
    )

    return NotificationRegisterCommandResponse(
        clan_tag=normalized_clan_tag,
        notification_type=notification_type,
        command=command,
    )


@router.get("/{route_id}/status", response_model=NotificationRouteStatusResponse)
async def get_admin_notification_route_status(
    route_id: int,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    dependencies: Annotated[
        AdminNotificationRouteDependencies,
        Depends(get_admin_notification_route_dependencies),
    ],
) -> NotificationRouteStatusResponse:
    """Возвращает статус notification route по route ID.

    Args:
        route_id: DB ID маршрута.
        _context: Admin web context.
        dependencies: Сервисы admin notification routes.

    Returns:
        JSON response со статусом маршрута.
    """
    try:
        route = await dependencies.route_service.get_route_by_id(route_id=route_id)
    except Exception as exc:
        _raise_admin_notification_route_error(exc)

    return NotificationRouteStatusResponse(route=NotificationRouteResponse.from_model(route))


@router.post("/{route_id}/disable", response_model=NotificationRouteDisableResponse)
async def disable_admin_notification_route(
    route_id: int,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    dependencies: Annotated[
        AdminNotificationRouteDependencies,
        Depends(get_admin_notification_route_dependencies),
    ],
) -> NotificationRouteDisableResponse:
    """Отключает notification route без удаления истории.

    Args:
        route_id: DB ID маршрута.
        _context: Admin web context.
        dependencies: Сервисы admin notification routes.

    Returns:
        JSON response с результатом отключения.
    """
    try:
        result = await dependencies.route_service.disable_route(route_id=route_id)
    except Exception as exc:
        _raise_admin_notification_route_error(exc)

    return NotificationRouteDisableResponse(
        changed=result.changed,
        route=NotificationRouteResponse.from_model(result.route),
    )


@router.post("/{route_id}/test", response_model=NotificationRouteTestResponse)
async def test_admin_notification_route(
    route_id: int,
    _context: Annotated[WebRequestContext, Depends(require_admin_request)],
    dependencies: Annotated[
        AdminNotificationRouteDependencies,
        Depends(get_admin_notification_route_dependencies),
    ],
) -> NotificationRouteTestResponse:
    """Отправляет тестовое уведомление в Telegram route и пишет log.

    Args:
        route_id: DB ID маршрута.
        _context: Admin web context.
        dependencies: Сервисы admin notification routes.

    Returns:
        JSON response с Telegram message id.
    """
    try:
        route = await dependencies.route_service.get_route_by_id(route_id=route_id)
        _ensure_route_enabled(route)
        notification = _build_test_notification(route)
        send_result = await dependencies.sender.send(route=route, notification=notification)
    except Exception as exc:
        await _record_failed_test_notification(
            dependencies=dependencies,
            route_id=route_id,
            error=exc,
        )
        _raise_admin_notification_route_error(exc)

    await dependencies.log_service.record_sent(
        route=route,
        notification_type=notification.notification_type,
        event_key=None,
        telegram_message_id=send_result.message_id,
        payload_summary=notification.payload_summary,
    )

    return NotificationRouteTestResponse(
        route=NotificationRouteResponse.from_model(route),
        telegram_message_id=send_result.message_id,
    )


async def _record_failed_test_notification(
    *,
    dependencies: AdminNotificationRouteDependencies,
    route_id: int,
    error: Exception,
) -> None:
    """Пишет failed log для test notification, если route можно найти.

    Args:
        dependencies: Сервисы admin notification routes.
        route_id: DB ID маршрута.
        error: Ошибка отправки.
    """
    try:
        route = await dependencies.route_service.get_route_by_id(route_id=route_id)
    except Exception:
        return

    notification = _build_test_notification(route)
    await dependencies.log_service.record_failed(
        route=route,
        notification_type=notification.notification_type,
        event_key=None,
        payload_summary=notification.payload_summary,
        error_text=f"{type(error).__name__}: {error}",
    )


def _ensure_route_enabled(route: NotificationRoute) -> None:
    """Проверяет, что route включён перед test send.

    Args:
        route: Маршрут уведомлений.

    Raises:
        HTTPException: Если route disabled.
    """
    if not route.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "notification_route_disabled",
                "message": "Отключённый маршрут нельзя тестировать.",
            },
        )


def _build_test_notification(route: NotificationRoute) -> RenderedNotification:
    """Создаёт тестовое уведомление для route.

    Args:
        route: Маршрут уведомлений.

    Returns:
        Plain text notification.
    """
    notification_type = NotificationType(route.notification_type)
    clan = route.__dict__.get("clan")
    clan_name = _optional_attr(clan, "name") or "неизвестный клан"

    return RenderedNotification(
        notification_type=notification_type,
        text="\n".join(
            (
                "Тестовое уведомление BestiaryNavigator_bot",
                f"Клан: {clan_name}",
                f"Тип: {notification_type.value}",
            )
        ),
        payload_summary=f"Test notification route {route.id or 'unsaved'}",
    )


def _raise_admin_notification_route_error(error: Exception) -> NoReturn:
    """Преобразует ошибки notification route слоя в HTTPException.

    Args:
        error: Исключение нижнего слоя.

    Raises:
        HTTPException: Понятная HTTP-ошибка admin route.
    """
    if isinstance(error, HTTPException):
        raise error

    if isinstance(error, NotificationRouteNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "notification_route_not_found",
                "message": str(error),
            },
        ) from error

    if isinstance(error, TelegramNotificationSenderError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "notification_test_send_failed",
                "message": str(error),
            },
        ) from error

    if isinstance(error, DomainValidationError | NotificationRouteServiceError | ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_notification_route_payload",
                "message": str(error),
            },
        ) from error

    raise error


def _build_register_command(*, clan_tag: str, notification_type: str) -> str:
    """Собирает команду регистрации Telegram route.

    Args:
        clan_tag: Нормализованный тег клана.
        notification_type: Тип уведомления.

    Returns:
        Команда `/register`.
    """
    normalized_clan_tag = normalize_clan_tag(clan_tag)
    normalized_notification_type = require_domain_enum_value(
        NotificationType,
        notification_type,
        field_name="notification_type",
    )

    return f"/register {normalized_clan_tag} {normalized_notification_type.value}"


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


def _optional_attr(model: object | None, attribute_name: str) -> str | None:
    """Безопасно достаёт опциональный строковый атрибут модели.

    Args:
        model: Объект модели или `None`.
        attribute_name: Имя атрибута.

    Returns:
        Непустая строка или `None`.
    """
    if model is None:
        return None

    value = getattr(model, attribute_name, None)
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    return normalized or None


def _optional_clan_type(value: str | None) -> ClanType | None:
    """Нормализует опциональный тип клана.

    Args:
        value: Строковый тип клана.

    Returns:
        Enum `ClanType` или `None`.
    """
    if value is None:
        return None

    return ClanType(value)


__all__ = [
    "AdminNotificationLogService",
    "AdminNotificationRouteDependencies",
    "AdminNotificationRouteService",
    "NotificationRegisterCommandResponse",
    "NotificationRouteDisableResponse",
    "NotificationRouteListResponse",
    "NotificationRouteResponse",
    "NotificationRouteStatusResponse",
    "NotificationRouteTestResponse",
    "get_admin_notification_route_dependencies",
    "get_admin_notification_route_settings",
    "router",
]

```


## FILE: app/web/admin_api_errors.py

```python
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

```


## FILE: app/web/admin_api_error_settings.py

```python
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

```


## FILE: app/db/base.py

```python
"""Базовые классы и mixins для SQLAlchemy models."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Единый declarative base для всех SQLAlchemy models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IdMixin:
    """Mixin с техническим первичным ключом."""

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )


class TimestampMixin:
    """Mixin с timezone-aware timestamp-полями."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BaseModelMixin(IdMixin, TimestampMixin):
    """Базовый mixin для моделей с id и timestamp-полями."""

```


## FILE: app/db/session.py

```python
"""Async SQLAlchemy engine, session factory и transaction helpers."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import get_settings

type DatabaseOperation[T] = Callable[[AsyncSession], Awaitable[T]]


def create_db_engine(
    database_url: str,
    *,
    echo: bool = False,
    pool_pre_ping: bool = True,
) -> AsyncEngine:
    """Создаёт async SQLAlchemy engine.

    Args:
        database_url: SQLAlchemy URL подключения к PostgreSQL через asyncpg.
        echo: Флаг SQLAlchemy echo для отладочного вывода SQL.
        pool_pre_ping: Флаг проверки соединения перед выдачей из пула.

    Returns:
        Async SQLAlchemy engine.
    """
    return create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=pool_pre_ping,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Создаёт единую async session factory.

    Args:
        engine: Async SQLAlchemy engine.

    Returns:
        Фабрика `AsyncSession`.
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        expire_on_commit=False,
    )


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Возвращает кешированный async engine приложения.

    Returns:
        Async SQLAlchemy engine, созданный из `DATABASE_URL`.
    """
    settings = get_settings()
    return create_db_engine(settings.database_url)


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Возвращает кешированную session factory приложения.

    Returns:
        Единая фабрика `AsyncSession`.
    """
    return create_session_factory(get_engine())


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency для получения DB session.

    Yields:
        Async SQLAlchemy session.
    """
    session_factory = get_session_factory()

    async with session_factory() as session:
        yield session


@asynccontextmanager
async def transactional_session(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """Открывает session и транзакцию в одном context manager.

    Args:
        session_factory: Явная фабрика session для тестов или специальных сценариев.
            Если не передана, используется глобальная factory приложения.

    Yields:
        Async SQLAlchemy session внутри транзакции.
    """
    factory = session_factory or get_session_factory()

    async with factory() as session, session.begin():
        yield session


async def run_in_transaction[T](
    operation: DatabaseOperation[T],
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> T:
    """Выполняет async-операцию внутри DB-транзакции.

    Args:
        operation: Async callable, принимающий `AsyncSession`.
        session_factory: Явная фабрика session для тестов или специальных сценариев.

    Returns:
        Результат выполнения `operation`.
    """
    async with transactional_session(session_factory) as session:
        return await operation(session)

```


## FILE: app/db/models/__init__.py

```python
"""SQLAlchemy models приложения."""

from app.db.models.api_errors import ApiError
from app.db.models.clans import Clan
from app.db.models.cwl import CwlSeason, CwlWar
from app.db.models.events import PlayerEvent
from app.db.models.notifications import NotificationLog, NotificationRoute
from app.db.models.players import ClanMemberSnapshot, PlayerAccount, PlayerProfileSnapshot
from app.db.models.raids import RaidMember, RaidSeason
from app.db.models.settings import AppSetting
from app.db.models.telegram import TelegramChat
from app.db.models.users import TelegramUser
from app.db.models.war import WarAttack, WarMember, WarSnapshot
from app.db.models.warnings import KickCandidate, Warning

__all__ = [
    "ApiError",
    "AppSetting",
    "Clan",
    "ClanMemberSnapshot",
    "CwlSeason",
    "CwlWar",
    "KickCandidate",
    "NotificationLog",
    "NotificationRoute",
    "PlayerAccount",
    "PlayerEvent",
    "PlayerProfileSnapshot",
    "RaidMember",
    "RaidSeason",
    "TelegramChat",
    "TelegramUser",
    "WarAttack",
    "WarMember",
    "WarSnapshot",
    "Warning",
]

```


## FILE: app/db/models/clans.py

```python
"""Модели отслеживаемых кланов."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, BaseModelMixin

if TYPE_CHECKING:
    from app.db.models.players import ClanMemberSnapshot, PlayerAccount


class Clan(BaseModelMixin, Base):
    """Отслеживаемый клан Clash of Clans."""

    __tablename__ = "clans"

    tag: Mapped[str] = mapped_column(
        String(32),
        unique=True,
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    badge_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        server_default=true(),
        default=True,
        nullable=False,
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    sync_status: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    player_accounts: Mapped[list["PlayerAccount"]] = relationship(
        "PlayerAccount",
        back_populates="last_seen_clan",
    )
    member_snapshots: Mapped[list["ClanMemberSnapshot"]] = relationship(
        "ClanMemberSnapshot",
        back_populates="clan",
    )

```


## FILE: app/db/models/users.py

```python
"""Модели Telegram-пользователей."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, String, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, BaseModelMixin

if TYPE_CHECKING:
    from app.db.models.players import PlayerAccount


class TelegramUser(BaseModelMixin, Base):
    """Telegram-пользователь системы.

    Пользователь может иметь несколько подтверждённых игровых аккаунтов.
    Админский статус кешируется из настроек, но источником истины остаётся
    `TELEGRAM_ADMIN_ID` из runtime-конфигурации.
    """

    __tablename__ = "telegram_users"

    telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
    )
    username: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    display_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    is_admin_cached: Mapped[bool] = mapped_column(
        Boolean,
        server_default=false(),
        default=False,
        nullable=False,
    )

    accounts: Mapped[list["PlayerAccount"]] = relationship(
        "PlayerAccount",
        back_populates="telegram_user",
    )

```


## FILE: app/db/models/players.py

```python
"""Модели игровых аккаунтов и snapshot-данных игроков."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    false,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, BaseModelMixin, IdMixin

if TYPE_CHECKING:
    from app.db.models.clans import Clan
    from app.db.models.users import TelegramUser


class PlayerAccount(BaseModelMixin, Base):
    """Подтверждённый игровой аккаунт после успешного verifytoken."""

    __tablename__ = "player_accounts"

    telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    player_tag: Mapped[str] = mapped_column(
        String(32),
        unique=True,
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        server_default=true(),
        default=True,
        nullable=False,
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    unlinked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_seen_clan_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("clans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    telegram_user: Mapped["TelegramUser | None"] = relationship(
        "TelegramUser",
        back_populates="accounts",
    )
    last_seen_clan: Mapped["Clan | None"] = relationship(
        "Clan",
        back_populates="player_accounts",
    )


class ClanMemberSnapshot(IdMixin, Base):
    """Snapshot участника клана, полученного из Clash API."""

    __tablename__ = "clan_member_snapshots"
    __table_args__ = (
        Index(
            "ix_clan_member_snapshots_clan_id_player_tag_snapshot_at",
            "clan_id",
            "player_tag",
            "snapshot_at",
        ),
    )

    clan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("clans.id", ondelete="CASCADE"),
        nullable=False,
    )
    player_tag: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    role: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    town_hall_level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    exp_level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    trophies: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    donations: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    donations_received: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    war_preference: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        server_default=false(),
        default=False,
        nullable=False,
    )

    clan: Mapped["Clan"] = relationship(
        "Clan",
        back_populates="member_snapshots",
    )


class PlayerProfileSnapshot(IdMixin, Base):
    """Snapshot профиля игрока, полученного из Clash API."""

    __tablename__ = "player_profile_snapshots"
    __table_args__ = (
        Index(
            "ix_player_profile_snapshots_player_tag_snapshot_at",
            "player_tag",
            "snapshot_at",
        ),
    )

    player_tag: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    town_hall_level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    town_hall_weapon_level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    exp_level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    trophies: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    best_trophies: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    war_stars: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    donations: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    donations_received: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    clan_capital_contributions: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    heroes_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )
    troops_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )
    spells_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )
    achievements_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

```


## FILE: app/db/models/war.py

```python
"""Модели обычных клановых войн."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, BaseModelMixin, IdMixin

if TYPE_CHECKING:
    from app.db.models.clans import Clan


class WarSnapshot(BaseModelMixin, Base):
    """Snapshot текущей или последней известной войны клана."""

    __tablename__ = "war_snapshots"

    clan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("clans.id"),
        nullable=False,
        index=True,
    )
    war_tag: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    war_event_key: Mapped[str] = mapped_column(
        String(160),
        unique=True,
        nullable=False,
        index=True,
    )
    state: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    team_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    attacks_per_member: Mapped[int] = mapped_column(
        Integer,
        default=2,
        server_default=text("2"),
        nullable=False,
    )
    preparation_start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    end_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    opponent_tag: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    opponent_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    our_stars: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    opponent_stars: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    our_destruction: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("0"),
        server_default=text("0"),
        nullable=False,
    )
    opponent_destruction: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("0"),
        server_default=text("0"),
        nullable=False,
    )
    our_attacks: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    opponent_attacks: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    clan: Mapped["Clan"] = relationship("Clan")


class WarMember(IdMixin, Base):
    """Участник войны из snapshot-данных Clash API."""

    __tablename__ = "war_members"

    war_snapshot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("war_snapshots.id"),
        nullable=False,
        index=True,
    )
    side: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    player_tag: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    town_hall_level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    map_position: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    attacks_done: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    attacks_left: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )

    war_snapshot: Mapped[WarSnapshot] = relationship("WarSnapshot")


class WarAttack(IdMixin, Base):
    """Атака в обычной клановой войне."""

    __tablename__ = "war_attacks"

    war_snapshot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("war_snapshots.id"),
        nullable=False,
        index=True,
    )
    attacker_tag: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    defender_tag: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    stars: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    destruction_percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("0"),
        server_default=text("0"),
        nullable=False,
    )
    duration: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    war_snapshot: Mapped[WarSnapshot] = relationship("WarSnapshot")

```


## FILE: app/db/models/raids.py

```python
"""Модели рейдов столицы клана."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin


class RaidSeason(IdMixin, Base):
    """Snapshot рейдового сезона столицы клана."""

    __tablename__ = "raid_seasons"

    clan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("clans.id"),
        nullable=False,
        index=True,
    )
    state: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    end_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    capital_total_loot: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    raids_completed: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    total_attacks: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    enemy_districts_destroyed: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    offensive_reward: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    defensive_reward: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class RaidMember(IdMixin, Base):
    """Участник рейдового сезона столицы клана."""

    __tablename__ = "raid_members"

    raid_season_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("raid_seasons.id"),
        nullable=False,
        index=True,
    )
    player_tag: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    attacks: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    project_expected_attacks: Mapped[int] = mapped_column(
        Integer,
        default=6,
        server_default=text("6"),
        nullable=False,
    )
    capital_resources_looted: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    raid_season: Mapped[RaidSeason] = relationship("RaidSeason")

```


## FILE: app/db/models/cwl.py

```python
"""Модели Лиги войн кланов."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, BaseModelMixin, IdMixin

if TYPE_CHECKING:
    from app.db.models.clans import Clan


class CwlSeason(BaseModelMixin, Base):
    """Сезон Лиги войн кланов для отслеживаемого клана."""

    __tablename__ = "cwl_seasons"

    clan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("clans.id"),
        nullable=False,
        index=True,
    )
    season: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    state: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    clan: Mapped["Clan"] = relationship("Clan")


class CwlWar(IdMixin, Base):
    """Конкретная война внутри раунда ЛВК."""

    __tablename__ = "cwl_wars"

    cwl_season_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cwl_seasons.id"),
        nullable=False,
        index=True,
    )
    round_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    war_tag: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        index=True,
    )
    state: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    our_clan_tag: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    opponent_clan_tag: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    end_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    our_stars: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    opponent_stars: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    our_destruction: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("0"),
        server_default=text("0"),
        nullable=False,
    )
    opponent_destruction: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("0"),
        server_default=text("0"),
        nullable=False,
    )

    cwl_season: Mapped[CwlSeason] = relationship("CwlSeason")

```


## FILE: app/db/models/warnings.py

```python
"""Модели warn и кандидатов на кик."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, BaseModelMixin

if TYPE_CHECKING:
    from app.db.models.clans import Clan
    from app.db.models.cwl import CwlSeason
    from app.db.models.users import TelegramUser


class Warning(BaseModelMixin, Base):
    """Warn, привязанный к Telegram-пользователю.

    Warn не привязывается к отдельному игровому аккаунту. Если у пользователя
    несколько игровых аккаунтов, запись warn остаётся одной, а затронутые
    аккаунты сохраняются в JSONB-полях.
    """

    __tablename__ = "warnings"

    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    reason_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    is_impactful: Mapped[bool] = mapped_column(
        Boolean,
        server_default=false(),
        default=False,
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    author_telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id"),
        nullable=True,
        index=True,
    )
    clan_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("clans.id"),
        nullable=True,
        index=True,
    )
    event_key: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
    )
    affected_player_tags_json: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )
    affected_player_names_json: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )
    created_cwl_season_key: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    active_until_cwl_season_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("cwl_seasons.id"),
        nullable=True,
        index=True,
    )
    expired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancelled_by_telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id"),
        nullable=True,
        index=True,
    )
    cancelled_reason: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )

    telegram_user: Mapped["TelegramUser"] = relationship(
        "TelegramUser",
        foreign_keys=[telegram_user_id],
    )
    author: Mapped["TelegramUser | None"] = relationship(
        "TelegramUser",
        foreign_keys=[author_telegram_user_id],
    )
    cancelled_by: Mapped["TelegramUser | None"] = relationship(
        "TelegramUser",
        foreign_keys=[cancelled_by_telegram_user_id],
    )
    clan: Mapped["Clan | None"] = relationship("Clan")
    active_until_cwl_season: Mapped["CwlSeason | None"] = relationship("CwlSeason")


class KickCandidate(BaseModelMixin, Base):
    """Кандидат на кик или удаление из Telegram-чата."""

    __tablename__ = "kick_candidates"

    telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id"),
        nullable=True,
        index=True,
    )
    player_tag: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        index=True,
    )
    reason_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    event_key: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
    )
    created_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id"),
        nullable=True,
        index=True,
    )
    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    decision_by_telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id"),
        nullable=True,
        index=True,
    )
    decision_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    decision_comment: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    telegram_user: Mapped["TelegramUser | None"] = relationship(
        "TelegramUser",
        foreign_keys=[telegram_user_id],
    )
    created_by_user: Mapped["TelegramUser | None"] = relationship(
        "TelegramUser",
        foreign_keys=[created_by],
    )
    decision_by_user: Mapped["TelegramUser | None"] = relationship(
        "TelegramUser",
        foreign_keys=[decision_by_telegram_user_id],
    )

```


## FILE: app/db/models/notifications.py

```python
"""Модели маршрутов и логов Telegram-уведомлений."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, BaseModelMixin, IdMixin

if TYPE_CHECKING:
    from app.db.models.clans import Clan
    from app.db.models.telegram import TelegramChat
    from app.db.models.users import TelegramUser


class NotificationRoute(BaseModelMixin, Base):
    """Маршрут уведомлений `клан + тип уведомления -> Telegram chat/topic`."""

    __tablename__ = "notification_routes"
    __table_args__ = (
        CheckConstraint(
            "message_thread_id IS NULL OR message_thread_id > 0",
            name="message_thread_id_positive",
        ),
    )

    clan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("clans.id"),
        nullable=False,
        index=True,
    )
    notification_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.chat_id"),
        nullable=False,
        index=True,
    )
    message_thread_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        server_default=true(),
        default=True,
        nullable=False,
    )
    created_by_telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id"),
        nullable=True,
        index=True,
    )

    clan: Mapped["Clan"] = relationship("Clan")
    chat: Mapped["TelegramChat"] = relationship("TelegramChat")
    created_by_user: Mapped["TelegramUser | None"] = relationship("TelegramUser")


Index(
    "uq_notification_routes_clan_notification_chat_thread",
    NotificationRoute.clan_id,
    NotificationRoute.notification_type,
    NotificationRoute.chat_id,
    func.coalesce(NotificationRoute.message_thread_id, 0),
    unique=True,
)


class NotificationLog(IdMixin, Base):
    """Лог попытки отправки Telegram-уведомления."""

    __tablename__ = "notification_logs"

    route_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("notification_routes.id"),
        nullable=True,
        index=True,
    )
    notification_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    event_key: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.chat_id"),
        nullable=False,
        index=True,
    )
    message_thread_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    telegram_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    payload_summary: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )
    error_text: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    route: Mapped["NotificationRoute | None"] = relationship("NotificationRoute")
    chat: Mapped["TelegramChat"] = relationship("TelegramChat")

```


## FILE: app/db/models/events.py

```python
"""Модели истории событий игрока."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin

if TYPE_CHECKING:
    from app.db.models.users import TelegramUser


class PlayerEvent(IdMixin, Base):
    """Историческое событие игрока.

    Это не audit log. Модель хранит события, которые нужны для карточки игрока
    и принятия игровых решений.
    """

    __tablename__ = "player_events"

    telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id"),
        nullable=True,
        index=True,
    )
    player_tag: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    telegram_user: Mapped["TelegramUser | None"] = relationship("TelegramUser")

```


## FILE: app/db/models/api_errors.py

```python
"""Модель ошибок внешних API."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin


class ApiError(IdMixin, Base):
    """Краткий debug-контекст ошибки внешнего API."""

    __tablename__ = "api_errors"

    endpoint: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        index=True,
    )
    method: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    entity_type: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    entity_tag: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    status_code: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    message: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )
    response_snippet: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    exception_class: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    worker_name: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

```


## FILE: app/domain/enums.py

```python
"""Доменные enum и константы проекта."""

from enum import StrEnum

from app.domain.exceptions import DomainValidationError


class DomainStrEnum(StrEnum):
    """Базовый строковый enum с helper для набора значений."""

    @classmethod
    def values(cls) -> frozenset[str]:
        """Возвращает набор строковых значений enum.

        Returns:
            Набор строковых значений всех enum members.
        """
        return frozenset(member.value for member in cls)


def require_domain_enum_value[DomainEnumT: DomainStrEnum](
    enum_type: type[DomainEnumT],
    value: str,
    *,
    field_name: str,
) -> DomainEnumT:
    """Проверяет строковое значение доменного enum.

    Helper нужен для service/worker/web/bot-слоёв, где значения приходят
    строками из форм, API payload или callback data. Модели БД могут хранить
    строки, но бизнес-логика должна валидировать их через доменные enum.

    Args:
        enum_type: Класс enum, наследующийся от `DomainStrEnum`.
        value: Проверяемое строковое значение.
        field_name: Имя поля для понятного текста ошибки.

    Returns:
        Enum member, соответствующий строковому значению.

    Raises:
        DomainValidationError: Если значение пустое, не строковое или не входит
            в набор допустимых enum values.
    """
    if not issubclass(enum_type, DomainStrEnum):
        raise DomainValidationError("enum_type должен быть подклассом DomainStrEnum.")

    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} должен быть строкой.")

    normalized_value = value.strip()
    if not normalized_value:
        raise DomainValidationError(f"{field_name} не может быть пустым.")

    try:
        return enum_type(normalized_value)
    except ValueError as exc:
        allowed_values = ", ".join(sorted(enum_type.values()))
        raise DomainValidationError(
            f"{field_name} должен быть одним из: {allowed_values}."
        ) from exc


class ClanType(DomainStrEnum):
    """Тип отслеживаемого клана."""

    MAIN = "main"
    ACADEMY = "academy"
    FREEZER = "freezer"


class WarningSource(DomainStrEnum):
    """Источник warn."""

    SYSTEM = "system"
    MANUAL = "manual"


class WarningStatus(DomainStrEnum):
    """Статус warn."""

    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class WarningImpact(DomainStrEnum):
    """Влияние warn на автоматические решения."""

    IMPACTFUL = "impactful"
    NON_IMPACTFUL = "non_impactful"


class KickCandidateStatus(DomainStrEnum):
    """Статус кандидата на кик."""

    PENDING_ADMIN_DECISION = "pending_admin_decision"
    APPROVED = "approved"
    REJECTED = "rejected"
    POSTPONED = "postponed"
    EXECUTED = "executed"
    MANUAL_REQUIRED = "manual_required"


class RaidMemberStatus(DomainStrEnum):
    """Статус участника рейдового сезона."""

    RAID_FULL = "raid_full"
    RAID_INCOMPLETE = "raid_incomplete"
    RAID_MISSED = "raid_missed"


class NotificationType(DomainStrEnum):
    """Тип Telegram-уведомления."""

    WAR_PREPARATION_STARTED = "war_preparation_started"
    WAR_STARTED = "war_started"
    WAR_6H_REMINDER = "war_6h_reminder"
    WAR_12H_REMINDER = "war_12h_reminder"
    WAR_3H_LEFT = "war_3h_left"
    WAR_1H_LEFT = "war_1h_left"
    WAR_ENDED = "war_ended"

    RAID_STARTED = "raid_started"
    RAID_LAUNCHED = "raid_launched"
    RAID_12H_REPORT = "raid_12h_report"

    CWL_STARTED = "cwl_started"
    CWL_ROUND_REPORT = "cwl_round_report"

    WARN_CREATED = "warn_created"
    WARN_CANCELLED = "warn_cancelled"

    KICK_CANDIDATES_EVENING = "kick_candidates_evening"
    UNLINKED_ACCOUNTS_EVENING = "unlinked_accounts_evening"
    API_ERRORS_ADMIN = "api_errors_admin"
    DAILY_ADMIN_REPORT = "daily_admin_report"


class WarningReasonCode(DomainStrEnum):
    """Код причины warn."""

    WAR_ATTACK_MISSED = "war_attack_missed"
    WAR_BAD_ATTACK = "war_bad_attack"
    WAR_WRONG_TARGET = "war_wrong_target"
    WAR_PLAN_IGNORED = "war_plan_ignored"

    CWL_ATTACK_MISSED = "cwl_attack_missed"
    CWL_WRONG_TARGET = "cwl_wrong_target"
    CWL_REMOVED = "cwl_removed"

    RAID_MISSED = "raid_missed"
    RAID_INCOMPLETE = "raid_incomplete"
    RAID_BAD_PLAY = "raid_bad_play"

    TOXICITY = "toxicity"
    SPAM = "spam"
    ADS = "ads"
    POLITICS = "politics"
    OFFICER_INFO_LEAK = "officer_info_leak"
    SABOTAGE = "sabotage"
    OTHER = "other"


class KickCandidateReasonCode(DomainStrEnum):
    """Код причины кандидата на кик."""

    TWO_IMPACTFUL_WARN = "2_impactful_warn"
    UNLINKED_AFTER_3_DAYS = "unlinked_after_3_days"
    ALL_ACCOUNTS_LEFT = "all_accounts_left"
    LINKED_ACCOUNT_LEFT = "linked_account_left"
    MANUAL_RECOMMENDATION = "manual_recommendation"


SYSTEM_WARNING_REASON_CODES = frozenset(
    {
        WarningReasonCode.WAR_ATTACK_MISSED,
        WarningReasonCode.CWL_ATTACK_MISSED,
        WarningReasonCode.RAID_MISSED,
        WarningReasonCode.RAID_INCOMPLETE,
    }
)
"""Причины warn, которые создаются системой."""

IMPACTFUL_WARNING_REASON_CODES = SYSTEM_WARNING_REASON_CODES
"""Причины warn, влияющие на автоматические решения."""

MANUAL_WARNING_REASON_CODES = frozenset(
    reason_code
    for reason_code in WarningReasonCode
    if reason_code not in SYSTEM_WARNING_REASON_CODES
)
"""Причины warn, которые могут выбираться вручную."""

__all__ = [
    "IMPACTFUL_WARNING_REASON_CODES",
    "MANUAL_WARNING_REASON_CODES",
    "SYSTEM_WARNING_REASON_CODES",
    "ClanType",
    "DomainStrEnum",
    "KickCandidateReasonCode",
    "KickCandidateStatus",
    "NotificationType",
    "RaidMemberStatus",
    "WarningImpact",
    "WarningReasonCode",
    "WarningSource",
    "WarningStatus",
    "require_domain_enum_value",
]

```


## FILE: app/domain/event_keys.py

```python
"""Helpers для построения идемпотентных event keys."""

from datetime import UTC, date, datetime
from hashlib import sha1
from typing import Final

from app.domain.enums import KickCandidateReasonCode, NotificationType, WarningReasonCode
from app.domain.exceptions import EventKeyValidationError
from app.domain.tags import normalize_clan_tag, normalize_player_tag

_WARN_PREFIX: Final[str] = "warn"
_AUTO_WARN_SOURCE: Final[str] = "auto"
_KICK_PREFIX: Final[str] = "kick"
_NOTIFY_PREFIX: Final[str] = "notify"


def build_war_event_key(
    *,
    clan_tag: str,
    opponent_tag: str,
    preparation_start_time: datetime,
    start_time: datetime,
    end_time: datetime,
    team_size: int,
) -> str:
    """Строит стабильный hash-key обычной войны.

    Args:
        clan_tag: Тег нашего клана.
        opponent_tag: Тег клана соперника.
        preparation_start_time: Время начала подготовки.
        start_time: Время начала войны.
        end_time: Время окончания войны.
        team_size: Размер войны.

    Returns:
        SHA1 hash, построенный из нормализованных полей войны.

    Raises:
        EventKeyValidationError: Если время не timezone-aware или размер войны некорректен.
    """
    normalized_clan_tag = normalize_clan_tag(clan_tag)
    normalized_opponent_tag = normalize_clan_tag(opponent_tag)
    normalized_team_size = _validate_positive_int(team_size, field_name="team_size")

    payload = ":".join(
        (
            normalized_clan_tag,
            normalized_opponent_tag,
            _format_aware_datetime(
                preparation_start_time,
                field_name="preparation_start_time",
            ),
            _format_aware_datetime(start_time, field_name="start_time"),
            _format_aware_datetime(end_time, field_name="end_time"),
            str(normalized_team_size),
        )
    )

    return sha1(payload.encode("utf-8")).hexdigest()


def build_war_warning_event_key(
    *,
    clan_tag: str,
    war_event_key: str,
    telegram_user_id: int,
) -> str:
    """Строит event key автоматического warn за пропуск атаки КВ.

    Args:
        clan_tag: Тег клана.
        war_event_key: Стабильный key войны.
        telegram_user_id: ID TelegramUser в БД.

    Returns:
        Event key для дедупликации warn.
    """
    return _join_event_key_parts(
        _WARN_PREFIX,
        _AUTO_WARN_SOURCE,
        WarningReasonCode.WAR_ATTACK_MISSED.value,
        normalize_clan_tag(clan_tag),
        _normalize_non_empty_string(war_event_key, field_name="war_event_key"),
        _validate_positive_int(telegram_user_id, field_name="telegram_user_id"),
    )


def build_cwl_warning_event_key(
    *,
    clan_tag: str,
    cwl_war_tag: str,
    telegram_user_id: int,
) -> str:
    """Строит event key автоматического warn за пропуск атаки ЛВК.

    Args:
        clan_tag: Тег клана.
        cwl_war_tag: Реальный war tag из CWL API.
        telegram_user_id: ID TelegramUser в БД.

    Returns:
        Event key для дедупликации warn.
    """
    return _join_event_key_parts(
        _WARN_PREFIX,
        _AUTO_WARN_SOURCE,
        WarningReasonCode.CWL_ATTACK_MISSED.value,
        normalize_clan_tag(clan_tag),
        normalize_clan_tag(cwl_war_tag),
        _validate_positive_int(telegram_user_id, field_name="telegram_user_id"),
    )


def build_raid_warning_event_key(
    *,
    reason_code: WarningReasonCode,
    clan_tag: str,
    raid_start_time: datetime,
    telegram_user_id: int,
) -> str:
    """Строит event key автоматического warn за рейды.

    Args:
        reason_code: Причина `raid_missed` или `raid_incomplete`.
        clan_tag: Тег клана.
        raid_start_time: Время начала рейдового сезона.
        telegram_user_id: ID TelegramUser в БД.

    Returns:
        Event key для дедупликации warn.

    Raises:
        EventKeyValidationError: Если передана причина не из рейдовых автоматических причин.
    """
    if reason_code not in {
        WarningReasonCode.RAID_MISSED,
        WarningReasonCode.RAID_INCOMPLETE,
    }:
        raise EventKeyValidationError(
            "Для рейдового warn допустимы только raid_missed и raid_incomplete."
        )

    return _join_event_key_parts(
        _WARN_PREFIX,
        _AUTO_WARN_SOURCE,
        reason_code.value,
        normalize_clan_tag(clan_tag),
        _format_aware_datetime(raid_start_time, field_name="raid_start_time"),
        _validate_positive_int(telegram_user_id, field_name="telegram_user_id"),
    )


def build_two_impactful_warn_kick_event_key(
    *,
    telegram_user_id: int,
    season_key: str,
) -> str:
    """Строит event key кандидата по двум active impactful warn.

    Args:
        telegram_user_id: ID TelegramUser в БД.
        season_key: Ключ сезона, в рамках которого считается кандидат.

    Returns:
        Event key для дедупликации кандидата на кик.
    """
    return _join_event_key_parts(
        _KICK_PREFIX,
        KickCandidateReasonCode.TWO_IMPACTFUL_WARN.value,
        _validate_positive_int(telegram_user_id, field_name="telegram_user_id"),
        _normalize_non_empty_string(season_key, field_name="season_key"),
    )


def build_unlinked_account_kick_event_key(
    *,
    clan_id: int,
    player_tag: str,
) -> str:
    """Строит event key кандидата по непривязанному аккаунту.

    Args:
        clan_id: ID клана в БД.
        player_tag: Тег непривязанного аккаунта.

    Returns:
        Event key для дедупликации кандидата на кик.
    """
    return _join_event_key_parts(
        _KICK_PREFIX,
        KickCandidateReasonCode.UNLINKED_AFTER_3_DAYS.value,
        _validate_positive_int(clan_id, field_name="clan_id"),
        normalize_player_tag(player_tag),
    )


def build_all_accounts_left_kick_event_key(*, telegram_user_id: int) -> str:
    """Строит event key кандидата по уходу всех аккаунтов пользователя.

    Args:
        telegram_user_id: ID TelegramUser в БД.

    Returns:
        Event key для дедупликации кандидата на кик.
    """
    return _join_event_key_parts(
        _KICK_PREFIX,
        KickCandidateReasonCode.ALL_ACCOUNTS_LEFT.value,
        _validate_positive_int(telegram_user_id, field_name="telegram_user_id"),
    )


def build_linked_account_left_kick_event_key(
    *,
    telegram_user_id: int,
    player_tag: str,
) -> str:
    """Строит event key кандидата по уходу одного привязанного аккаунта.

    Args:
        telegram_user_id: ID TelegramUser в БД.
        player_tag: Тег ушедшего аккаунта.

    Returns:
        Event key для дедупликации кандидата на кик.
    """
    return _join_event_key_parts(
        _KICK_PREFIX,
        KickCandidateReasonCode.LINKED_ACCOUNT_LEFT.value,
        _validate_positive_int(telegram_user_id, field_name="telegram_user_id"),
        normalize_player_tag(player_tag),
    )


def build_notification_event_key(notification_type: NotificationType, *parts: object) -> str:
    """Строит event key уведомления.

    Args:
        notification_type: Тип уведомления.
        *parts: Стабильные части события: ID, event key, date или timezone-aware datetime.

    Returns:
        Event key для дедупликации уведомления.

    Raises:
        EventKeyValidationError: Если тип уведомления или части ключа невалидны.
    """
    if not isinstance(notification_type, NotificationType):
        raise EventKeyValidationError("notification_type должен быть NotificationType.")

    if not parts:
        raise EventKeyValidationError("Notification event key должен содержать части события.")

    return _join_event_key_parts(_NOTIFY_PREFIX, notification_type.value, *parts)


def _join_event_key_parts(*parts: object) -> str:
    """Собирает event key из нормализованных частей.

    Args:
        *parts: Части event key.

    Returns:
        Строковый event key.
    """
    return ":".join(_format_event_key_part(part) for part in parts)


def _format_event_key_part(part: object) -> str:
    """Форматирует одну часть event key.

    Args:
        part: Значение части event key.

    Returns:
        Строковое представление части event key.

    Raises:
        EventKeyValidationError: Если тип или значение части невалидны.
    """
    if isinstance(part, datetime):
        return _format_aware_datetime(part, field_name="event_key_part")

    if isinstance(part, date):
        return part.isoformat()

    if isinstance(part, bool):
        raise EventKeyValidationError("Boolean не может быть частью event key.")

    if isinstance(part, int):
        return str(_validate_positive_int(part, field_name="event_key_part"))

    if isinstance(part, str):
        return _normalize_non_empty_string(part, field_name="event_key_part")

    raise EventKeyValidationError(f"Неподдерживаемая часть event key: {type(part).__name__}.")


def _format_aware_datetime(value: datetime, *, field_name: str) -> str:
    """Форматирует timezone-aware datetime в стабильный UTC ISO-format.

    Args:
        value: Datetime-значение.
        field_name: Название поля для текста ошибки.

    Returns:
        UTC ISO-строка с точностью до секунд.

    Raises:
        EventKeyValidationError: Если datetime не содержит timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise EventKeyValidationError(f"{field_name} должен быть timezone-aware datetime.")

    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_positive_int(value: int, *, field_name: str) -> int:
    """Проверяет положительное целое число.

    Args:
        value: Проверяемое значение.
        field_name: Название поля для текста ошибки.

    Returns:
        Проверенное значение.

    Raises:
        EventKeyValidationError: Если значение не является положительным int.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise EventKeyValidationError(f"{field_name} должен быть целым числом.")

    if value <= 0:
        raise EventKeyValidationError(f"{field_name} должен быть положительным числом.")

    return value


def _normalize_non_empty_string(value: str, *, field_name: str) -> str:
    """Проверяет непустую строку.

    Args:
        value: Проверяемое значение.
        field_name: Название поля для текста ошибки.

    Returns:
        Строка без пробелов по краям.

    Raises:
        EventKeyValidationError: Если строка пустая.
    """
    normalized_value = value.strip()

    if not normalized_value:
        raise EventKeyValidationError(f"{field_name} не может быть пустым.")

    return normalized_value

```


## FILE: app/domain/tags.py

```python
"""Утилиты нормализации и кодирования Clash of Clans тегов."""

import re
from urllib.parse import quote

from app.domain.exceptions import TagValidationError

_TAG_ALLOWED_PATTERN = re.compile(r"^#[A-Z0-9]+$")
_URL_ENCODED_PREFIX = "%23"


def normalize_player_tag(value: str) -> str:
    """Нормализует тег игрока для хранения в БД.

    Args:
        value: Пользовательский ввод тега игрока.

    Returns:
        Нормализованный тег в формате `#ABC123`.

    Raises:
        TagValidationError: Если тег пустой, URL-encoded или содержит недопустимые символы.
    """
    return _normalize_tag(value, entity_name="Тег игрока")


def normalize_clan_tag(value: str) -> str:
    """Нормализует тег клана для хранения в БД.

    Args:
        value: Пользовательский ввод тега клана.

    Returns:
        Нормализованный тег в формате `#ABC123`.

    Raises:
        TagValidationError: Если тег пустой, URL-encoded или содержит недопустимые символы.
    """
    return _normalize_tag(value, entity_name="Тег клана")


def encode_tag_for_clash_url(value: str) -> str:
    """Кодирует нормализованный тег для URL Clash API.

    Args:
        value: Тег игрока или клана.

    Returns:
        URL-encoded тег, например `%232ABC`.

    Raises:
        TagValidationError: Если тег нельзя нормализовать.
    """
    normalized_tag = _normalize_tag(value, entity_name="Тег")
    return quote(normalized_tag, safe="")


def _normalize_tag(value: str, *, entity_name: str) -> str:
    """Нормализует Clash-тег по общим правилам проекта.

    Args:
        value: Сырой пользовательский ввод.
        entity_name: Название сущности для текста ошибки.

    Returns:
        Нормализованный тег.

    Raises:
        TagValidationError: Если значение не может быть безопасно сохранено как DB-value.
    """
    if not isinstance(value, str):
        raise TagValidationError(f"{entity_name} должен быть строкой.")

    raw_tag = value.strip()
    if not raw_tag:
        raise TagValidationError(f"{entity_name} не может быть пустым.")

    if raw_tag.lower().startswith(_URL_ENCODED_PREFIX):
        raise TagValidationError(
            f"{entity_name} не должен быть URL-encoded. Передайте тег в формате #ABC123."
        )

    if any(character.isspace() for character in raw_tag):
        raise TagValidationError(f"{entity_name} не должен содержать пробелы.")

    tag_with_prefix = raw_tag if raw_tag.startswith("#") else f"#{raw_tag}"
    normalized_tag = tag_with_prefix.upper()

    if normalized_tag == "#":
        raise TagValidationError(f"{entity_name} должен содержать символы после #.")

    if not _TAG_ALLOWED_PATTERN.fullmatch(normalized_tag):
        raise TagValidationError(
            f"{entity_name} должен содержать только символ # в начале, латинские буквы A-Z и цифры."
        )

    return normalized_tag

```


## FILE: app/domain/telegram.py

```python
"""Доменные helpers для Telegram-значений."""

from app.domain.exceptions import DomainValidationError


def normalize_message_thread_id(value: int | None) -> int | None:
    """Проверяет Telegram message thread id.

    `None` означает обычный чат без topic. Значение `0` запрещено, потому что
    оно используется как sentinel в PostgreSQL expression index для
    дедупликации маршрутов с `NULL message_thread_id`.

    Args:
        value: Telegram topic/thread id или `None`.

    Returns:
        Проверенное значение без преобразования.

    Raises:
        DomainValidationError: Если значение не является положительным int или `None`.
    """
    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainValidationError("message_thread_id должен быть положительным числом или None.")

    if value <= 0:
        raise DomainValidationError("message_thread_id должен быть больше 0 или None.")

    return value

```


## FILE: app/domain/exceptions.py

```python
"""Доменные исключения приложения."""


class DomainValidationError(ValueError):
    """Базовая ошибка доменной валидации."""


class TagValidationError(DomainValidationError):
    """Ошибка нормализации или валидации Clash-тега."""


class EventKeyValidationError(DomainValidationError):
    """Ошибка построения идемпотентного event key."""

```


## FILE: app/services/clans.py

```python
"""Сервис управления отслеживаемыми кланами."""

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Clan
from app.domain import ClanType, normalize_clan_tag, require_domain_enum_value
from app.integrations.clash import ClashClan

_CLAN_SYNC_STATUS_OK = "ok"


class ClanManagementError(RuntimeError):
    """Базовая ошибка сервиса управления кланами."""


class ClanNotFoundError(ClanManagementError):
    """Клан не найден в локальной БД."""


class ClashClanProvider(Protocol):
    """Минимальный contract Clash API client для clan management service."""

    async def get_clan(self, clan_tag: str) -> ClashClan:
        """Получает клан из Clash API.

        Args:
            clan_tag: Тег клана в пользовательском или нормализованном виде.

        Returns:
            DTO клана из Clash API.
        """


class ClanRepository(Protocol):
    """Repository contract для управления моделью `Clan`."""

    async def get_by_tag(self, clan_tag: str) -> Clan | None:
        """Возвращает клан по нормализованному тегу.

        Args:
            clan_tag: Нормализованный тег клана.

        Returns:
            Модель клана или `None`.
        """

    async def list_all(self) -> tuple[Clan, ...]:
        """Возвращает все кланы в стабильном порядке.

        Returns:
            Tuple отслеживаемых кланов.
        """

    def add(self, clan: Clan) -> None:
        """Добавляет клан в unit of work.

        Args:
            clan: Новая модель клана.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyClanRepository:
    """SQLAlchemy-реализация repository для кланов."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_tag(self, clan_tag: str) -> Clan | None:
        """Возвращает клан по нормализованному тегу.

        Args:
            clan_tag: Нормализованный тег клана.

        Returns:
            Модель клана или `None`.
        """
        result = await self._session.execute(select(Clan).where(Clan.tag == clan_tag))
        return result.scalar_one_or_none()

    async def list_all(self) -> tuple[Clan, ...]:
        """Возвращает все кланы в стабильном порядке.

        Returns:
            Tuple отслеживаемых кланов.
        """
        result = await self._session.execute(
            select(Clan).order_by(Clan.is_active.desc(), Clan.name.asc(), Clan.tag.asc())
        )
        return tuple(result.scalars().all())

    def add(self, clan: Clan) -> None:
        """Добавляет клан в текущую session.

        Args:
            clan: Новая модель клана.
        """
        self._session.add(clan)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class ClanManagementService:
    """Сервис управления отслеживаемыми кланами.

    Сервис отвечает за локальные правила управления кланами: проверку через
    Clash API перед сохранением, нормализацию тега, валидацию типа клана,
    обновление данных, реактивацию и отключение мониторинга.
    """

    def __init__(
        self,
        *,
        repository: ClanRepository,
        clash_client: ClashClanProvider,
    ) -> None:
        """Инициализирует service.

        Args:
            repository: Repository для доступа к кланам.
            clash_client: Клиент Clash API или совместимый provider.
        """
        self._repository = repository
        self._clash_client = clash_client

    @classmethod
    def from_session(
        cls,
        *,
        session: AsyncSession,
        clash_client: ClashClanProvider,
    ) -> "ClanManagementService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            clash_client: Клиент Clash API или совместимый provider.

        Returns:
            Настроенный service.
        """
        return cls(
            repository=SqlAlchemyClanRepository(session),
            clash_client=clash_client,
        )

    async def list_clans(self) -> tuple[Clan, ...]:
        """Возвращает список отслеживаемых кланов.

        Метод нужен web/admin слою как read-contract. Он не обращается к Clash
        API и не меняет состояние БД.

        Returns:
            Tuple кланов в стабильном порядке.
        """
        return await self._repository.list_all()

    async def check_clan(self, *, clan_tag: str) -> ClashClan:
        """Проверяет тег клана через Clash API без сохранения в БД.

        Args:
            clan_tag: Тег клана.

        Returns:
            DTO клана из Clash API.

        Raises:
            ClashApiError: Если Clash API вернул ошибку.
            TagValidationError: Если тег нельзя нормализовать внутри клиента.
        """
        return await self._clash_client.get_clan(clan_tag)

    async def add_clan(self, *, clan_tag: str, clan_type: ClanType | str) -> Clan:
        """Добавляет или реактивирует отслеживаемый клан.

        Перед любым сохранением клан проверяется через Clash API. Если клан уже
        существует, сервис обновляет его данные, применяет переданный тип и
        включает мониторинг.

        Args:
            clan_tag: Тег клана.
            clan_type: Тип клана: `main`, `academy` или `freezer`.

        Returns:
            Созданная или обновлённая модель клана.
        """
        normalized_clan_type = _normalize_clan_type(clan_type)
        verified_clan = await self._clash_client.get_clan(clan_tag)
        existing_clan = await self._repository.get_by_tag(verified_clan.tag)
        synced_at = _utc_now()

        if existing_clan is None:
            clan = Clan(
                tag=verified_clan.tag,
                name=verified_clan.name,
                type=normalized_clan_type.value,
                level=verified_clan.level,
                badge_url=verified_clan.badge_url,
                is_active=True,
                last_sync_at=synced_at,
                sync_status=_CLAN_SYNC_STATUS_OK,
            )
            self._repository.add(clan)
        else:
            clan = existing_clan
            _apply_verified_clan_fields(clan, verified_clan, synced_at=synced_at)
            clan.type = normalized_clan_type.value
            clan.is_active = True

        await self._repository.flush()
        return clan

    async def refresh_clan(self, *, clan_tag: str) -> Clan:
        """Обновляет локальные данные клана из Clash API.

        Args:
            clan_tag: Тег клана.

        Returns:
            Обновлённая модель клана.

        Raises:
            ClanNotFoundError: Если клан не найден в локальной БД.
        """
        normalized_clan_tag = normalize_clan_tag(clan_tag)
        clan = await self._get_required_clan(normalized_clan_tag)

        verified_clan = await self._clash_client.get_clan(normalized_clan_tag)
        _apply_verified_clan_fields(clan, verified_clan, synced_at=_utc_now())

        await self._repository.flush()
        return clan

    async def update_clan_type(self, *, clan_tag: str, clan_type: ClanType | str) -> Clan:
        """Меняет тип отслеживаемого клана.

        Args:
            clan_tag: Тег клана.
            clan_type: Новый тип клана.

        Returns:
            Обновлённая модель клана.

        Raises:
            ClanNotFoundError: Если клан не найден в локальной БД.
            DomainValidationError: Если тип клана не входит в `ClanType`.
        """
        normalized_clan_tag = normalize_clan_tag(clan_tag)
        normalized_clan_type = _normalize_clan_type(clan_type)

        clan = await self._get_required_clan(normalized_clan_tag)
        clan.type = normalized_clan_type.value

        await self._repository.flush()
        return clan

    async def deactivate_clan(self, *, clan_tag: str) -> Clan:
        """Отключает мониторинг клана без удаления истории.

        Args:
            clan_tag: Тег клана.

        Returns:
            Обновлённая модель клана.

        Raises:
            ClanNotFoundError: Если клан не найден в локальной БД.
        """
        normalized_clan_tag = normalize_clan_tag(clan_tag)
        clan = await self._get_required_clan(normalized_clan_tag)
        clan.is_active = False

        await self._repository.flush()
        return clan

    async def _get_required_clan(self, clan_tag: str) -> Clan:
        """Возвращает существующий клан или выбрасывает service error.

        Args:
            clan_tag: Нормализованный тег клана.

        Returns:
            Модель клана.

        Raises:
            ClanNotFoundError: Если клан не найден.
        """
        clan = await self._repository.get_by_tag(clan_tag)
        if clan is None:
            raise ClanNotFoundError(f"Клан {clan_tag} не найден.")

        return clan


def _normalize_clan_type(value: ClanType | str) -> ClanType:
    """Валидирует тип клана через доменный enum.

    Args:
        value: Тип клана.

    Returns:
        Enum member `ClanType`.
    """
    return require_domain_enum_value(ClanType, value, field_name="clan_type")


def _apply_verified_clan_fields(
    clan: Clan,
    verified_clan: ClashClan,
    *,
    synced_at: datetime,
) -> None:
    """Обновляет локальные поля клана по данным Clash API.

    Args:
        clan: Локальная модель клана.
        verified_clan: DTO клана из Clash API.
        synced_at: Время успешной проверки/синхронизации.
    """
    clan.name = verified_clan.name
    clan.level = verified_clan.level
    clan.badge_url = verified_clan.badge_url
    clan.last_sync_at = synced_at
    clan.sync_status = _CLAN_SYNC_STATUS_OK


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "ClanManagementError",
    "ClanManagementService",
    "ClanNotFoundError",
    "ClanRepository",
    "ClashClanProvider",
    "SqlAlchemyClanRepository",
]

```


## FILE: app/services/member_lifecycle.py

```python
"""Сервис жизненного цикла участников клана."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Clan, ClanMemberSnapshot, PlayerAccount, PlayerEvent
from app.domain import normalize_player_tag
from app.integrations.clash import ClashClanMember

_MEMBER_JOINED_EVENT_TYPE = "clan_member_joined"
_MEMBER_LEFT_EVENT_TYPE = "clan_member_left"
_MEMBER_MOVED_EVENT_TYPE = "clan_member_moved"
_MEMBER_RENAMED_EVENT_TYPE = "clan_member_renamed"


class MemberLifecycleError(RuntimeError):
    """Базовая ошибка сервиса жизненного цикла участников."""


@dataclass(frozen=True, slots=True)
class MemberLifecycleResult:
    """Результат обработки состава клана."""

    clan: Clan
    created_count: int
    updated_count: int
    left_count: int
    moved_count: int
    current_count: int


class MemberLifecycleRepository(Protocol):
    """Repository contract для обработки состава клана."""

    async def list_current_by_clan(self, clan_id: int) -> list[ClanMemberSnapshot]:
        """Возвращает текущие snapshot-записи состава клана.

        Args:
            clan_id: DB ID клана.

        Returns:
            Список текущих snapshot-записей.
        """

    async def get_current_by_clan_and_player_tag(
        self,
        *,
        clan_id: int,
        player_tag: str,
    ) -> ClanMemberSnapshot | None:
        """Возвращает текущий snapshot игрока в конкретном клане.

        Args:
            clan_id: DB ID клана.
            player_tag: Нормализованный тег игрока.

        Returns:
            Snapshot или `None`.
        """

    async def close_current_snapshots_in_other_clans(
        self,
        *,
        player_tag: str,
        clan_id: int,
        observed_at: datetime,
    ) -> int:
        """Закрывает current snapshots игрока в других кланах.

        Args:
            player_tag: Нормализованный тег игрока.
            clan_id: DB ID актуального клана.
            observed_at: Время наблюдения состава.

        Returns:
            Количество закрытых snapshot-записей.
        """

    async def get_player_account_by_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает подтверждённый аккаунт по тегу.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            `PlayerAccount` или `None`.
        """

    def add_player_event(self, player_event: PlayerEvent) -> None:
        """Добавляет событие игрока в unit of work.

        Args:
            player_event: Новая модель события.
        """

    def add_member_snapshot(self, snapshot: ClanMemberSnapshot) -> None:
        """Добавляет snapshot участника в unit of work.

        Args:
            snapshot: Новый snapshot участника.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyMemberLifecycleRepository:
    """SQLAlchemy-реализация repository для жизненного цикла участников."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_current_by_clan(self, clan_id: int) -> list[ClanMemberSnapshot]:
        """Возвращает текущие snapshot-записи состава клана.

        Args:
            clan_id: DB ID клана.

        Returns:
            Список текущих snapshot-записей.
        """
        result = await self._session.execute(
            select(ClanMemberSnapshot).where(
                ClanMemberSnapshot.clan_id == clan_id,
                ClanMemberSnapshot.is_current.is_(True),
            )
        )
        return list(result.scalars().all())

    async def get_current_by_clan_and_player_tag(
        self,
        *,
        clan_id: int,
        player_tag: str,
    ) -> ClanMemberSnapshot | None:
        """Возвращает текущий snapshot игрока в клане.

        Args:
            clan_id: DB ID клана.
            player_tag: Нормализованный тег игрока.

        Returns:
            Snapshot или `None`.
        """
        result = await self._session.execute(
            select(ClanMemberSnapshot).where(
                ClanMemberSnapshot.clan_id == clan_id,
                ClanMemberSnapshot.player_tag == player_tag,
                ClanMemberSnapshot.is_current.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def close_current_snapshots_in_other_clans(
        self,
        *,
        player_tag: str,
        clan_id: int,
        observed_at: datetime,
    ) -> int:
        """Закрывает current snapshots игрока в других кланах.

        Args:
            player_tag: Нормализованный тег игрока.
            clan_id: DB ID актуального клана.
            observed_at: Время наблюдения состава.

        Returns:
            Количество закрытых snapshot-записей.
        """
        result = await self._session.execute(
            select(ClanMemberSnapshot).where(
                ClanMemberSnapshot.player_tag == player_tag,
                ClanMemberSnapshot.clan_id != clan_id,
                ClanMemberSnapshot.is_current.is_(True),
            )
        )
        snapshots = list(result.scalars().all())

        for snapshot in snapshots:
            _mark_snapshot_not_current(snapshot, observed_at=observed_at)

        return len(snapshots)

    async def get_player_account_by_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает подтверждённый аккаунт по тегу.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            `PlayerAccount` или `None`.
        """
        result = await self._session.execute(
            select(PlayerAccount).where(PlayerAccount.player_tag == player_tag)
        )
        return result.scalar_one_or_none()

    def add_player_event(self, player_event: PlayerEvent) -> None:
        """Добавляет событие игрока в текущую session.

        Args:
            player_event: Новая модель события.
        """
        self._session.add(player_event)

    def add_member_snapshot(self, snapshot: ClanMemberSnapshot) -> None:
        """Добавляет snapshot участника в текущую session.

        Args:
            snapshot: Новый snapshot участника.
        """
        self._session.add(snapshot)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class MemberLifecycleService:
    """Сервис обработки состава клана.

    Сервис поддерживает current-state состава: актуальный участник обновляется
    in-place, пропавший участник закрывается через `is_current=False`, а игрок,
    появившийся в другом клане, закрывается в старых current snapshots.
    """

    def __init__(self, *, repository: MemberLifecycleRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository для состава и связанных аккаунтов.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "MemberLifecycleService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyMemberLifecycleRepository(session))

    async def process_clan_members(
        self,
        *,
        clan: Clan,
        members: list[ClashClanMember],
    ) -> MemberLifecycleResult:
        """Обрабатывает актуальный API-состав клана.

        Args:
            clan: Отслеживаемый клан.
            members: Список участников из Clash API.

        Returns:
            Счётчики изменений состава.

        Raises:
            MemberLifecycleError: Если клан ещё не сохранён в БД.
        """
        clan_id = _required_model_id(clan, model_name="Clan")
        observed_at = _utc_now()
        incoming_members = _normalize_members(members)

        created_count = 0
        updated_count = 0
        left_count = 0
        moved_count = 0

        current_snapshots = await self._repository.list_current_by_clan(clan_id)
        incoming_tags = set(incoming_members)

        for snapshot in current_snapshots:
            if snapshot.player_tag not in incoming_tags:
                _mark_snapshot_not_current(snapshot, observed_at=observed_at)
                account = await self._repository.get_player_account_by_tag(snapshot.player_tag)
                self._repository.add_player_event(
                    _build_member_left_event(
                        clan=clan,
                        snapshot=snapshot,
                        account=account,
                        observed_at=observed_at,
                    )
                )
                left_count += 1

        for player_tag, member in incoming_members.items():
            account = await self._repository.get_player_account_by_tag(player_tag)
            moved_from_other_clans_count = (
                await self._repository.close_current_snapshots_in_other_clans(
                    player_tag=player_tag,
                    clan_id=clan_id,
                    observed_at=observed_at,
                )
            )
            moved_count += moved_from_other_clans_count
            if moved_from_other_clans_count:
                self._repository.add_player_event(
                    _build_member_moved_event(
                        clan=clan,
                        member=member,
                        account=account,
                        observed_at=observed_at,
                    )
                )

            snapshot = await self._repository.get_current_by_clan_and_player_tag(
                player_tag=player_tag,
                clan_id=clan_id,
            )
            if snapshot is None:
                snapshot = _build_member_snapshot(
                    clan=clan,
                    member=member,
                    observed_at=observed_at,
                )
                self._repository.add_member_snapshot(snapshot)
                self._repository.add_player_event(
                    _build_member_joined_event(
                        clan=clan,
                        member=member,
                        account=account,
                        observed_at=observed_at,
                    )
                )
                created_count += 1
            else:
                previous_name = snapshot.name
                _apply_member_snapshot_update(snapshot, member=member, observed_at=observed_at)
                if previous_name != member.name:
                    self._repository.add_player_event(
                        _build_member_renamed_event(
                            clan=clan,
                            member=member,
                            previous_name=previous_name,
                            account=account,
                            observed_at=observed_at,
                        )
                    )
                updated_count += 1

            if account is not None:
                account.last_seen_clan_id = clan_id
                account.last_seen_clan = clan

        await self._repository.flush()

        return MemberLifecycleResult(
            clan=clan,
            created_count=created_count,
            updated_count=updated_count,
            left_count=left_count,
            moved_count=moved_count,
            current_count=len(incoming_members),
        )


def _normalize_members(members: list[ClashClanMember]) -> dict[str, ClashClanMember]:
    """Нормализует список участников в словарь по player tag.

    Args:
        members: Участники клана из Clash API.

    Returns:
        Словарь `player_tag -> member`.

    Raises:
        MemberLifecycleError: Если вход содержит дубликаты player tag.
    """
    normalized_members: dict[str, ClashClanMember] = {}

    for member in members:
        player_tag = normalize_player_tag(member.player_tag)
        if player_tag in normalized_members:
            raise MemberLifecycleError(f"Дубликат player_tag в составе клана: {player_tag}.")
        normalized_members[player_tag] = member

    return normalized_members


def _build_member_snapshot(
    *,
    clan: Clan,
    member: ClashClanMember,
    observed_at: datetime,
) -> ClanMemberSnapshot:
    """Создаёт current snapshot участника клана.

    Args:
        clan: Отслеживаемый клан.
        member: Участник из Clash API.
        observed_at: Время наблюдения состава.

    Returns:
        Новый current snapshot.
    """
    return ClanMemberSnapshot(
        clan_id=_required_model_id(clan, model_name="Clan"),
        clan=clan,
        player_tag=normalize_player_tag(member.player_tag),
        name=member.name,
        role=member.role,
        town_hall_level=member.town_hall_level,
        exp_level=member.exp_level,
        trophies=member.trophies,
        donations=member.donations,
        donations_received=member.donations_received,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        snapshot_at=observed_at,
        is_current=True,
    )


def _apply_member_snapshot_update(
    snapshot: ClanMemberSnapshot,
    *,
    member: ClashClanMember,
    observed_at: datetime,
) -> None:
    """Обновляет current snapshot участника без потери first_seen_at.

    Args:
        snapshot: Текущий snapshot участника.
        member: Участник из Clash API.
        observed_at: Время наблюдения состава.
    """
    snapshot.name = member.name
    snapshot.role = member.role
    snapshot.town_hall_level = member.town_hall_level
    snapshot.exp_level = member.exp_level
    snapshot.trophies = member.trophies
    snapshot.donations = member.donations
    snapshot.donations_received = member.donations_received
    snapshot.last_seen_at = observed_at
    snapshot.snapshot_at = observed_at
    snapshot.is_current = True


def _mark_snapshot_not_current(snapshot: ClanMemberSnapshot, *, observed_at: datetime) -> None:
    """Закрывает current snapshot участника.

    Args:
        snapshot: Snapshot участника.
        observed_at: Время наблюдения ухода/перехода.
    """
    snapshot.is_current = False
    snapshot.last_seen_at = observed_at
    snapshot.snapshot_at = observed_at


def _build_member_joined_event(
    *,
    clan: Clan,
    member: ClashClanMember,
    account: PlayerAccount | None,
    observed_at: datetime,
) -> PlayerEvent:
    """Создаёт событие появления игрока в клане.

    Args:
        clan: Клан, в котором замечен игрок.
        member: Участник из Clash API.
        account: Подтверждённый аккаунт игрока, если он есть.
        observed_at: Время наблюдения состава.

    Returns:
        Модель события игрока.
    """
    player_tag = normalize_player_tag(member.player_tag)
    return PlayerEvent(
        telegram_user_id=_account_telegram_user_id(account),
        player_tag=player_tag,
        event_type=_MEMBER_JOINED_EVENT_TYPE,
        title="Игрок появился в клане",
        description=f"Игрок {member.name} появился в клане {clan.name}.",
        metadata_json=_build_member_event_metadata(
            clan=clan,
            player_tag=player_tag,
            player_name=member.name,
        ),
        created_at=observed_at,
    )


def _build_member_left_event(
    *,
    clan: Clan,
    snapshot: ClanMemberSnapshot,
    account: PlayerAccount | None,
    observed_at: datetime,
) -> PlayerEvent:
    """Создаёт событие ухода игрока из клана.

    Args:
        clan: Клан, из которого пропал игрок.
        snapshot: Последний current snapshot игрока.
        account: Подтверждённый аккаунт игрока, если он есть.
        observed_at: Время наблюдения ухода.

    Returns:
        Модель события игрока.
    """
    player_tag = normalize_player_tag(snapshot.player_tag)
    return PlayerEvent(
        telegram_user_id=_account_telegram_user_id(account),
        player_tag=player_tag,
        event_type=_MEMBER_LEFT_EVENT_TYPE,
        title="Игрок вышел из клана",
        description=f"Игрок {snapshot.name} больше не найден в составе клана {clan.name}.",
        metadata_json=_build_member_event_metadata(
            clan=clan,
            player_tag=player_tag,
            player_name=snapshot.name,
        ),
        created_at=observed_at,
    )


def _build_member_moved_event(
    *,
    clan: Clan,
    member: ClashClanMember,
    account: PlayerAccount | None,
    observed_at: datetime,
) -> PlayerEvent:
    """Создаёт событие перехода игрока в другой отслеживаемый клан.

    Args:
        clan: Новый актуальный клан игрока.
        member: Участник из Clash API.
        account: Подтверждённый аккаунт игрока, если он есть.
        observed_at: Время наблюдения перехода.

    Returns:
        Модель события игрока.
    """
    player_tag = normalize_player_tag(member.player_tag)
    return PlayerEvent(
        telegram_user_id=_account_telegram_user_id(account),
        player_tag=player_tag,
        event_type=_MEMBER_MOVED_EVENT_TYPE,
        title="Игрок перешёл в другой клан",
        description=f"Игрок {member.name} теперь найден в клане {clan.name}.",
        metadata_json=_build_member_event_metadata(
            clan=clan,
            player_tag=player_tag,
            player_name=member.name,
        ),
        created_at=observed_at,
    )


def _build_member_renamed_event(
    *,
    clan: Clan,
    member: ClashClanMember,
    previous_name: str,
    account: PlayerAccount | None,
    observed_at: datetime,
) -> PlayerEvent:
    """Создаёт событие смены ника игрока.

    Args:
        clan: Клан, где замечена смена ника.
        member: Участник из Clash API с новым ником.
        previous_name: Предыдущее имя из current snapshot.
        account: Подтверждённый аккаунт игрока, если он есть.
        observed_at: Время наблюдения смены ника.

    Returns:
        Модель события игрока.
    """
    player_tag = normalize_player_tag(member.player_tag)
    return PlayerEvent(
        telegram_user_id=_account_telegram_user_id(account),
        player_tag=player_tag,
        event_type=_MEMBER_RENAMED_EVENT_TYPE,
        title="Игрок сменил ник",
        description=f"Игрок {previous_name} сменил ник на {member.name}.",
        metadata_json=_build_member_event_metadata(
            clan=clan,
            player_tag=player_tag,
            player_name=member.name,
            previous_name=previous_name,
        ),
        created_at=observed_at,
    )


def _build_member_event_metadata(
    *,
    clan: Clan,
    player_tag: str,
    player_name: str,
    previous_name: str | None = None,
) -> dict[str, object]:
    """Собирает metadata для события жизненного цикла участника.

    Args:
        clan: Клан события.
        player_tag: Тег игрока.
        player_name: Актуальное имя игрока.
        previous_name: Предыдущее имя игрока, если событие связано со сменой ника.

    Returns:
        JSON-совместимый словарь metadata.
    """
    metadata: dict[str, object] = {
        "clan_id": _required_model_id(clan, model_name="Clan"),
        "clan_tag": clan.tag,
        "clan_name": clan.name,
        "player_tag": normalize_player_tag(player_tag),
        "player_name": player_name,
    }
    if previous_name is not None:
        metadata["previous_name"] = previous_name

    return metadata


def _account_telegram_user_id(account: PlayerAccount | None) -> int | None:
    """Возвращает TelegramUser ID подтверждённого аккаунта.

    Args:
        account: Подтверждённый аккаунт игрока или `None`.

    Returns:
        DB ID TelegramUser или `None`.
    """
    if account is None:
        return None

    return account.telegram_user_id


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.

    Raises:
        MemberLifecycleError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise MemberLifecycleError(f"{model_name} должен быть сохранён в БД.")


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "MemberLifecycleError",
    "MemberLifecycleRepository",
    "MemberLifecycleResult",
    "MemberLifecycleService",
    "SqlAlchemyMemberLifecycleRepository",
]

```


## FILE: app/services/telegram_users.py

```python
"""Сервис управления Telegram-пользователями."""

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings
from app.db.models import TelegramUser

_TELEGRAM_TEXT_MAX_LENGTH = 255


class TelegramUserRepository(Protocol):
    """Repository contract для управления моделью `TelegramUser`."""

    async def get_by_telegram_id(self, telegram_id: int) -> TelegramUser | None:
        """Возвращает Telegram-пользователя по Telegram ID.

        Args:
            telegram_id: Внешний Telegram ID пользователя.

        Returns:
            Модель пользователя или `None`.
        """

    def add(self, telegram_user: TelegramUser) -> None:
        """Добавляет Telegram-пользователя в unit of work.

        Args:
            telegram_user: Новая модель Telegram-пользователя.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyTelegramUserRepository:
    """SQLAlchemy-реализация repository для Telegram-пользователей."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_telegram_id(self, telegram_id: int) -> TelegramUser | None:
        """Возвращает Telegram-пользователя по Telegram ID.

        Args:
            telegram_id: Внешний Telegram ID пользователя.

        Returns:
            Модель пользователя или `None`.
        """
        result = await self._session.execute(
            select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    def add(self, telegram_user: TelegramUser) -> None:
        """Добавляет Telegram-пользователя в текущую session.

        Args:
            telegram_user: Новая модель Telegram-пользователя.
        """
        self._session.add(telegram_user)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class TelegramUserService:
    """Сервис управления Telegram-пользователями.

    Сервис отвечает за idempotent upsert пользователя, обновление профиля,
    controlled refresh admin-cache и helpers доступа. Источником истины для
    admin-прав остаётся `TELEGRAM_ADMIN_ID` из runtime settings.
    """

    def __init__(self, *, repository: TelegramUserRepository, settings: Settings) -> None:
        """Инициализирует service.

        Args:
            repository: Repository для доступа к Telegram-пользователям.
            settings: Runtime settings с `telegram_admin_id`.
        """
        self._repository = repository
        self._settings = settings

    @classmethod
    def from_session(cls, *, session: AsyncSession, settings: Settings) -> "TelegramUserService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            settings: Runtime settings.

        Returns:
            Настроенный service.
        """
        return cls(
            repository=SqlAlchemyTelegramUserRepository(session),
            settings=settings,
        )

    async def upsert_telegram_user(
        self,
        *,
        telegram_id: int,
        username: str | None,
        display_name: str | None,
    ) -> TelegramUser:
        """Создаёт или обновляет Telegram-пользователя.

        Повторный вызов, например повторный `/start`, не создаёт дубль.
        У существующего пользователя обновляются username, display name,
        last_seen_at и controlled admin cache.

        Args:
            telegram_id: Внешний Telegram ID пользователя.
            username: Telegram username, если есть.
            display_name: Отображаемое имя, если есть.

        Returns:
            Созданная или обновлённая модель пользователя.

        Raises:
            ValueError: Если `telegram_id` некорректный.
        """
        normalized_telegram_id = _validate_telegram_id(telegram_id)
        normalized_username = _normalize_optional_text(username)
        normalized_display_name = _normalize_optional_text(display_name)
        seen_at = _utc_now()
        is_admin = self.is_admin_telegram_id(normalized_telegram_id)

        telegram_user = await self._repository.get_by_telegram_id(normalized_telegram_id)
        if telegram_user is None:
            telegram_user = TelegramUser(
                telegram_id=normalized_telegram_id,
                username=normalized_username,
                display_name=normalized_display_name,
                first_seen_at=seen_at,
                last_seen_at=seen_at,
                is_admin_cached=is_admin,
            )
            self._repository.add(telegram_user)
        else:
            telegram_user.username = normalized_username
            telegram_user.display_name = normalized_display_name
            telegram_user.last_seen_at = seen_at
            telegram_user.is_admin_cached = is_admin

        await self._repository.flush()
        return telegram_user

    def is_admin_telegram_id(self, telegram_id: int) -> bool:
        """Проверяет, является ли Telegram ID админом.

        Args:
            telegram_id: Внешний Telegram ID пользователя.

        Returns:
            `True`, если ID совпадает с `TELEGRAM_ADMIN_ID` из settings.

        Raises:
            ValueError: Если `telegram_id` некорректный.
        """
        return _validate_telegram_id(telegram_id) == self._settings.telegram_admin_id

    def has_admin_access(self, telegram_user: TelegramUser | None) -> bool:
        """Проверяет admin-доступ пользователя.

        Проверка намеренно идёт через settings, а не через `is_admin_cached`,
        потому что cache-поле нужно для удобства отображения, но не является
        главным источником истины.

        Args:
            telegram_user: Модель Telegram-пользователя или `None`.

        Returns:
            `True`, если пользователь является админом по settings.
        """
        if telegram_user is None:
            return False

        return self.is_admin_telegram_id(telegram_user.telegram_id)


def _validate_telegram_id(value: int) -> int:
    """Проверяет Telegram ID.

    Args:
        value: Telegram ID.

    Returns:
        Проверенный положительный Telegram ID.

    Raises:
        ValueError: Если значение не является положительным int.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("telegram_id должен быть целым числом.")

    if value <= 0:
        raise ValueError("telegram_id должен быть положительным числом.")

    return value


def _normalize_optional_text(value: str | None) -> str | None:
    """Нормализует опциональное текстовое поле Telegram-профиля.

    Args:
        value: Сырой username или display name.

    Returns:
        Строка без пробелов по краям, обрезанная под DB-limit, или `None`.
    """
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    return normalized[:_TELEGRAM_TEXT_MAX_LENGTH]


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "SqlAlchemyTelegramUserRepository",
    "TelegramUserRepository",
    "TelegramUserService",
]

```


## FILE: app/services/account_linking.py

```python
"""Сервис привязки игровых аккаунтов к Telegram-пользователям."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PlayerAccount, PlayerEvent, TelegramUser
from app.domain import normalize_player_tag
from app.integrations.clash import VerifyPlayerTokenResult

_ACCOUNT_LINKED_EVENT_TYPE = "account_linked"
_VERIFICATION_FAILED_REASON = "verification_failed"


class AccountLinkingError(RuntimeError):
    """Базовая ошибка сервиса привязки игровых аккаунтов."""


@dataclass(frozen=True, slots=True)
class AccountLinkingResult:
    """Результат сценария привязки игрового аккаунта."""

    success: bool
    reason: str | None
    verification_status: str
    player_account: PlayerAccount | None
    event: PlayerEvent | None


class ClashAccountProvider(Protocol):
    """Минимальный contract Clash API client для account linking service."""

    async def verify_player_token(
        self,
        player_tag: str,
        token: str,
    ) -> VerifyPlayerTokenResult:
        """Проверяет one-time API token игрока.

        Args:
            player_tag: Нормализованный тег игрока.
            token: One-time API token из игры.

        Returns:
            Typed result проверки владения аккаунтом.
        """

    async def get_player(self, player_tag: str) -> Mapping[str, object]:
        """Получает профиль игрока.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            JSON object профиля игрока.
        """


class AccountRepository(Protocol):
    """Repository contract для управления `PlayerAccount`."""

    async def get_by_player_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает аккаунт по нормализованному тегу.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            Модель аккаунта или `None`.
        """

    def add(self, player_account: PlayerAccount) -> None:
        """Добавляет аккаунт в unit of work.

        Args:
            player_account: Новая модель аккаунта.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class PlayerEventRepository(Protocol):
    """Repository contract для истории событий игрока."""

    def add(self, player_event: PlayerEvent) -> None:
        """Добавляет событие игрока в unit of work.

        Args:
            player_event: Новая модель события.
        """


class SqlAlchemyAccountRepository:
    """SQLAlchemy-реализация repository для игровых аккаунтов."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_player_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает аккаунт по нормализованному тегу.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            Модель аккаунта или `None`.
        """
        result = await self._session.execute(
            select(PlayerAccount).where(PlayerAccount.player_tag == player_tag)
        )
        return result.scalar_one_or_none()

    def add(self, player_account: PlayerAccount) -> None:
        """Добавляет аккаунт в текущую session.

        Args:
            player_account: Новая модель аккаунта.
        """
        self._session.add(player_account)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class SqlAlchemyPlayerEventRepository:
    """SQLAlchemy-реализация repository для событий игрока."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    def add(self, player_event: PlayerEvent) -> None:
        """Добавляет событие игрока в текущую session.

        Args:
            player_event: Новая модель события.
        """
        self._session.add(player_event)


class AccountLinkingService:
    """Сервис привязки игрового аккаунта к Telegram-пользователю.

    Сервис не хранит one-time API token. Token используется только для вызова
    `verifytoken`, после чего в БД сохраняются только подтверждённый player tag,
    имя игрока, связь с Telegram-пользователем и событие истории.
    """

    def __init__(
        self,
        *,
        account_repository: AccountRepository,
        event_repository: PlayerEventRepository,
        clash_client: ClashAccountProvider,
    ) -> None:
        """Инициализирует service.

        Args:
            account_repository: Repository игровых аккаунтов.
            event_repository: Repository событий игрока.
            clash_client: Клиент Clash API или совместимый provider.
        """
        self._account_repository = account_repository
        self._event_repository = event_repository
        self._clash_client = clash_client

    @classmethod
    def from_session(
        cls,
        *,
        session: AsyncSession,
        clash_client: ClashAccountProvider,
    ) -> "AccountLinkingService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            clash_client: Клиент Clash API или совместимый provider.

        Returns:
            Настроенный service.
        """
        return cls(
            account_repository=SqlAlchemyAccountRepository(session),
            event_repository=SqlAlchemyPlayerEventRepository(session),
            clash_client=clash_client,
        )

    async def link_account(
        self,
        *,
        telegram_user: TelegramUser,
        player_tag: str,
        api_token: str,
    ) -> AccountLinkingResult:
        """Привязывает игровой аккаунт к Telegram-пользователю.

        Args:
            telegram_user: Telegram-пользователь, к которому привязывается аккаунт.
            player_tag: Тег игрового аккаунта.
            api_token: One-time API token из игры. Не сохраняется.

        Returns:
            Результат привязки. При failed verifytoken аккаунт и событие не создаются.

        Raises:
            AccountLinkingError: Если Clash API вернул tag, отличный от запрошенного.
            ValueError: Если профиль игрока не содержит имя.
        """
        normalized_player_tag = normalize_player_tag(player_tag)
        verification_result = await self._clash_client.verify_player_token(
            normalized_player_tag,
            api_token,
        )

        if not verification_result.is_successful:
            return AccountLinkingResult(
                success=False,
                reason=_VERIFICATION_FAILED_REASON,
                verification_status=verification_result.status,
                player_account=None,
                event=None,
            )

        if verification_result.player_tag != normalized_player_tag:
            raise AccountLinkingError(
                "Clash API verifytoken вернул другой player_tag, привязка остановлена."
            )

        player_payload = await self._clash_client.get_player(verification_result.player_tag)
        player_name = _extract_player_name(player_payload)

        account = await self._account_repository.get_by_player_tag(verification_result.player_tag)
        previous_telegram_user_id = _model_id(account) if account is not None else None
        linked_at = _utc_now()

        if account is None:
            account = PlayerAccount(
                telegram_user_id=_model_id(telegram_user),
                player_tag=verification_result.player_tag,
                name=player_name,
                is_active=True,
                linked_at=linked_at,
                unlinked_at=None,
            )
            account.telegram_user = telegram_user
            self._account_repository.add(account)
        else:
            previous_telegram_user_id = account.telegram_user_id
            _apply_account_linkage(
                account,
                telegram_user=telegram_user,
                player_name=player_name,
                linked_at=linked_at,
            )

        event = _build_account_linked_event(
            telegram_user=telegram_user,
            player_account=account,
            previous_telegram_user_id=previous_telegram_user_id,
            created_at=linked_at,
        )
        self._event_repository.add(event)

        await self._account_repository.flush()

        return AccountLinkingResult(
            success=True,
            reason=None,
            verification_status=verification_result.status,
            player_account=account,
            event=event,
        )


def _apply_account_linkage(
    account: PlayerAccount,
    *,
    telegram_user: TelegramUser,
    player_name: str,
    linked_at: datetime,
) -> None:
    """Обновляет связь существующего игрового аккаунта.

    Args:
        account: Существующая модель игрового аккаунта.
        telegram_user: Новый владелец Telegram.
        player_name: Актуальное имя игрока из Clash API.
        linked_at: Время успешной привязки.
    """
    account.telegram_user_id = _model_id(telegram_user)
    account.telegram_user = telegram_user
    account.name = player_name
    account.is_active = True
    account.linked_at = linked_at
    account.unlinked_at = None


def _build_account_linked_event(
    *,
    telegram_user: TelegramUser,
    player_account: PlayerAccount,
    previous_telegram_user_id: int | None,
    created_at: datetime,
) -> PlayerEvent:
    """Создаёт событие истории о привязке аккаунта.

    Args:
        telegram_user: Telegram-пользователь.
        player_account: Привязанный игровой аккаунт.
        previous_telegram_user_id: Предыдущий TelegramUser ID, если был перенос.
        created_at: Время события.

    Returns:
        Модель события игрока.
    """
    return PlayerEvent(
        telegram_user_id=_model_id(telegram_user),
        player_tag=player_account.player_tag,
        event_type=_ACCOUNT_LINKED_EVENT_TYPE,
        title="Игровой аккаунт привязан",
        description=f"Аккаунт {player_account.name} привязан к Telegram-пользователю.",
        metadata_json={
            "player_tag": player_account.player_tag,
            "player_name": player_account.name,
            "telegram_id": telegram_user.telegram_id,
            "previous_telegram_user_id": previous_telegram_user_id,
        },
        created_at=created_at,
    )


def _extract_player_name(payload: Mapping[str, object]) -> str:
    """Извлекает имя игрока из profile payload.

    Args:
        payload: JSON object профиля игрока.

    Returns:
        Непустое имя игрока.

    Raises:
        ValueError: Если поле `name` отсутствует или пустое.
    """
    name = payload.get("name")
    if not isinstance(name, str):
        raise ValueError("Профиль игрока должен содержать строковое поле name.")

    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Профиль игрока содержит пустое имя.")

    return normalized_name


def _model_id(model: object | None) -> int | None:
    """Достаёт DB id из SQLAlchemy model, если он уже назначен.

    Args:
        model: SQLAlchemy model или `None`.

    Returns:
        Положительный DB id или `None`.
    """
    if model is None:
        return None

    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    return None


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "AccountLinkingError",
    "AccountLinkingResult",
    "AccountLinkingService",
    "AccountRepository",
    "ClashAccountProvider",
    "PlayerEventRepository",
    "SqlAlchemyAccountRepository",
    "SqlAlchemyPlayerEventRepository",
]

```


## FILE: app/services/account_unlinking.py

```python
"""Сервис отвязки игровых аккаунтов от Telegram-пользователей."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClanMemberSnapshot, PlayerAccount, PlayerEvent, TelegramUser
from app.domain import normalize_player_tag
from app.services.account_linking import PlayerEventRepository, SqlAlchemyPlayerEventRepository

_ACCOUNT_NOT_FOUND_REASON = "account_not_found"
_ACCOUNT_UNLINKED_EVENT_TYPE = "account_unlinked"


class AccountUnlinkingError(RuntimeError):
    """Базовая ошибка сервиса отвязки игровых аккаунтов."""


@dataclass(frozen=True, slots=True)
class AccountUnlinkingResult:
    """Результат сценария отвязки игрового аккаунта."""

    success: bool
    reason: str | None
    already_unlinked: bool
    player_account: PlayerAccount | None
    event: PlayerEvent | None
    was_in_current_clan: bool
    should_recommend_telegram_removal: bool


class AccountUnlinkingRepository(Protocol):
    """Repository contract для отвязки `PlayerAccount`."""

    async def get_by_player_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает аккаунт по нормализованному тегу.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            Модель аккаунта или `None`.
        """

    async def has_current_clan_member_snapshot(self, player_tag: str) -> bool:
        """Проверяет, есть ли аккаунт в текущем API-составе клана.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            `True`, если существует текущий `ClanMemberSnapshot`.
        """

    async def has_other_active_accounts(
        self,
        *,
        telegram_user_id: int,
        excluding_player_tag: str,
    ) -> bool:
        """Проверяет, есть ли у TelegramUser другие активные аккаунты.

        Args:
            telegram_user_id: DB ID TelegramUser.
            excluding_player_tag: Тег аккаунта, который сейчас отвязывается.

        Returns:
            `True`, если есть другой активный аккаунт.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyAccountUnlinkingRepository:
    """SQLAlchemy-реализация repository для отвязки аккаунтов."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_player_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает аккаунт по нормализованному тегу.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            Модель аккаунта или `None`.
        """
        result = await self._session.execute(
            select(PlayerAccount).where(PlayerAccount.player_tag == player_tag)
        )
        return result.scalar_one_or_none()

    async def has_current_clan_member_snapshot(self, player_tag: str) -> bool:
        """Проверяет, есть ли аккаунт в текущем составе клана.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            `True`, если аккаунт найден в текущих snapshot-данных состава.
        """
        result = await self._session.execute(
            select(ClanMemberSnapshot.id)
            .where(
                ClanMemberSnapshot.player_tag == player_tag,
                ClanMemberSnapshot.is_current.is_(True),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def has_other_active_accounts(
        self,
        *,
        telegram_user_id: int,
        excluding_player_tag: str,
    ) -> bool:
        """Проверяет наличие других активных аккаунтов TelegramUser.

        Args:
            telegram_user_id: DB ID TelegramUser.
            excluding_player_tag: Тег аккаунта, который сейчас отвязывается.

        Returns:
            `True`, если есть другой активный аккаунт.
        """
        result = await self._session.execute(
            select(PlayerAccount.id)
            .where(
                PlayerAccount.telegram_user_id == telegram_user_id,
                PlayerAccount.player_tag != excluding_player_tag,
                PlayerAccount.is_active.is_(True),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class AccountUnlinkingService:
    """Сервис отвязки игрового аккаунта от Telegram-пользователя.

    Сервис не удаляет `PlayerAccount` физически. Аккаунт переводится в
    неактивное состояние без Telegram-связи, а история сохраняется через
    `PlayerEvent`.
    """

    def __init__(
        self,
        *,
        account_repository: AccountUnlinkingRepository,
        event_repository: PlayerEventRepository,
    ) -> None:
        """Инициализирует service.

        Args:
            account_repository: Repository игровых аккаунтов.
            event_repository: Repository событий игрока.
        """
        self._account_repository = account_repository
        self._event_repository = event_repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "AccountUnlinkingService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(
            account_repository=SqlAlchemyAccountUnlinkingRepository(session),
            event_repository=SqlAlchemyPlayerEventRepository(session),
        )

    async def unlink_account(
        self,
        *,
        telegram_user: TelegramUser,
        player_tag: str,
    ) -> AccountUnlinkingResult:
        """Отвязывает игровой аккаунт от Telegram-пользователя.

        Args:
            telegram_user: Telegram-пользователь, который отвязывает свой аккаунт.
            player_tag: Тег игрового аккаунта.

        Returns:
            Результат отвязки.

        Raises:
            AccountUnlinkingError: Если аккаунт принадлежит другому TelegramUser
                или TelegramUser не имеет DB ID.
        """
        telegram_user_id = _required_model_id(telegram_user)
        normalized_player_tag = normalize_player_tag(player_tag)
        account = await self._account_repository.get_by_player_tag(normalized_player_tag)

        if account is None:
            return AccountUnlinkingResult(
                success=False,
                reason=_ACCOUNT_NOT_FOUND_REASON,
                already_unlinked=False,
                player_account=None,
                event=None,
                was_in_current_clan=False,
                should_recommend_telegram_removal=False,
            )

        if account.telegram_user_id is not None and account.telegram_user_id != telegram_user_id:
            raise AccountUnlinkingError("Нельзя отвязать аккаунт другого Telegram-пользователя.")

        was_in_current_clan = await self._account_repository.has_current_clan_member_snapshot(
            normalized_player_tag
        )

        if account.telegram_user_id is None and account.is_active is False:
            return AccountUnlinkingResult(
                success=True,
                reason=None,
                already_unlinked=True,
                player_account=account,
                event=None,
                was_in_current_clan=was_in_current_clan,
                should_recommend_telegram_removal=False,
            )

        has_other_active_accounts = await self._account_repository.has_other_active_accounts(
            telegram_user_id=telegram_user_id,
            excluding_player_tag=normalized_player_tag,
        )
        should_recommend_telegram_removal = not has_other_active_accounts
        unlinked_at = _utc_now()

        account.telegram_user_id = None
        account.telegram_user = None
        account.is_active = False
        account.unlinked_at = unlinked_at

        event = _build_account_unlinked_event(
            telegram_user=telegram_user,
            player_account=account,
            was_in_current_clan=was_in_current_clan,
            should_recommend_telegram_removal=should_recommend_telegram_removal,
            created_at=unlinked_at,
        )
        self._event_repository.add(event)

        await self._account_repository.flush()

        return AccountUnlinkingResult(
            success=True,
            reason=None,
            already_unlinked=False,
            player_account=account,
            event=event,
            was_in_current_clan=was_in_current_clan,
            should_recommend_telegram_removal=should_recommend_telegram_removal,
        )


def _build_account_unlinked_event(
    *,
    telegram_user: TelegramUser,
    player_account: PlayerAccount,
    was_in_current_clan: bool,
    should_recommend_telegram_removal: bool,
    created_at: datetime,
) -> PlayerEvent:
    """Создаёт событие истории об отвязке аккаунта.

    Args:
        telegram_user: Telegram-пользователь.
        player_account: Отвязанный игровой аккаунт.
        was_in_current_clan: Был ли аккаунт в текущем составе клана.
        should_recommend_telegram_removal: Нужно ли рекомендовать удаление из Telegram.
        created_at: Время события.

    Returns:
        Модель события игрока.
    """
    return PlayerEvent(
        telegram_user_id=_required_model_id(telegram_user),
        player_tag=player_account.player_tag,
        event_type=_ACCOUNT_UNLINKED_EVENT_TYPE,
        title="Игровой аккаунт отвязан",
        description=f"Аккаунт {player_account.name} отвязан от Telegram-пользователя.",
        metadata_json={
            "player_tag": player_account.player_tag,
            "player_name": player_account.name,
            "telegram_id": telegram_user.telegram_id,
            "was_in_current_clan": was_in_current_clan,
            "should_recommend_telegram_removal": should_recommend_telegram_removal,
        },
        created_at=created_at,
    )


def _required_model_id(model: object) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.

    Returns:
        Положительный DB id.

    Raises:
        AccountUnlinkingError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise AccountUnlinkingError("TelegramUser должен быть сохранён в БД перед отвязкой аккаунта.")


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "AccountUnlinkingError",
    "AccountUnlinkingRepository",
    "AccountUnlinkingResult",
    "AccountUnlinkingService",
    "SqlAlchemyAccountUnlinkingRepository",
]

```


## FILE: app/services/warning_creation.py

```python
"""Сервис создания warn-записей."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Clan, TelegramUser, Warning
from app.domain import (
    IMPACTFUL_WARNING_REASON_CODES,
    MANUAL_WARNING_REASON_CODES,
    SYSTEM_WARNING_REASON_CODES,
    WarningReasonCode,
    WarningSource,
    WarningStatus,
    normalize_player_tag,
    require_domain_enum_value,
)

_CATEGORY_CWL = "cwl"
_CATEGORY_DISCIPLINE = "discipline"
_CATEGORY_RAID = "raid"
_CATEGORY_WAR = "war"


class WarningCreationError(RuntimeError):
    """Базовая ошибка сервиса создания warn."""


@dataclass(frozen=True, slots=True)
class WarningAffectedAccount:
    """Затронутый warn игровой аккаунт."""

    player_tag: str
    player_name: str


@dataclass(frozen=True, slots=True)
class WarningCreationResult:
    """Результат создания warn."""

    warning: Warning
    created: bool


class WarningCreationRepository(Protocol):
    """Repository contract для создания warn."""

    async def get_telegram_user_by_id(self, telegram_user_id: int) -> TelegramUser | None:
        """Возвращает TelegramUser по DB ID.

        Args:
            telegram_user_id: DB ID TelegramUser.
        """

    async def get_by_event_key(self, event_key: str) -> Warning | None:
        """Возвращает warn по event key.

        Args:
            event_key: Идемпотентный ключ события.

        Returns:
            Warn или `None`.
        """

    def add(self, warning: Warning) -> None:
        """Добавляет warn в unit of work.

        Args:
            warning: Новая warn-запись.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyWarningRepository:
    """SQLAlchemy-реализация repository для warn."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_telegram_user_by_id(self, telegram_user_id: int) -> TelegramUser | None:
        """Возвращает TelegramUser по DB ID."""
        result = await self._session.execute(
            select(TelegramUser).where(TelegramUser.id == telegram_user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_event_key(self, event_key: str) -> Warning | None:
        """Возвращает warn по event key.

        Args:
            event_key: Идемпотентный ключ события.

        Returns:
            Warn или `None`.
        """
        result = await self._session.execute(select(Warning).where(Warning.event_key == event_key))
        return result.scalar_one_or_none()

    def add(self, warning: Warning) -> None:
        """Добавляет warn в текущую session.

        Args:
            warning: Новая warn-запись.
        """
        self._session.add(warning)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class WarningCreationService:
    """Сервис создания manual/system warn.

    Сервис не создаёт kick candidates и не отправляет уведомления. Эти сценарии
    закрываются отдельными сервисами следующих коммитов.
    """

    def __init__(self, *, repository: WarningCreationRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository warn-записей.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "WarningCreationService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyWarningRepository(session))

    async def create_manual_warning(
        self,
        *,
        telegram_user: TelegramUser,
        reason_code: WarningReasonCode | str,
        author_telegram_user: TelegramUser | None = None,
        clan: Clan | None = None,
        affected_accounts: list[WarningAffectedAccount] | None = None,
        comment: str | None = None,
        event_key: str | None = None,
    ) -> WarningCreationResult:
        """Создаёт manual warn.

        Manual warn всегда non-impactful. По умолчанию manual warn создаётся без
        `event_key`, чтобы не блокировать повторное ручное наказание. Если
        `event_key` явно передан, сервис выполняет dedup.

        Args:
            telegram_user: Пользователь, которому создаётся warn.
            reason_code: Причина manual warn.
            author_telegram_user: Автор warn, если известен.
            clan: Клан контекста warn, если известен.
            affected_accounts: Затронутые игровые аккаунты.
            comment: Комментарий к warn.
            event_key: Опциональный ключ дедупликации.

        Returns:
            Результат создания warn.
        """
        normalized_reason = _normalize_reason_code(reason_code)
        if normalized_reason not in MANUAL_WARNING_REASON_CODES:
            raise WarningCreationError("Manual warn не может использовать system reason_code.")

        return await self._create_warning(
            telegram_user=telegram_user,
            source=WarningSource.MANUAL,
            reason_code=normalized_reason,
            is_impactful=False,
            author_telegram_user=author_telegram_user,
            clan=clan,
            affected_accounts=affected_accounts or [],
            affected_accounts_required=False,
            comment=comment,
            event_key=event_key,
            created_cwl_season_key=None,
        )

    async def create_manual_warning_for_telegram_user_id(
        self,
        *,
        telegram_user_id: int,
        reason_code: WarningReasonCode | str,
        author_telegram_user: TelegramUser | None = None,
        affected_accounts: list[WarningAffectedAccount] | None = None,
        comment: str | None = None,
    ) -> WarningCreationResult:
        """Создаёт manual warn по DB ID TelegramUser.

        Метод нужен bot-слою: FSM хранит только ID цели, а handler не должен
        напрямую создавать SQLAlchemy model или читать БД.

        Args:
            telegram_user_id: DB ID пользователя-цели.
            reason_code: Причина manual warn.
            author_telegram_user: Автор warn.
            affected_accounts: Затронутые аккаунты.
            comment: Комментарий.

        Returns:
            Результат создания manual warn.
        """
        target_user_id = _validate_positive_int(telegram_user_id, field_name="telegram_user_id")
        target_user = await self._repository.get_telegram_user_by_id(target_user_id)
        if target_user is None:
            raise WarningCreationError(f"TelegramUser {target_user_id} не найден.")

        return await self.create_manual_warning(
            telegram_user=target_user,
            reason_code=reason_code,
            author_telegram_user=author_telegram_user,
            affected_accounts=affected_accounts,
            comment=comment,
        )

    async def create_system_warning(
        self,
        *,
        telegram_user: TelegramUser,
        reason_code: WarningReasonCode | str,
        event_key: str,
        affected_accounts: list[WarningAffectedAccount],
        clan: Clan | None = None,
        comment: str | None = None,
        created_cwl_season_key: str | None = None,
    ) -> WarningCreationResult:
        """Создаёт system warn.

        System warn требует `event_key` и affected accounts, потому что worker
        jobs должны быть идемпотентными и сохранять затронутые аккаунты.

        Args:
            telegram_user: Пользователь, которому создаётся warn.
            reason_code: Причина system warn.
            event_key: Идемпотентный ключ события.
            affected_accounts: Затронутые игровые аккаунты.
            clan: Клан контекста warn, если известен.
            comment: Комментарий к warn.
            created_cwl_season_key: CWL season key, если warn связан с ЛВК.

        Returns:
            Результат создания warn.
        """
        normalized_reason = _normalize_reason_code(reason_code)
        if normalized_reason not in SYSTEM_WARNING_REASON_CODES:
            raise WarningCreationError("System warn может использовать только system reason_code.")

        return await self._create_warning(
            telegram_user=telegram_user,
            source=WarningSource.SYSTEM,
            reason_code=normalized_reason,
            is_impactful=normalized_reason in IMPACTFUL_WARNING_REASON_CODES,
            author_telegram_user=None,
            clan=clan,
            affected_accounts=affected_accounts,
            affected_accounts_required=True,
            comment=comment,
            event_key=event_key,
            created_cwl_season_key=created_cwl_season_key,
        )

    async def _create_warning(
        self,
        *,
        telegram_user: TelegramUser,
        source: WarningSource,
        reason_code: WarningReasonCode,
        is_impactful: bool,
        author_telegram_user: TelegramUser | None,
        clan: Clan | None,
        affected_accounts: list[WarningAffectedAccount],
        affected_accounts_required: bool,
        comment: str | None,
        event_key: str | None,
        created_cwl_season_key: str | None,
    ) -> WarningCreationResult:
        """Создаёт warn с общей логикой dedup и нормализации.

        Args:
            telegram_user: Пользователь, которому создаётся warn.
            source: Источник warn.
            reason_code: Причина warn.
            is_impactful: Влияет ли warn на автоматические решения.
            author_telegram_user: Автор manual warn.
            clan: Клан контекста.
            affected_accounts: Затронутые аккаунты.
            affected_accounts_required: Требовать ли хотя бы один аккаунт.
            comment: Комментарий.
            event_key: Опциональный event key.
            created_cwl_season_key: CWL season key.

        Returns:
            Результат создания warn.
        """
        normalized_event_key = _normalize_optional_text(event_key)
        if normalized_event_key is not None:
            existing_warning = await self._repository.get_by_event_key(normalized_event_key)
            if existing_warning is not None:
                return WarningCreationResult(warning=existing_warning, created=False)

        affected_player_tags, affected_player_names = _normalize_affected_accounts(
            affected_accounts,
            required=affected_accounts_required,
        )

        warning = Warning(
            telegram_user_id=_required_model_id(telegram_user, model_name="TelegramUser"),
            telegram_user=telegram_user,
            source=source.value,
            status=WarningStatus.ACTIVE.value,
            reason_code=reason_code.value,
            category=_category_for_reason(reason_code),
            is_impactful=is_impactful,
            comment=_normalize_optional_text(comment),
            author_telegram_user_id=_optional_model_id(
                author_telegram_user,
                model_name="TelegramUser",
            ),
            author=author_telegram_user,
            clan_id=_optional_model_id(clan, model_name="Clan"),
            clan=clan,
            event_key=normalized_event_key,
            affected_player_tags_json=affected_player_tags,
            affected_player_names_json=affected_player_names,
            created_cwl_season_key=_normalize_optional_text(created_cwl_season_key),
        )
        self._repository.add(warning)
        await self._repository.flush()

        return WarningCreationResult(warning=warning, created=True)


def _normalize_reason_code(value: WarningReasonCode | str) -> WarningReasonCode:
    """Валидирует reason code через доменный enum.

    Args:
        value: Reason code.

    Returns:
        Enum member `WarningReasonCode`.
    """
    return require_domain_enum_value(WarningReasonCode, value, field_name="reason_code")


def _category_for_reason(reason_code: WarningReasonCode) -> str:
    """Определяет категорию warn по reason code.

    Args:
        reason_code: Причина warn.

    Returns:
        Строковая категория warn.
    """
    if reason_code in {
        WarningReasonCode.WAR_ATTACK_MISSED,
        WarningReasonCode.WAR_BAD_ATTACK,
        WarningReasonCode.WAR_WRONG_TARGET,
        WarningReasonCode.WAR_PLAN_IGNORED,
    }:
        return _CATEGORY_WAR

    if reason_code in {
        WarningReasonCode.CWL_ATTACK_MISSED,
        WarningReasonCode.CWL_WRONG_TARGET,
        WarningReasonCode.CWL_REMOVED,
    }:
        return _CATEGORY_CWL

    if reason_code in {
        WarningReasonCode.RAID_MISSED,
        WarningReasonCode.RAID_INCOMPLETE,
        WarningReasonCode.RAID_BAD_PLAY,
    }:
        return _CATEGORY_RAID

    return _CATEGORY_DISCIPLINE


def _normalize_affected_accounts(
    accounts: list[WarningAffectedAccount],
    *,
    required: bool,
) -> tuple[list[str], list[str]]:
    """Нормализует списки затронутых игровых аккаунтов.

    Args:
        accounts: Затронутые игровые аккаунты.
        required: Требовать ли непустой список.

    Returns:
        Пара списков: player tags и player names.

    Raises:
        WarningCreationError: Если список обязателен, пустой или содержит дубли.
    """
    if required and not accounts:
        raise WarningCreationError("System warn должен содержать affected accounts.")

    player_tags: list[str] = []
    player_names: list[str] = []
    seen_tags: set[str] = set()

    for account in accounts:
        player_tag = normalize_player_tag(account.player_tag)
        if player_tag in seen_tags:
            raise WarningCreationError(f"Дубликат affected player_tag: {player_tag}.")
        seen_tags.add(player_tag)

        player_name = account.player_name.strip()
        if not player_name:
            raise WarningCreationError("Affected player name не может быть пустым.")

        player_tags.append(player_tag)
        player_names.append(player_name)

    return player_tags, player_names


def _normalize_optional_text(value: str | None) -> str | None:
    """Нормализует опциональную строку.

    Args:
        value: Сырое значение.

    Returns:
        Строка без пробелов по краям или `None`.
    """
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.

    Raises:
        WarningCreationError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise WarningCreationError(f"{model_name} должен быть сохранён в БД.")


def _optional_model_id(model: object | None, *, model_name: str) -> int | None:
    """Достаёт опциональный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model или `None`.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id или `None`.

    Raises:
        WarningCreationError: Если модель передана, но id отсутствует.
    """
    if model is None:
        return None

    return _required_model_id(model, model_name=model_name)


def _validate_positive_int(value: int, *, field_name: str) -> int:
    """Проверяет положительный integer.

    Args:
        value: Значение.
        field_name: Имя поля.

    Returns:
        Проверенное значение.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise WarningCreationError(f"{field_name} должен быть целым числом.")

    if value <= 0:
        raise WarningCreationError(f"{field_name} должен быть положительным числом.")

    return value


__all__ = [
    "SqlAlchemyWarningRepository",
    "WarningAffectedAccount",
    "WarningCreationError",
    "WarningCreationRepository",
    "WarningCreationResult",
    "WarningCreationService",
]

```


## FILE: app/services/warning_lifecycle.py

```python
"""Сервис отмены и истечения warn."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CwlSeason, TelegramUser, Warning
from app.domain import WarningSource, WarningStatus


class WarningLifecycleError(RuntimeError):
    """Базовая ошибка сервиса жизненного цикла warn."""


class WarningNotFoundError(WarningLifecycleError):
    """Warn не найден."""


@dataclass(frozen=True, slots=True)
class WarningCancellationResult:
    """Результат отмены warn."""

    warning: Warning
    changed: bool


@dataclass(frozen=True, slots=True)
class WarningExpirationResult:
    """Результат истечения warn по CWL season."""

    expired_warnings: list[Warning]
    expired_count: int


class WarningLifecycleRepository(Protocol):
    """Repository contract для жизненного цикла warn."""

    async def get_by_id(self, warning_id: int) -> Warning | None:
        """Возвращает warn по DB ID.

        Args:
            warning_id: DB ID warn.

        Returns:
            Warn или `None`.
        """

    async def list_active_expirable_warnings(
        self,
        *,
        clan_id: int,
        current_cwl_season_key: str,
        current_cwl_season_started_at: datetime,
    ) -> list[Warning]:
        """Возвращает active warn, которые должны истечь.

        Args:
            clan_id: DB ID клана, внутри которого истекают warn.
            current_cwl_season_key: Обнаруженный CWL season key.
            current_cwl_season_started_at: Время обнаружения текущего CWL season.

        Returns:
            Список active system impactful warn, которые должны истечь.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyWarningLifecycleRepository:
    """SQLAlchemy-реализация repository для жизненного цикла warn."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_id(self, warning_id: int) -> Warning | None:
        """Возвращает warn по DB ID.

        Args:
            warning_id: DB ID warn.

        Returns:
            Warn или `None`.
        """
        result = await self._session.execute(select(Warning).where(Warning.id == warning_id))
        return result.scalar_one_or_none()

    async def list_active_expirable_warnings(
        self,
        *,
        clan_id: int,
        current_cwl_season_key: str,
        current_cwl_season_started_at: datetime,
    ) -> list[Warning]:
        """Возвращает active warn, которые должны истечь.

        Args:
            clan_id: DB ID клана.
            current_cwl_season_key: Обнаруженный CWL season key.
            current_cwl_season_started_at: Время обнаружения текущего CWL season.

        Returns:
            Список active system impactful warn, которые должны истечь.
        """
        result = await self._session.execute(
            select(Warning).where(
                Warning.status == WarningStatus.ACTIVE.value,
                Warning.source == WarningSource.SYSTEM.value,
                Warning.is_impactful.is_(True),
                Warning.clan_id == clan_id,
                or_(
                    and_(
                        Warning.created_cwl_season_key.is_not(None),
                        Warning.created_cwl_season_key != current_cwl_season_key,
                    ),
                    and_(
                        Warning.created_cwl_season_key.is_(None),
                        Warning.created_at < current_cwl_season_started_at,
                    ),
                ),
            )
        )
        return list(result.scalars().all())

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class WarningLifecycleService:
    """Сервис отмены и истечения warn.

    Сервис не удаляет warn физически и не создаёт уведомления. Он меняет только
    lifecycle-поля самой warn-записи.
    """

    def __init__(self, *, repository: WarningLifecycleRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository warn-записей.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "WarningLifecycleService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyWarningLifecycleRepository(session))

    async def cancel_warning(
        self,
        *,
        warning_id: int,
        cancelled_by: TelegramUser,
        reason: str,
    ) -> WarningCancellationResult:
        """Отменяет active warn админом.

        Повторная отмена already-cancelled warn идемпотентна и не перетирает
        исходные данные отмены.

        Args:
            warning_id: DB ID warn.
            cancelled_by: TelegramUser администратора.
            reason: Причина отмены.

        Returns:
            Результат отмены warn.

        Raises:
            WarningNotFoundError: Если warn не найден.
            WarningLifecycleError: Если warn не active или входные данные невалидны.
        """
        normalized_warning_id = _validate_positive_int(warning_id, field_name="warning_id")
        normalized_reason = _normalize_required_text(reason, field_name="cancelled_reason")
        admin_id = _required_model_id(cancelled_by, model_name="TelegramUser")
        warning = await self._get_required_warning(normalized_warning_id)

        if warning.status == WarningStatus.CANCELLED.value:
            return WarningCancellationResult(warning=warning, changed=False)

        if warning.status != WarningStatus.ACTIVE.value:
            raise WarningLifecycleError("Отменять можно только active warn.")

        warning.status = WarningStatus.CANCELLED.value
        warning.cancelled_at = _utc_now()
        warning.cancelled_by_telegram_user_id = admin_id
        warning.cancelled_by = cancelled_by
        warning.cancelled_reason = normalized_reason

        await self._repository.flush()

        return WarningCancellationResult(warning=warning, changed=True)

    async def expire_warnings_by_cwl_season(
        self,
        *,
        current_cwl_season: CwlSeason,
        expired_at: datetime | None = None,
    ) -> WarningExpirationResult:
        """Переводит старые active warn в expired при новом CWL season.

        Истекают только active system impactful warn внутри того же клана.
        Warn без `created_cwl_season_key` истекает только если сезон был
        обнаружен после создания warn.

        Args:
            current_cwl_season: Последний обнаруженный CWL season клана.
            expired_at: Явное время истечения для тестов.

        Returns:
            Результат истечения warn.
        """
        if current_cwl_season.clan_id <= 0:
            raise WarningLifecycleError("CwlSeason.clan_id должен быть положительным числом.")

        normalized_season_key = _normalize_required_text(
            current_cwl_season.season,
            field_name="current_cwl_season.season",
        )
        current_cwl_season_started_at = _require_aware_datetime(
            current_cwl_season.started_at,
            field_name="current_cwl_season.started_at",
        )
        warnings = await self._repository.list_active_expirable_warnings(
            clan_id=current_cwl_season.clan_id,
            current_cwl_season_key=normalized_season_key,
            current_cwl_season_started_at=current_cwl_season_started_at,
        )

        if not warnings:
            return WarningExpirationResult(expired_warnings=[], expired_count=0)

        normalized_expired_at = _require_aware_datetime(
            expired_at or _utc_now(),
            field_name="expired_at",
        )
        for warning in warnings:
            warning.status = WarningStatus.EXPIRED.value
            warning.expired_at = normalized_expired_at

        await self._repository.flush()

        return WarningExpirationResult(
            expired_warnings=warnings,
            expired_count=len(warnings),
        )

    async def _get_required_warning(self, warning_id: int) -> Warning:
        """Возвращает warn или выбрасывает service error.

        Args:
            warning_id: DB ID warn.

        Returns:
            Warn.

        Raises:
            WarningNotFoundError: Если warn не найден.
        """
        warning = await self._repository.get_by_id(warning_id)
        if warning is None:
            raise WarningNotFoundError(f"Warn {warning_id} не найден.")

        return warning


def _validate_positive_int(value: int, *, field_name: str) -> int:
    """Проверяет положительный integer.

    Args:
        value: Проверяемое значение.
        field_name: Имя поля для текста ошибки.

    Returns:
        Проверенное значение.

    Raises:
        WarningLifecycleError: Если значение некорректное.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise WarningLifecycleError(f"{field_name} должен быть целым числом.")

    if value <= 0:
        raise WarningLifecycleError(f"{field_name} должен быть положительным числом.")

    return value


def _normalize_required_text(value: str, *, field_name: str) -> str:
    """Нормализует обязательную строку.

    Args:
        value: Сырое значение.
        field_name: Имя поля для текста ошибки.

    Returns:
        Строка без пробелов по краям.

    Raises:
        WarningLifecycleError: Если строка пустая.
    """
    if not isinstance(value, str):
        raise WarningLifecycleError(f"{field_name} должен быть строкой.")

    normalized = value.strip()
    if not normalized:
        raise WarningLifecycleError(f"{field_name} не может быть пустым.")

    return normalized


def _require_aware_datetime(value: datetime, *, field_name: str) -> datetime:
    """Проверяет timezone-aware datetime.

    Args:
        value: Значение datetime.
        field_name: Имя поля для текста ошибки.

    Returns:
        Проверенное значение datetime.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise WarningLifecycleError(f"{field_name} должен быть timezone-aware datetime.")

    return value


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.

    Raises:
        WarningLifecycleError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise WarningLifecycleError(f"{model_name} должен быть сохранён в БД.")


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "SqlAlchemyWarningLifecycleRepository",
    "WarningCancellationResult",
    "WarningExpirationResult",
    "WarningLifecycleError",
    "WarningLifecycleRepository",
    "WarningLifecycleService",
    "WarningNotFoundError",
]

```


## FILE: app/services/kick_candidates.py

```python
"""Сервис создания кандидатов на кик."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Clan, KickCandidate, TelegramUser, Warning
from app.domain import (
    KickCandidateReasonCode,
    KickCandidateStatus,
    WarningSource,
    WarningStatus,
    build_all_accounts_left_kick_event_key,
    build_linked_account_left_kick_event_key,
    build_two_impactful_warn_kick_event_key,
    build_unlinked_account_kick_event_key,
    normalize_player_tag,
)

_UNLINKED_ACCOUNT_MIN_AGE_DAYS = 3
_NOT_ENOUGH_IMPACTFUL_WARNINGS_REASON = "not_enough_impactful_warnings"
_UNLINKED_ACCOUNT_NOT_OLD_ENOUGH_REASON = "unlinked_account_not_old_enough"


class KickCandidateServiceError(RuntimeError):
    """Базовая ошибка сервиса кандидатов на кик."""


@dataclass(frozen=True, slots=True)
class KickCandidateCreationResult:
    """Результат создания кандидата на кик."""

    candidate: KickCandidate | None
    created: bool
    reason: str | None = None


class KickCandidateRepository(Protocol):
    """Repository contract для кандидатов на кик."""

    async def get_by_event_key(self, event_key: str) -> KickCandidate | None:
        """Возвращает кандидата по event key.

        Args:
            event_key: Идемпотентный ключ события.

        Returns:
            Кандидат или `None`.
        """

    def add(self, candidate: KickCandidate) -> None:
        """Добавляет кандидата в unit of work.

        Args:
            candidate: Новая модель кандидата.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyKickCandidateRepository:
    """SQLAlchemy-реализация repository для кандидатов на кик."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_event_key(self, event_key: str) -> KickCandidate | None:
        """Возвращает кандидата по event key.

        Args:
            event_key: Идемпотентный ключ события.

        Returns:
            Кандидат или `None`.
        """
        result = await self._session.execute(
            select(KickCandidate).where(KickCandidate.event_key == event_key)
        )
        return result.scalar_one_or_none()

    def add(self, candidate: KickCandidate) -> None:
        """Добавляет кандидата в текущую session.

        Args:
            candidate: Новая модель кандидата.
        """
        self._session.add(candidate)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class KickCandidateService:
    """Сервис создания кандидатов на кик.

    Сервис только создаёт idempotent candidates. Он не принимает решения,
    не исполняет удаление из Telegram и не отправляет уведомления.
    """

    def __init__(self, *, repository: KickCandidateRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository кандидатов.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "KickCandidateService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyKickCandidateRepository(session))

    async def create_for_two_impactful_warnings(
        self,
        *,
        telegram_user: TelegramUser,
        warnings: list[Warning],
        season_key: str,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата по двум active impactful system warn.

        Manual warn не учитываются, даже если caller ошибочно передал их в список.

        Args:
            telegram_user: TelegramUser, для которого проверяются warn.
            warnings: Warn-записи пользователя.
            season_key: Ключ сезона для идемпотентного candidate event key.

        Returns:
            Результат создания кандидата.
        """
        telegram_user_id = _required_model_id(telegram_user, model_name="TelegramUser")
        eligible_warnings = _filter_active_impactful_system_warnings(
            warnings,
            telegram_user_id=telegram_user_id,
        )

        if len(eligible_warnings) < 2:
            return KickCandidateCreationResult(
                candidate=None,
                created=False,
                reason=_NOT_ENOUGH_IMPACTFUL_WARNINGS_REASON,
            )

        event_key = build_two_impactful_warn_kick_event_key(
            telegram_user_id=telegram_user_id,
            season_key=season_key,
        )
        return await self._create_candidate(
            reason_code=KickCandidateReasonCode.TWO_IMPACTFUL_WARN,
            event_key=event_key,
            telegram_user=telegram_user,
        )

    async def create_for_unlinked_account(
        self,
        *,
        clan: Clan,
        player_tag: str,
        first_seen_at: datetime,
        observed_at: datetime | None = None,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата по непривязанному аккаунту старше 3 дней.

        Args:
            clan: Клан, где замечен непривязанный аккаунт.
            player_tag: Тег непривязанного аккаунта.
            first_seen_at: Первое появление аккаунта в API-составе.
            observed_at: Время текущей проверки. Если не передано, используется UTC now.

        Returns:
            Результат создания кандидата.
        """
        clan_id = _required_model_id(clan, model_name="Clan")
        normalized_player_tag = normalize_player_tag(player_tag)
        normalized_observed_at = observed_at or _utc_now()

        if not _is_at_least_days_old(
            first_seen_at=first_seen_at,
            observed_at=normalized_observed_at,
            days=_UNLINKED_ACCOUNT_MIN_AGE_DAYS,
        ):
            return KickCandidateCreationResult(
                candidate=None,
                created=False,
                reason=_UNLINKED_ACCOUNT_NOT_OLD_ENOUGH_REASON,
            )

        event_key = build_unlinked_account_kick_event_key(
            clan_id=clan_id,
            player_tag=normalized_player_tag,
        )
        return await self._create_candidate(
            reason_code=KickCandidateReasonCode.UNLINKED_AFTER_3_DAYS,
            event_key=event_key,
            player_tag=normalized_player_tag,
        )

    async def create_for_all_accounts_left(
        self,
        *,
        telegram_user: TelegramUser,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата по уходу всех аккаунтов TelegramUser.

        Args:
            telegram_user: TelegramUser, у которого все аккаунты покинули кланы.

        Returns:
            Результат создания кандидата.
        """
        telegram_user_id = _required_model_id(telegram_user, model_name="TelegramUser")
        event_key = build_all_accounts_left_kick_event_key(telegram_user_id=telegram_user_id)

        return await self._create_candidate(
            reason_code=KickCandidateReasonCode.ALL_ACCOUNTS_LEFT,
            event_key=event_key,
            telegram_user=telegram_user,
        )

    async def create_for_linked_account_left(
        self,
        *,
        telegram_user: TelegramUser,
        player_tag: str,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата по уходу одного привязанного аккаунта.

        Args:
            telegram_user: TelegramUser владельца аккаунта.
            player_tag: Тег ушедшего аккаунта.

        Returns:
            Результат создания кандидата.
        """
        telegram_user_id = _required_model_id(telegram_user, model_name="TelegramUser")
        normalized_player_tag = normalize_player_tag(player_tag)
        event_key = build_linked_account_left_kick_event_key(
            telegram_user_id=telegram_user_id,
            player_tag=normalized_player_tag,
        )

        return await self._create_candidate(
            reason_code=KickCandidateReasonCode.LINKED_ACCOUNT_LEFT,
            event_key=event_key,
            telegram_user=telegram_user,
            player_tag=normalized_player_tag,
        )

    async def create_manual_recommendation(
        self,
        *,
        telegram_user: TelegramUser | None = None,
        player_tag: str | None = None,
        created_by: TelegramUser | None = None,
        event_key: str | None = None,
    ) -> KickCandidateCreationResult:
        """Создаёт ручную рекомендацию на кик.

        По умолчанию manual recommendation создаётся без event key. Если caller
        явно передал event key, сервис выполняет dedup.

        Args:
            telegram_user: TelegramUser-кандидат, если известен.
            player_tag: Player tag-кандидат, если известен.
            created_by: Пользователь, создавший рекомендацию.
            event_key: Опциональный ключ дедупликации.

        Returns:
            Результат создания кандидата.

        Raises:
            KickCandidateServiceError: Если не передан ни TelegramUser, ни player tag.
        """
        if telegram_user is None and player_tag is None:
            raise KickCandidateServiceError(
                "Manual recommendation должна содержать telegram_user или player_tag."
            )

        return await self._create_candidate(
            reason_code=KickCandidateReasonCode.MANUAL_RECOMMENDATION,
            event_key=event_key,
            telegram_user=telegram_user,
            player_tag=player_tag,
            created_by=created_by,
        )

    async def _create_candidate(
        self,
        *,
        reason_code: KickCandidateReasonCode,
        event_key: str | None,
        telegram_user: TelegramUser | None = None,
        player_tag: str | None = None,
        created_by: TelegramUser | None = None,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата с общей логикой dedup.

        Args:
            reason_code: Причина кандидата.
            event_key: Event key для дедупликации.
            telegram_user: TelegramUser-кандидат.
            player_tag: Player tag-кандидат.
            created_by: Пользователь, создавший кандидата вручную или системно.

        Returns:
            Результат создания кандидата.
        """
        normalized_event_key = _normalize_optional_text(event_key)

        if normalized_event_key is not None:
            existing_candidate = await self._repository.get_by_event_key(normalized_event_key)
            if existing_candidate is not None:
                return KickCandidateCreationResult(candidate=existing_candidate, created=False)

        normalized_player_tag = normalize_player_tag(player_tag) if player_tag is not None else None
        candidate = KickCandidate(
            telegram_user_id=_optional_model_id(telegram_user, model_name="TelegramUser"),
            telegram_user=telegram_user,
            player_tag=normalized_player_tag,
            reason_code=reason_code.value,
            status=KickCandidateStatus.PENDING_ADMIN_DECISION.value,
            event_key=normalized_event_key,
            created_by=_optional_model_id(created_by, model_name="TelegramUser"),
            created_by_user=created_by,
        )

        self._repository.add(candidate)
        await self._repository.flush()

        return KickCandidateCreationResult(candidate=candidate, created=True)


def _filter_active_impactful_system_warnings(
    warnings: list[Warning],
    *,
    telegram_user_id: int,
) -> list[Warning]:
    """Фильтрует warn, которые влияют на кандидата по двум warn.

    Args:
        warnings: Warn-записи.
        telegram_user_id: DB ID TelegramUser.

    Returns:
        Active impactful system warn этого TelegramUser.
    """
    return [
        warning
        for warning in warnings
        if warning.telegram_user_id == telegram_user_id
        and warning.status == WarningStatus.ACTIVE.value
        and warning.is_impactful
        and warning.source == WarningSource.SYSTEM.value
    ]


def _is_at_least_days_old(
    *,
    first_seen_at: datetime,
    observed_at: datetime,
    days: int,
) -> bool:
    """Проверяет возраст непривязанного аккаунта.

    Args:
        first_seen_at: Первое появление аккаунта.
        observed_at: Время проверки.
        days: Минимальный возраст в днях.

    Returns:
        `True`, если аккаунт старше или равен порогу.

    Raises:
        KickCandidateServiceError: Если datetime не timezone-aware.
    """
    _validate_aware_datetime(first_seen_at, field_name="first_seen_at")
    _validate_aware_datetime(observed_at, field_name="observed_at")

    return observed_at - first_seen_at >= timedelta(days=days)


def _validate_aware_datetime(value: datetime, *, field_name: str) -> None:
    """Проверяет timezone-aware datetime.

    Args:
        value: Datetime-значение.
        field_name: Имя поля для текста ошибки.

    Raises:
        KickCandidateServiceError: Если datetime не содержит timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise KickCandidateServiceError(f"{field_name} должен быть timezone-aware datetime.")


def _normalize_optional_text(value: str | None) -> str | None:
    """Нормализует опциональную строку.

    Args:
        value: Сырое значение.

    Returns:
        Строка без пробелов по краям или `None`.
    """
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.

    Raises:
        KickCandidateServiceError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise KickCandidateServiceError(f"{model_name} должен быть сохранён в БД.")


def _optional_model_id(model: object | None, *, model_name: str) -> int | None:
    """Достаёт опциональный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model или `None`.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id или `None`.
    """
    if model is None:
        return None

    return _required_model_id(model, model_name=model_name)


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "KickCandidateCreationResult",
    "KickCandidateRepository",
    "KickCandidateService",
    "KickCandidateServiceError",
    "SqlAlchemyKickCandidateRepository",
]

```


## FILE: app/services/kick_candidate_decisions.py

```python
"""Сервис workflow-решений по кандидатам на кик."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KickCandidate, TelegramUser
from app.domain import KickCandidateStatus, require_domain_enum_value

_TERMINAL_STATUSES = frozenset(
    {
        KickCandidateStatus.REJECTED,
        KickCandidateStatus.EXECUTED,
    }
)

_ALLOWED_TRANSITIONS = {
    KickCandidateStatus.PENDING_ADMIN_DECISION: frozenset(
        {
            KickCandidateStatus.APPROVED,
            KickCandidateStatus.REJECTED,
            KickCandidateStatus.POSTPONED,
            KickCandidateStatus.MANUAL_REQUIRED,
        }
    ),
    KickCandidateStatus.POSTPONED: frozenset(
        {
            KickCandidateStatus.APPROVED,
            KickCandidateStatus.REJECTED,
            KickCandidateStatus.POSTPONED,
            KickCandidateStatus.MANUAL_REQUIRED,
        }
    ),
    KickCandidateStatus.APPROVED: frozenset({KickCandidateStatus.EXECUTED}),
    KickCandidateStatus.MANUAL_REQUIRED: frozenset({KickCandidateStatus.EXECUTED}),
}


class KickCandidateDecisionError(RuntimeError):
    """Базовая ошибка workflow-решений по кандидатам на кик."""


class KickCandidateNotFoundError(KickCandidateDecisionError):
    """Кандидат на кик не найден."""


class KickCandidateInvalidTransitionError(KickCandidateDecisionError):
    """Недопустимый переход статуса кандидата на кик."""


@dataclass(frozen=True, slots=True)
class KickCandidateDecisionResult:
    """Результат workflow-действия по кандидату на кик."""

    candidate: KickCandidate
    changed: bool


class KickCandidateDecisionRepository(Protocol):
    """Repository contract для workflow-решений по кандидатам."""

    async def get_by_id(self, candidate_id: int) -> KickCandidate | None:
        """Возвращает кандидата по DB ID.

        Args:
            candidate_id: DB ID кандидата.

        Returns:
            Кандидат или `None`.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyKickCandidateDecisionRepository:
    """SQLAlchemy-реализация repository для workflow-решений."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_id(self, candidate_id: int) -> KickCandidate | None:
        """Возвращает кандидата по DB ID.

        Args:
            candidate_id: DB ID кандидата.

        Returns:
            Кандидат или `None`.
        """
        result = await self._session.execute(
            select(KickCandidate).where(KickCandidate.id == candidate_id)
        )
        return result.scalar_one_or_none()

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class KickCandidateDecisionService:
    """Сервис workflow-решений по кандидатам на кик.

    Сервис меняет только поля decision workflow. Он не создаёт кандидатов,
    не удаляет их физически и не выполняет Telegram-действия.
    """

    def __init__(self, *, repository: KickCandidateDecisionRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository кандидатов на кик.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "KickCandidateDecisionService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyKickCandidateDecisionRepository(session))

    async def approve_candidate(
        self,
        *,
        candidate_id: int,
        decision_by: TelegramUser,
        comment: str | None = None,
    ) -> KickCandidateDecisionResult:
        """Подтверждает кандидата.

        Args:
            candidate_id: DB ID кандидата.
            decision_by: Пользователь, принявший решение.
            comment: Опциональный комментарий.

        Returns:
            Результат workflow-действия.
        """
        return await self._transition_candidate(
            candidate_id=candidate_id,
            target_status=KickCandidateStatus.APPROVED,
            decision_by=decision_by,
            comment=comment,
            comment_required=False,
            deadline_at=None,
            mark_executed=False,
        )

    async def reject_candidate(
        self,
        *,
        candidate_id: int,
        decision_by: TelegramUser,
        comment: str,
    ) -> KickCandidateDecisionResult:
        """Отклоняет кандидата.

        Args:
            candidate_id: DB ID кандидата.
            decision_by: Пользователь, принявший решение.
            comment: Обязательный комментарий.

        Returns:
            Результат workflow-действия.
        """
        return await self._transition_candidate(
            candidate_id=candidate_id,
            target_status=KickCandidateStatus.REJECTED,
            decision_by=decision_by,
            comment=comment,
            comment_required=True,
            deadline_at=None,
            mark_executed=False,
        )

    async def postpone_candidate(
        self,
        *,
        candidate_id: int,
        decision_by: TelegramUser,
        deadline_at: datetime,
        comment: str,
    ) -> KickCandidateDecisionResult:
        """Откладывает кандидата до конкретного срока.

        Args:
            candidate_id: DB ID кандидата.
            decision_by: Пользователь, принявший решение.
            deadline_at: Timezone-aware срок возврата к решению.
            comment: Обязательный комментарий.

        Returns:
            Результат workflow-действия.
        """
        return await self._transition_candidate(
            candidate_id=candidate_id,
            target_status=KickCandidateStatus.POSTPONED,
            decision_by=decision_by,
            comment=comment,
            comment_required=True,
            deadline_at=_validate_aware_datetime(deadline_at, field_name="deadline_at"),
            mark_executed=False,
        )

    async def mark_manual_required(
        self,
        *,
        candidate_id: int,
        decision_by: TelegramUser,
        comment: str | None = None,
    ) -> KickCandidateDecisionResult:
        """Помечает кандидата как требующего ручного действия.

        Args:
            candidate_id: DB ID кандидата.
            decision_by: Пользователь, принявший решение.
            comment: Опциональный комментарий.

        Returns:
            Результат workflow-действия.
        """
        return await self._transition_candidate(
            candidate_id=candidate_id,
            target_status=KickCandidateStatus.MANUAL_REQUIRED,
            decision_by=decision_by,
            comment=comment,
            comment_required=False,
            deadline_at=None,
            mark_executed=False,
        )

    async def mark_executed(
        self,
        *,
        candidate_id: int,
        decision_by: TelegramUser,
        comment: str | None = None,
    ) -> KickCandidateDecisionResult:
        """Помечает кандидата как исполненного.

        Args:
            candidate_id: DB ID кандидата.
            decision_by: Пользователь, отметивший выполнение.
            comment: Опциональный комментарий.

        Returns:
            Результат workflow-действия.
        """
        return await self._transition_candidate(
            candidate_id=candidate_id,
            target_status=KickCandidateStatus.EXECUTED,
            decision_by=decision_by,
            comment=comment,
            comment_required=False,
            deadline_at=None,
            mark_executed=True,
        )

    async def _transition_candidate(
        self,
        *,
        candidate_id: int,
        target_status: KickCandidateStatus,
        decision_by: TelegramUser,
        comment: str | None,
        comment_required: bool,
        deadline_at: datetime | None,
        mark_executed: bool,
    ) -> KickCandidateDecisionResult:
        """Выполняет валидированный переход статуса кандидата.

        Args:
            candidate_id: DB ID кандидата.
            target_status: Целевой статус.
            decision_by: Пользователь, принявший решение.
            comment: Комментарий решения.
            comment_required: Требуется ли непустой комментарий.
            deadline_at: Срок для postponed-статуса.
            mark_executed: Нужно ли заполнить `executed_at`.

        Returns:
            Результат workflow-действия.
        """
        normalized_candidate_id = _validate_positive_int(candidate_id, field_name="candidate_id")
        decision_by_id = _required_model_id(decision_by, model_name="TelegramUser")
        normalized_comment = _normalize_comment(comment, required=comment_required)
        candidate = await self._get_required_candidate(normalized_candidate_id)
        current_status = _normalize_status(candidate.status)

        _ensure_transition_allowed(current_status=current_status, target_status=target_status)

        decision_at = _utc_now()
        candidate.status = target_status.value
        candidate.decision_by_telegram_user_id = decision_by_id
        candidate.decision_by_user = decision_by
        candidate.decision_at = decision_at
        candidate.decision_comment = normalized_comment
        candidate.deadline_at = deadline_at

        if mark_executed:
            candidate.executed_at = decision_at

        await self._repository.flush()

        return KickCandidateDecisionResult(candidate=candidate, changed=True)

    async def _get_required_candidate(self, candidate_id: int) -> KickCandidate:
        """Возвращает кандидата или выбрасывает service error.

        Args:
            candidate_id: DB ID кандидата.

        Returns:
            Кандидат.

        Raises:
            KickCandidateNotFoundError: Если кандидат не найден.
        """
        candidate = await self._repository.get_by_id(candidate_id)
        if candidate is None:
            raise KickCandidateNotFoundError(f"Kick candidate {candidate_id} не найден.")

        return candidate


def _ensure_transition_allowed(
    *,
    current_status: KickCandidateStatus,
    target_status: KickCandidateStatus,
) -> None:
    """Проверяет допустимость перехода статуса.

    Args:
        current_status: Текущий статус кандидата.
        target_status: Целевой статус.

    Raises:
        KickCandidateInvalidTransitionError: Если переход запрещён.
    """
    if current_status in _TERMINAL_STATUSES:
        raise KickCandidateInvalidTransitionError(
            f"Нельзя изменить терминальный статус {current_status.value}."
        )

    allowed_targets = _ALLOWED_TRANSITIONS.get(current_status, frozenset())
    if target_status not in allowed_targets:
        raise KickCandidateInvalidTransitionError(
            f"Недопустимый переход {current_status.value} -> {target_status.value}."
        )


def _normalize_status(value: str) -> KickCandidateStatus:
    """Валидирует статус кандидата через доменный enum.

    Args:
        value: Строковый статус.

    Returns:
        Enum member `KickCandidateStatus`.
    """
    return require_domain_enum_value(
        KickCandidateStatus,
        value,
        field_name="candidate_status",
    )


def _normalize_comment(value: str | None, *, required: bool) -> str | None:
    """Нормализует комментарий решения.

    Args:
        value: Сырой комментарий.
        required: Требуется ли непустой комментарий.

    Returns:
        Нормализованный комментарий или `None`.

    Raises:
        KickCandidateDecisionError: Если комментарий обязателен, но пустой.
    """
    if value is None:
        if required:
            raise KickCandidateDecisionError("decision_comment не может быть пустым.")
        return None

    normalized = value.strip()
    if not normalized:
        if required:
            raise KickCandidateDecisionError("decision_comment не может быть пустым.")
        return None

    return normalized


def _validate_aware_datetime(value: datetime, *, field_name: str) -> datetime:
    """Проверяет timezone-aware datetime.

    Args:
        value: Datetime-значение.
        field_name: Имя поля для текста ошибки.

    Returns:
        Исходное datetime-значение.

    Raises:
        KickCandidateDecisionError: Если datetime не содержит timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise KickCandidateDecisionError(f"{field_name} должен быть timezone-aware datetime.")

    return value


def _validate_positive_int(value: int, *, field_name: str) -> int:
    """Проверяет положительное целое число.

    Args:
        value: Проверяемое значение.
        field_name: Имя поля для текста ошибки.

    Returns:
        Проверенное значение.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise KickCandidateDecisionError(f"{field_name} должен быть целым числом.")

    if value <= 0:
        raise KickCandidateDecisionError(f"{field_name} должен быть положительным числом.")

    return value


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.

    Raises:
        KickCandidateDecisionError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise KickCandidateDecisionError(f"{model_name} должен быть сохранён в БД.")


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "KickCandidateDecisionError",
    "KickCandidateDecisionRepository",
    "KickCandidateDecisionResult",
    "KickCandidateDecisionService",
    "KickCandidateInvalidTransitionError",
    "KickCandidateNotFoundError",
    "SqlAlchemyKickCandidateDecisionRepository",
]

```


## FILE: app/services/notification_routes.py

```python
"""Сервис маршрутов Telegram-уведомлений."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Clan, NotificationRoute, TelegramChat, TelegramUser
from app.domain import (
    NotificationType,
    normalize_clan_tag,
    normalize_message_thread_id,
    require_domain_enum_value,
)


class NotificationRouteServiceError(RuntimeError):
    """Базовая ошибка сервиса маршрутов уведомлений."""


class NotificationRouteClanNotFoundError(NotificationRouteServiceError):
    """Клан для маршрута уведомлений не найден."""


class NotificationRouteNotFoundError(NotificationRouteServiceError):
    """Маршрут уведомлений не найден."""


@dataclass(frozen=True, slots=True)
class NotificationRouteRegistrationResult:
    """Результат регистрации маршрута уведомлений."""

    route: NotificationRoute
    chat: TelegramChat
    created: bool


@dataclass(frozen=True, slots=True)
class NotificationRouteStateResult:
    """Результат включения или отключения маршрута."""

    route: NotificationRoute
    changed: bool


@dataclass(frozen=True, slots=True)
class NotificationRouteMutationResult:
    """Совместимый alias результата изменения состояния route."""

    route: NotificationRoute
    changed: bool


class NotificationRouteRepository(Protocol):
    """Repository contract для маршрутов уведомлений."""

    async def get_clan_by_tag(self, clan_tag: str) -> Clan | None:
        """Возвращает клан по нормализованному тегу.

        Args:
            clan_tag: Нормализованный тег клана.

        Returns:
            Клан или `None`.
        """

    async def get_chat_by_id(self, chat_id: int) -> TelegramChat | None:
        """Возвращает TelegramChat по chat_id.

        Args:
            chat_id: Telegram chat id.

        Returns:
            TelegramChat или `None`.
        """

    def add_chat(self, chat: TelegramChat) -> None:
        """Добавляет TelegramChat в unit of work.

        Args:
            chat: Новая модель Telegram-чата.
        """

    async def get_route(
        self,
        *,
        clan_id: int,
        notification_type: str,
        chat_id: int,
        message_thread_id: int | None,
    ) -> NotificationRoute | None:
        """Возвращает route по unique key.

        Args:
            clan_id: DB ID клана.
            notification_type: Тип уведомления.
            chat_id: Telegram chat id.
            message_thread_id: Telegram topic/thread id или `None`.

        Returns:
            Route или `None`.
        """

    async def get_route_by_id(self, route_id: int) -> NotificationRoute | None:
        """Возвращает route по DB ID.

        Args:
            route_id: DB ID маршрута.

        Returns:
            Route или `None`.
        """

    async def list_all_routes(self, *, include_disabled: bool) -> tuple[NotificationRoute, ...]:
        """Возвращает все маршруты уведомлений.

        Args:
            include_disabled: Возвращать ли disabled routes.

        Returns:
            Tuple маршрутов уведомлений.
        """

    async def list_routes_by_clan_and_type(
        self,
        *,
        clan_id: int,
        notification_type: str,
        include_disabled: bool,
    ) -> list[NotificationRoute]:
        """Возвращает routes по клану и типу уведомления.

        Args:
            clan_id: DB ID клана.
            notification_type: Тип уведомления.
            include_disabled: Возвращать ли disabled routes.

        Returns:
            Список маршрутов.
        """

    def add_route(self, route: NotificationRoute) -> None:
        """Добавляет route в unit of work.

        Args:
            route: Новая модель маршрута.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyNotificationRouteRepository:
    """SQLAlchemy-реализация repository для маршрутов уведомлений."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_clan_by_tag(self, clan_tag: str) -> Clan | None:
        """Возвращает клан по нормализованному тегу."""
        result = await self._session.execute(select(Clan).where(Clan.tag == clan_tag))
        return result.scalar_one_or_none()

    async def get_chat_by_id(self, chat_id: int) -> TelegramChat | None:
        """Возвращает TelegramChat по chat_id."""
        result = await self._session.execute(
            select(TelegramChat).where(TelegramChat.chat_id == chat_id)
        )
        return result.scalar_one_or_none()

    def add_chat(self, chat: TelegramChat) -> None:
        """Добавляет TelegramChat в текущую session."""
        self._session.add(chat)

    async def get_route(
        self,
        *,
        clan_id: int,
        notification_type: str,
        chat_id: int,
        message_thread_id: int | None,
    ) -> NotificationRoute | None:
        """Возвращает route по unique key."""
        query = select(NotificationRoute).where(
            NotificationRoute.clan_id == clan_id,
            NotificationRoute.notification_type == notification_type,
            NotificationRoute.chat_id == chat_id,
        )

        if message_thread_id is None:
            query = query.where(NotificationRoute.message_thread_id.is_(None))
        else:
            query = query.where(NotificationRoute.message_thread_id == message_thread_id)

        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def get_route_by_id(self, route_id: int) -> NotificationRoute | None:
        """Возвращает route по DB ID."""
        result = await self._session.execute(
            select(NotificationRoute)
            .options(
                selectinload(NotificationRoute.clan),
                selectinload(NotificationRoute.chat),
            )
            .where(NotificationRoute.id == route_id)
        )
        return result.scalar_one_or_none()

    async def list_all_routes(self, *, include_disabled: bool) -> tuple[NotificationRoute, ...]:
        """Возвращает все маршруты уведомлений в стабильном порядке."""
        query = select(NotificationRoute).options(
            selectinload(NotificationRoute.clan),
            selectinload(NotificationRoute.chat),
        )

        if not include_disabled:
            query = query.where(NotificationRoute.enabled.is_(True))

        query = query.order_by(
            NotificationRoute.clan_id,
            NotificationRoute.notification_type,
            NotificationRoute.chat_id,
            NotificationRoute.message_thread_id,
        )
        result = await self._session.execute(query)
        return tuple(result.scalars().all())

    async def list_routes_by_clan_and_type(
        self,
        *,
        clan_id: int,
        notification_type: str,
        include_disabled: bool,
    ) -> list[NotificationRoute]:
        """Возвращает routes по клану и типу уведомления."""
        query = select(NotificationRoute).where(
            NotificationRoute.clan_id == clan_id,
            NotificationRoute.notification_type == notification_type,
        )

        if not include_disabled:
            query = query.where(NotificationRoute.enabled.is_(True))

        result = await self._session.execute(query)
        return list(result.scalars().all())

    def add_route(self, route: NotificationRoute) -> None:
        """Добавляет route в текущую session."""
        self._session.add(route)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class NotificationRouteService:
    """Сервис управления маршрутами уведомлений.

    Сервис регистрирует Telegram chat/topic routes, upsert-ит TelegramChat и
    обеспечивает идемпотентную регистрацию по unique key маршрута.
    """

    def __init__(self, *, repository: NotificationRouteRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository маршрутов уведомлений.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "NotificationRouteService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyNotificationRouteRepository(session))

    async def register_route(
        self,
        *,
        clan_tag: str,
        notification_type: NotificationType | str,
        chat_id: int,
        message_thread_id: int | None,
        title: str,
        created_by: TelegramUser | None = None,
        chat_type: str = "supergroup",
        is_forum: bool = False,
    ) -> NotificationRouteRegistrationResult:
        """Регистрирует или повторно включает route уведомлений.

        Args:
            clan_tag: Тег клана.
            notification_type: Тип уведомления.
            chat_id: Telegram chat id.
            message_thread_id: Telegram topic/thread id или `None`.
            title: Название Telegram-чата.
            created_by: TelegramUser администратора, если известен.
            chat_type: Тип Telegram-чата.
            is_forum: Является ли чат форумом с topics.

        Returns:
            Результат регистрации route.

        Raises:
            NotificationRouteClanNotFoundError: Если клан не найден.
        """
        clan = await self._get_required_clan(clan_tag)
        normalized_notification_type = _normalize_notification_type(notification_type)
        normalized_chat_id = _validate_chat_id(chat_id)
        normalized_thread_id = normalize_message_thread_id(message_thread_id)
        chat = await self._upsert_chat(
            chat_id=normalized_chat_id,
            title=title,
            chat_type=chat_type,
            is_forum=is_forum,
        )

        existing_route = await self._repository.get_route(
            clan_id=_required_model_id(clan, model_name="Clan"),
            notification_type=normalized_notification_type.value,
            chat_id=normalized_chat_id,
            message_thread_id=normalized_thread_id,
        )
        created_by_id = _optional_model_id(created_by, model_name="TelegramUser")

        if existing_route is not None:
            existing_route.enabled = True
            existing_route.created_by_telegram_user_id = created_by_id
            existing_route.created_by_user = created_by
            existing_route.chat = chat

            await self._repository.flush()

            return NotificationRouteRegistrationResult(
                route=existing_route,
                chat=chat,
                created=False,
            )

        route = NotificationRoute(
            clan_id=_required_model_id(clan, model_name="Clan"),
            clan=clan,
            notification_type=normalized_notification_type.value,
            chat_id=normalized_chat_id,
            chat=chat,
            message_thread_id=normalized_thread_id,
            enabled=True,
            created_by_telegram_user_id=created_by_id,
            created_by_user=created_by,
        )
        self._repository.add_route(route)
        await self._repository.flush()

        return NotificationRouteRegistrationResult(route=route, chat=chat, created=True)

    async def list_routes(
        self,
        *,
        clan_tag: str,
        notification_type: NotificationType | str,
        include_disabled: bool = False,
    ) -> list[NotificationRoute]:
        """Возвращает routes по клану и типу уведомления.

        По умолчанию возвращаются только enabled routes, чтобы sender не мог
        случайно отправить уведомление в отключённый маршрут.

        Args:
            clan_tag: Тег клана.
            notification_type: Тип уведомления.
            include_disabled: Вернуть ли disabled routes.

        Returns:
            Список маршрутов.
        """
        clan = await self._get_required_clan(clan_tag)
        normalized_notification_type = _normalize_notification_type(notification_type)

        return await self._repository.list_routes_by_clan_and_type(
            clan_id=_required_model_id(clan, model_name="Clan"),
            notification_type=normalized_notification_type.value,
            include_disabled=include_disabled,
        )

    async def list_all_routes(
        self,
        *,
        include_disabled: bool = True,
    ) -> tuple[NotificationRoute, ...]:
        """Возвращает все notification routes для admin UI.

        Args:
            include_disabled: Возвращать ли disabled routes.

        Returns:
            Tuple маршрутов уведомлений.
        """
        return await self._repository.list_all_routes(include_disabled=include_disabled)

    async def get_route_by_id(self, *, route_id: int) -> NotificationRoute:
        """Возвращает route по DB ID.

        Args:
            route_id: DB ID маршрута.

        Returns:
            Route уведомлений.

        Raises:
            NotificationRouteNotFoundError: Если route не найден.
        """
        return await self._get_required_route(route_id)

    async def disable_route(self, *, route_id: int) -> NotificationRouteStateResult:
        """Отключает route уведомлений.

        Args:
            route_id: DB ID маршрута.

        Returns:
            Результат отключения.
        """
        route = await self._get_required_route(route_id)
        if not route.enabled:
            return NotificationRouteStateResult(route=route, changed=False)

        route.enabled = False
        await self._repository.flush()

        return NotificationRouteStateResult(route=route, changed=True)

    async def enable_route(self, *, route_id: int) -> NotificationRouteStateResult:
        """Включает route уведомлений.

        Args:
            route_id: DB ID маршрута.

        Returns:
            Результат включения.
        """
        route = await self._get_required_route(route_id)
        if route.enabled:
            return NotificationRouteStateResult(route=route, changed=False)

        route.enabled = True
        await self._repository.flush()

        return NotificationRouteStateResult(route=route, changed=True)

    async def _upsert_chat(
        self,
        *,
        chat_id: int,
        title: str,
        chat_type: str,
        is_forum: bool,
    ) -> TelegramChat:
        """Создаёт или обновляет TelegramChat.

        Args:
            chat_id: Telegram chat id.
            title: Название Telegram-чата.
            chat_type: Тип Telegram-чата.
            is_forum: Является ли чат форумом.

        Returns:
            TelegramChat.
        """
        normalized_title = _normalize_required_text(title, field_name="title", max_length=255)
        normalized_type = _normalize_required_text(chat_type, field_name="chat_type", max_length=64)

        chat = await self._repository.get_chat_by_id(chat_id)
        if chat is None:
            chat = TelegramChat(
                chat_id=chat_id,
                title=normalized_title,
                type=normalized_type,
                is_forum=is_forum,
            )
            self._repository.add_chat(chat)
        else:
            chat.title = normalized_title
            chat.type = normalized_type
            chat.is_forum = is_forum

        return chat

    async def _get_required_clan(self, clan_tag: str) -> Clan:
        """Возвращает клан или выбрасывает service error.

        Args:
            clan_tag: Тег клана.

        Returns:
            Клан.

        Raises:
            NotificationRouteClanNotFoundError: Если клан не найден.
        """
        normalized_clan_tag = normalize_clan_tag(clan_tag)
        clan = await self._repository.get_clan_by_tag(normalized_clan_tag)
        if clan is None:
            raise NotificationRouteClanNotFoundError(f"Клан {normalized_clan_tag} не найден.")

        return clan

    async def _get_required_route(self, route_id: int) -> NotificationRoute:
        """Возвращает route или выбрасывает service error.

        Args:
            route_id: DB ID route.

        Returns:
            Route.

        Raises:
            NotificationRouteNotFoundError: Если route не найден.
        """
        normalized_route_id = _validate_positive_int(route_id, field_name="route_id")
        route = await self._repository.get_route_by_id(normalized_route_id)
        if route is None:
            raise NotificationRouteNotFoundError(f"Notification route {route_id} не найден.")

        return route


def _normalize_notification_type(value: NotificationType | str) -> NotificationType:
    """Валидирует notification type через доменный enum.

    Args:
        value: Тип уведомления.

    Returns:
        Enum member `NotificationType`.
    """
    return require_domain_enum_value(NotificationType, value, field_name="notification_type")


def _validate_chat_id(value: int) -> int:
    """Проверяет Telegram chat id.

    Telegram group/supergroup chat id может быть отрицательным, поэтому
    запрещаем только `0`, bool и не-int значения.

    Args:
        value: Telegram chat id.

    Returns:
        Проверенный chat id.

    Raises:
        NotificationRouteServiceError: Если chat id некорректный.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise NotificationRouteServiceError("chat_id должен быть целым числом.")

    if value == 0:
        raise NotificationRouteServiceError("chat_id не может быть 0.")

    return value


def _validate_positive_int(value: int, *, field_name: str) -> int:
    """Проверяет положительный integer.

    Args:
        value: Проверяемое значение.
        field_name: Название поля для текста ошибки.

    Returns:
        Проверенное значение.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise NotificationRouteServiceError(f"{field_name} должен быть целым числом.")

    if value <= 0:
        raise NotificationRouteServiceError(f"{field_name} должен быть положительным числом.")

    return value


def _normalize_required_text(value: str, *, field_name: str, max_length: int) -> str:
    """Нормализует обязательную строку.

    Args:
        value: Сырое значение.
        field_name: Имя поля для текста ошибки.
        max_length: Максимальная длина.

    Returns:
        Нормализованная строка.

    Raises:
        NotificationRouteServiceError: Если строка пустая.
    """
    if not isinstance(value, str):
        raise NotificationRouteServiceError(f"{field_name} должен быть строкой.")

    normalized = value.strip()
    if not normalized:
        raise NotificationRouteServiceError(f"{field_name} не может быть пустым.")

    return normalized[:max_length]


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.

    Raises:
        NotificationRouteServiceError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise NotificationRouteServiceError(f"{model_name} должен быть сохранён в БД.")


def _optional_model_id(model: object | None, *, model_name: str) -> int | None:
    """Достаёт опциональный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model или `None`.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id или `None`.
    """
    if model is None:
        return None

    return _required_model_id(model, model_name=model_name)


__all__ = [
    "NotificationRouteClanNotFoundError",
    "NotificationRouteMutationResult",
    "NotificationRouteNotFoundError",
    "NotificationRouteRegistrationResult",
    "NotificationRouteRepository",
    "NotificationRouteService",
    "NotificationRouteServiceError",
    "NotificationRouteStateResult",
    "SqlAlchemyNotificationRouteRepository",
]

```


## FILE: app/services/notification_logs.py

```python
"""Сервис логирования Telegram-уведомлений."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import NotificationLog, NotificationRoute
from app.domain import NotificationType, normalize_message_thread_id, require_domain_enum_value

_NOTIFICATION_STATUS_SENT = "sent"
_NOTIFICATION_STATUS_FAILED = "failed"
_NOTIFICATION_STATUS_SKIPPED = "skipped"
_ALREADY_SENT_REASON = "already_sent"
_EVENT_KEY_MAX_LENGTH = 255
_PAYLOAD_SUMMARY_MAX_LENGTH = 2048
_ERROR_TEXT_MAX_LENGTH = 2048
_SENSITIVE_PAYLOAD_MARKERS = frozenset(
    {
        "token",
        "bearer",
        "authorization",
        "api_key",
        "password",
    }
)


class NotificationLogServiceError(RuntimeError):
    """Базовая ошибка сервиса логирования уведомлений."""


@dataclass(frozen=True, slots=True)
class NotificationLogResult:
    """Результат записи notification log."""

    log: NotificationLog
    created: bool
    updated: bool
    skipped: bool = False
    reason: str | None = None


class NotificationLogRepository(Protocol):
    """Repository contract для notification logs."""

    async def get_by_event_key(self, event_key: str) -> NotificationLog | None:
        """Возвращает notification log по event key.

        Args:
            event_key: Идемпотентный ключ уведомления.

        Returns:
            NotificationLog или `None`.
        """

    def add(self, notification_log: NotificationLog) -> None:
        """Добавляет notification log в unit of work.

        Args:
            notification_log: Новая модель лога.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyNotificationLogRepository:
    """SQLAlchemy-реализация repository для notification logs."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_event_key(self, event_key: str) -> NotificationLog | None:
        """Возвращает notification log по event key.

        Args:
            event_key: Идемпотентный ключ уведомления.

        Returns:
            NotificationLog или `None`.
        """
        result = await self._session.execute(
            select(NotificationLog).where(NotificationLog.event_key == event_key)
        )
        return result.scalar_one_or_none()

    def add(self, notification_log: NotificationLog) -> None:
        """Добавляет notification log в текущую session.

        Args:
            notification_log: Новая модель лога.
        """
        self._session.add(notification_log)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class NotificationLogService:
    """Сервис логирования результатов отправки Telegram-уведомлений.

    Сервис не отправляет сообщения в Telegram. Он фиксирует состояние отправки,
    защищает `payload_summary` от очевидных чувствительных данных и выполняет
    dedup по `event_key`.
    """

    def __init__(self, *, repository: NotificationLogRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository notification logs.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "NotificationLogService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyNotificationLogRepository(session))

    async def has_sent(self, *, event_key: str) -> bool:
        """Проверяет, был ли event уже успешно отправлен.

        Args:
            event_key: Идемпотентный ключ уведомления.

        Returns:
            `True`, если по ключу уже есть log со статусом `sent`.
        """
        normalized_event_key = _normalize_required_event_key(event_key)
        existing_log = await self._repository.get_by_event_key(normalized_event_key)

        return existing_log is not None and existing_log.status == _NOTIFICATION_STATUS_SENT

    async def record_sent(
        self,
        *,
        notification_type: NotificationType | str,
        payload_summary: str,
        telegram_message_id: int,
        event_key: str | None = None,
        route: NotificationRoute | None = None,
        chat_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> NotificationLogResult:
        """Фиксирует успешную отправку уведомления.

        Если по `event_key` уже есть failed log, он обновляется до `sent`.
        Если по `event_key` уже есть sent log, повторная запись пропускается.

        Args:
            notification_type: Тип уведомления.
            payload_summary: Безопасное краткое описание payload.
            telegram_message_id: ID сообщения Telegram.
            event_key: Идемпотентный ключ уведомления.
            route: Route, через который отправлялось уведомление.
            chat_id: Telegram chat id, если route не передан.
            message_thread_id: Topic/thread id, если route не передан.

        Returns:
            Результат записи лога.
        """
        normalized_event_key = _normalize_optional_event_key(event_key)
        normalized_summary = _normalize_payload_summary(payload_summary)
        normalized_message_id = _validate_positive_int(
            telegram_message_id,
            field_name="telegram_message_id",
        )
        target = _resolve_delivery_target(
            route=route,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
        )

        if normalized_event_key is not None:
            existing_log = await self._repository.get_by_event_key(normalized_event_key)
            if existing_log is not None:
                if existing_log.status == _NOTIFICATION_STATUS_SENT:
                    return NotificationLogResult(
                        log=existing_log,
                        created=False,
                        updated=False,
                        skipped=True,
                        reason=_ALREADY_SENT_REASON,
                    )

                _apply_log_values(
                    existing_log,
                    route=route,
                    notification_type=notification_type,
                    event_key=normalized_event_key,
                    chat_id=target.chat_id,
                    message_thread_id=target.message_thread_id,
                    status=_NOTIFICATION_STATUS_SENT,
                    telegram_message_id=normalized_message_id,
                    payload_summary=normalized_summary,
                    error_text=None,
                )
                await self._repository.flush()

                return NotificationLogResult(
                    log=existing_log,
                    created=False,
                    updated=True,
                )

        notification_log = _build_notification_log(
            route=route,
            notification_type=notification_type,
            event_key=normalized_event_key,
            chat_id=target.chat_id,
            message_thread_id=target.message_thread_id,
            status=_NOTIFICATION_STATUS_SENT,
            telegram_message_id=normalized_message_id,
            payload_summary=normalized_summary,
            error_text=None,
        )
        self._repository.add(notification_log)
        await self._repository.flush()

        return NotificationLogResult(
            log=notification_log,
            created=True,
            updated=False,
        )

    async def record_failed(
        self,
        *,
        notification_type: NotificationType | str,
        payload_summary: str,
        error_text: str,
        event_key: str | None = None,
        route: NotificationRoute | None = None,
        chat_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> NotificationLogResult:
        """Фиксирует ошибку отправки уведомления.

        Failed log с `event_key` может быть позже обновлён успешной отправкой
        через `record_sent`.

        Args:
            notification_type: Тип уведомления.
            payload_summary: Безопасное краткое описание payload.
            error_text: Текст ошибки отправки.
            event_key: Идемпотентный ключ уведомления.
            route: Route, через который отправлялось уведомление.
            chat_id: Telegram chat id, если route не передан.
            message_thread_id: Topic/thread id, если route не передан.

        Returns:
            Результат записи лога.
        """
        normalized_event_key = _normalize_optional_event_key(event_key)
        normalized_summary = _normalize_payload_summary(payload_summary)
        normalized_error = _normalize_required_text(
            error_text,
            field_name="error_text",
            max_length=_ERROR_TEXT_MAX_LENGTH,
        )
        target = _resolve_delivery_target(
            route=route,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
        )

        if normalized_event_key is not None:
            existing_log = await self._repository.get_by_event_key(normalized_event_key)
            if existing_log is not None:
                if existing_log.status == _NOTIFICATION_STATUS_SENT:
                    return NotificationLogResult(
                        log=existing_log,
                        created=False,
                        updated=False,
                        skipped=True,
                        reason=_ALREADY_SENT_REASON,
                    )

                _apply_log_values(
                    existing_log,
                    route=route,
                    notification_type=notification_type,
                    event_key=normalized_event_key,
                    chat_id=target.chat_id,
                    message_thread_id=target.message_thread_id,
                    status=_NOTIFICATION_STATUS_FAILED,
                    telegram_message_id=None,
                    payload_summary=normalized_summary,
                    error_text=normalized_error,
                )
                await self._repository.flush()

                return NotificationLogResult(
                    log=existing_log,
                    created=False,
                    updated=True,
                )

        notification_log = _build_notification_log(
            route=route,
            notification_type=notification_type,
            event_key=normalized_event_key,
            chat_id=target.chat_id,
            message_thread_id=target.message_thread_id,
            status=_NOTIFICATION_STATUS_FAILED,
            telegram_message_id=None,
            payload_summary=normalized_summary,
            error_text=normalized_error,
        )
        self._repository.add(notification_log)
        await self._repository.flush()

        return NotificationLogResult(
            log=notification_log,
            created=True,
            updated=False,
        )

    async def record_skipped(
        self,
        *,
        notification_type: NotificationType | str,
        payload_summary: str,
        event_key: str | None = None,
        route: NotificationRoute | None = None,
        chat_id: int | None = None,
        message_thread_id: int | None = None,
        reason: str | None = None,
    ) -> NotificationLogResult:
        """Фиксирует осознанно пропущенное уведомление.

        Args:
            notification_type: Тип уведомления.
            payload_summary: Безопасное краткое описание payload.
            event_key: Идемпотентный ключ уведомления.
            route: Route, через который уведомление должно было уйти.
            chat_id: Telegram chat id, если route не передан.
            message_thread_id: Topic/thread id, если route не передан.
            reason: Причина пропуска.

        Returns:
            Результат записи лога.
        """
        normalized_event_key = _normalize_optional_event_key(event_key)
        normalized_summary = _normalize_payload_summary(payload_summary)
        normalized_reason = _normalize_optional_text(reason, max_length=_ERROR_TEXT_MAX_LENGTH)
        target = _resolve_delivery_target(
            route=route,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
        )

        if normalized_event_key is not None:
            existing_log = await self._repository.get_by_event_key(normalized_event_key)
            if existing_log is not None:
                if existing_log.status == _NOTIFICATION_STATUS_SENT:
                    return NotificationLogResult(
                        log=existing_log,
                        created=False,
                        updated=False,
                        skipped=True,
                        reason=_ALREADY_SENT_REASON,
                    )

                _apply_log_values(
                    existing_log,
                    route=route,
                    notification_type=notification_type,
                    event_key=normalized_event_key,
                    chat_id=target.chat_id,
                    message_thread_id=target.message_thread_id,
                    status=_NOTIFICATION_STATUS_SKIPPED,
                    telegram_message_id=None,
                    payload_summary=normalized_summary,
                    error_text=normalized_reason,
                )
                await self._repository.flush()

                return NotificationLogResult(
                    log=existing_log,
                    created=False,
                    updated=True,
                )

        notification_log = _build_notification_log(
            route=route,
            notification_type=notification_type,
            event_key=normalized_event_key,
            chat_id=target.chat_id,
            message_thread_id=target.message_thread_id,
            status=_NOTIFICATION_STATUS_SKIPPED,
            telegram_message_id=None,
            payload_summary=normalized_summary,
            error_text=normalized_reason,
        )
        self._repository.add(notification_log)
        await self._repository.flush()

        return NotificationLogResult(
            log=notification_log,
            created=True,
            updated=False,
        )


@dataclass(frozen=True, slots=True)
class _DeliveryTarget:
    """Нормализованная цель доставки уведомления."""

    chat_id: int
    message_thread_id: int | None


def _build_notification_log(
    *,
    route: NotificationRoute | None,
    notification_type: NotificationType | str,
    event_key: str | None,
    chat_id: int,
    message_thread_id: int | None,
    status: str,
    telegram_message_id: int | None,
    payload_summary: str,
    error_text: str | None,
) -> NotificationLog:
    """Создаёт модель NotificationLog.

    Args:
        route: Route, связанный с отправкой.
        notification_type: Тип уведомления.
        event_key: Идемпотентный ключ.
        chat_id: Telegram chat id.
        message_thread_id: Telegram topic/thread id.
        status: Статус отправки.
        telegram_message_id: ID Telegram message для successful send.
        payload_summary: Безопасное краткое описание payload.
        error_text: Текст ошибки или причина пропуска.

    Returns:
        Новая модель NotificationLog.
    """
    notification_log = NotificationLog()
    _apply_log_values(
        notification_log,
        route=route,
        notification_type=notification_type,
        event_key=event_key,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        status=status,
        telegram_message_id=telegram_message_id,
        payload_summary=payload_summary,
        error_text=error_text,
    )

    return notification_log


def _apply_log_values(
    notification_log: NotificationLog,
    *,
    route: NotificationRoute | None,
    notification_type: NotificationType | str,
    event_key: str | None,
    chat_id: int,
    message_thread_id: int | None,
    status: str,
    telegram_message_id: int | None,
    payload_summary: str,
    error_text: str | None,
) -> None:
    """Применяет значения к модели NotificationLog.

    Args:
        notification_log: Модель лога.
        route: Route, связанный с отправкой.
        notification_type: Тип уведомления.
        event_key: Идемпотентный ключ.
        chat_id: Telegram chat id.
        message_thread_id: Telegram topic/thread id.
        status: Статус отправки.
        telegram_message_id: ID Telegram message для successful send.
        payload_summary: Безопасное краткое описание payload.
        error_text: Текст ошибки или причина пропуска.
    """
    notification_log.route_id = _optional_model_id(route)
    notification_log.route = route
    notification_log.notification_type = _normalize_notification_type(notification_type).value
    notification_log.event_key = event_key
    notification_log.chat_id = chat_id
    notification_log.message_thread_id = message_thread_id
    notification_log.status = status
    notification_log.telegram_message_id = telegram_message_id
    notification_log.payload_summary = payload_summary
    notification_log.error_text = error_text


def _resolve_delivery_target(
    *,
    route: NotificationRoute | None,
    chat_id: int | None,
    message_thread_id: int | None,
) -> _DeliveryTarget:
    """Определяет цель доставки по route или явным аргументам.

    Args:
        route: Route уведомления.
        chat_id: Telegram chat id, если route не передан.
        message_thread_id: Telegram topic/thread id, если route не передан.

    Returns:
        Нормализованная цель доставки.

    Raises:
        NotificationLogServiceError: Если chat id нельзя определить.
    """
    if route is not None:
        return _DeliveryTarget(
            chat_id=_validate_chat_id(route.chat_id),
            message_thread_id=normalize_message_thread_id(route.message_thread_id),
        )

    if chat_id is None:
        raise NotificationLogServiceError("chat_id обязателен, если route не передан.")

    return _DeliveryTarget(
        chat_id=_validate_chat_id(chat_id),
        message_thread_id=normalize_message_thread_id(message_thread_id),
    )


def _normalize_notification_type(value: NotificationType | str) -> NotificationType:
    """Валидирует notification type через доменный enum.

    Args:
        value: Тип уведомления.

    Returns:
        Enum member `NotificationType`.
    """
    return require_domain_enum_value(NotificationType, value, field_name="notification_type")


def _normalize_payload_summary(value: str) -> str:
    """Нормализует payload summary и проверяет sensitive markers.

    Args:
        value: Краткое описание payload.

    Returns:
        Обрезанное краткое описание.

    Raises:
        NotificationLogServiceError: Если summary пустой или похож на sensitive data.
    """
    normalized = _normalize_required_text(
        value,
        field_name="payload_summary",
        max_length=_PAYLOAD_SUMMARY_MAX_LENGTH,
    )
    lowered = normalized.lower()

    for marker in _SENSITIVE_PAYLOAD_MARKERS:
        if marker in lowered:
            raise NotificationLogServiceError(
                f"payload_summary содержит чувствительный маркер: {marker}."
            )

    return normalized


def _normalize_required_event_key(value: str) -> str:
    """Нормализует обязательный event key.

    Args:
        value: Event key.

    Returns:
        Нормализованный event key.
    """
    normalized = _normalize_optional_event_key(value)
    if normalized is None:
        raise NotificationLogServiceError("event_key не может быть пустым.")

    return normalized


def _normalize_optional_event_key(value: str | None) -> str | None:
    """Нормализует опциональный event key.

    Args:
        value: Event key или `None`.

    Returns:
        Нормализованный event key или `None`.

    Raises:
        NotificationLogServiceError: Если event key слишком длинный.
    """
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    if len(normalized) > _EVENT_KEY_MAX_LENGTH:
        raise NotificationLogServiceError("event_key слишком длинный.")

    return normalized


def _normalize_required_text(value: str, *, field_name: str, max_length: int) -> str:
    """Нормализует обязательную строку.

    Args:
        value: Сырое значение.
        field_name: Имя поля для текста ошибки.
        max_length: Максимальная длина.

    Returns:
        Нормализованная строка.

    Raises:
        NotificationLogServiceError: Если строка пустая.
    """
    if not isinstance(value, str):
        raise NotificationLogServiceError(f"{field_name} должен быть строкой.")

    normalized = value.strip()
    if not normalized:
        raise NotificationLogServiceError(f"{field_name} не может быть пустым.")

    return normalized[:max_length]


def _normalize_optional_text(value: str | None, *, max_length: int) -> str | None:
    """Нормализует опциональную строку.

    Args:
        value: Сырое значение.
        max_length: Максимальная длина.

    Returns:
        Нормализованная строка или `None`.
    """
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    return normalized[:max_length]


def _validate_chat_id(value: int) -> int:
    """Проверяет Telegram chat id.

    Telegram group/supergroup chat id может быть отрицательным, поэтому
    запрещаем только `0`, bool и не-int значения.

    Args:
        value: Telegram chat id.

    Returns:
        Проверенный chat id.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise NotificationLogServiceError("chat_id должен быть целым числом.")

    if value == 0:
        raise NotificationLogServiceError("chat_id не может быть 0.")

    return value


def _validate_positive_int(value: int, *, field_name: str) -> int:
    """Проверяет положительный integer.

    Args:
        value: Проверяемое значение.
        field_name: Имя поля для текста ошибки.

    Returns:
        Проверенное значение.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise NotificationLogServiceError(f"{field_name} должен быть целым числом.")

    if value <= 0:
        raise NotificationLogServiceError(f"{field_name} должен быть положительным числом.")

    return value


def _optional_model_id(model: object | None) -> int | None:
    """Достаёт опциональный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model или `None`.

    Returns:
        Положительный DB id или `None`.
    """
    if model is None:
        return None

    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    return None


__all__ = [
    "NotificationLogRepository",
    "NotificationLogResult",
    "NotificationLogService",
    "NotificationLogServiceError",
    "SqlAlchemyNotificationLogRepository",
]

```


## FILE: app/services/api_errors.py

```python
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

```


## FILE: app/worker/jobs/sync_clans.py

```python
"""Worker job синхронизации отслеживаемых кланов."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiError, Clan
from app.integrations.clash import (
    ClashApiClient,
    ClashApiError,
    ClashClan,
    map_clash_api_error_to_context,
)
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

SYNC_CLANS_JOB_NAME = "sync_clans"

_CLAN_ENTITY_TYPE = "clan"
_CLAN_SYNC_STATUS_OK = "ok"
_CLAN_SYNC_STATUS_ERROR = "error"


class ClashClanSyncProvider(Protocol):
    """Contract Clash API provider для синхронизации кланов."""

    async def get_clan(self, clan_tag: str) -> ClashClan:
        """Получает актуальные данные клана.

        Args:
            clan_tag: Нормализованный тег клана.

        Returns:
            DTO клана из Clash API.
        """


class SyncClansRepository(Protocol):
    """Repository contract для sync clans job."""

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает кланы, для которых включён мониторинг.

        Returns:
            Tuple active-кланов.
        """

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в unit of work.

        Args:
            api_error: Модель ошибки внешнего API.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemySyncClansRepository:
    """SQLAlchemy repository для sync clans job."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает active-кланы в стабильном порядке.

        Returns:
            Tuple active-кланов.
        """
        result = await self._session.execute(
            select(Clan).where(Clan.is_active.is_(True)).order_by(Clan.id)
        )
        return tuple(result.scalars().all())

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в текущую session.

        Args:
            api_error: Модель ошибки внешнего API.
        """
        self._session.add(api_error)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class SyncClansJobResult:
    """Результат одного запуска sync clans job."""

    discovered_count: int
    synced_count: int
    failed_count: int
    skipped_inactive_count: int


class SyncClansJob:
    """Job синхронизации active-кланов из Clash API.

    Job идемпотентна: повторный запуск обновляет текущие поля клана теми же
    значениями, не создаёт новых доменных сущностей и пишет `last_sync_at`
    только при успешной синхронизации конкретного клана.
    """

    def __init__(
        self,
        *,
        repository: SyncClansRepository,
        clash_client: ClashClanSyncProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository для кланов и ошибок API.
            clash_client: Clash API client или совместимый provider.
            clock: Источник текущего времени для тестов.
        """
        self._repository = repository
        self._clash_client = clash_client
        self._clock = clock or _utc_now

    @classmethod
    def from_session(
        cls,
        *,
        session: AsyncSession,
        clash_client: ClashClanSyncProvider,
    ) -> "SyncClansJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            clash_client: Clash API client или совместимый provider.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemySyncClansRepository(session),
            clash_client=clash_client,
        )

    async def run(self, context: WorkerJobContext) -> SyncClansJobResult:
        """Синхронизирует active-кланы.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        clans = await self._repository.list_active_clans()

        synced_count = 0
        failed_count = 0
        skipped_inactive_count = 0

        for clan in clans:
            if context.should_stop:
                break

            if not clan.is_active:
                skipped_inactive_count += 1
                continue

            try:
                clash_clan = await self._clash_client.get_clan(clan.tag)
            except ClashApiError as error:
                self._handle_clash_api_error(
                    clan=clan,
                    error=error,
                    worker_name=context.job_name,
                )
                failed_count += 1
                continue

            _apply_synced_clan_data(
                clan=clan,
                clash_clan=clash_clan,
                synced_at=self._clock(),
            )
            synced_count += 1

        await self._repository.flush()

        return SyncClansJobResult(
            discovered_count=len(clans),
            synced_count=synced_count,
            failed_count=failed_count,
            skipped_inactive_count=skipped_inactive_count,
        )

    def _handle_clash_api_error(
        self,
        *,
        clan: Clan,
        error: ClashApiError,
        worker_name: str,
    ) -> None:
        """Обрабатывает typed ошибку Clash API для одного клана.

        Args:
            clan: Клан, при синхронизации которого возникла ошибка.
            error: Typed Clash API exception.
            worker_name: Имя текущей worker job для debug-контекста.
        """
        clan.sync_status = _CLAN_SYNC_STATUS_ERROR

        error_context = map_clash_api_error_to_context(
            error,
            entity_type=_CLAN_ENTITY_TYPE,
            entity_tag=clan.tag,
            worker_name=worker_name,
            retry_count=0,
        )
        self._repository.add_api_error(ApiError(**error_context.to_api_error_values()))


def register_sync_clans_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует sync clans job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=SYNC_CLANS_JOB_NAME)
    async def sync_clans(context: WorkerJobContext) -> None:
        """Запускает синхронизацию кланов внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("sync_clans требует DB session.")

        async with ClashApiClient.from_settings() as clash_client:
            job = SyncClansJob.from_session(
                session=context.session,
                clash_client=clash_client,
            )
            await job.run(context)


def _apply_synced_clan_data(
    *,
    clan: Clan,
    clash_clan: ClashClan,
    synced_at: datetime,
) -> None:
    """Обновляет локальную модель клана после успешного ответа Clash API.

    Args:
        clan: Локальная модель клана.
        clash_clan: DTO клана из Clash API.
        synced_at: Время успешной синхронизации.
    """
    clan.name = clash_clan.name
    clan.level = clash_clan.level
    clan.badge_url = clash_clan.badge_url
    clan.sync_status = _CLAN_SYNC_STATUS_OK
    clan.last_sync_at = synced_at


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "SYNC_CLANS_JOB_NAME",
    "ClashClanSyncProvider",
    "SqlAlchemySyncClansRepository",
    "SyncClansJob",
    "SyncClansJobResult",
    "SyncClansRepository",
    "register_sync_clans_job",
]

```


## FILE: app/worker/jobs/sync_members.py

```python
"""Worker job синхронизации составов отслеживаемых кланов."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiError, Clan
from app.integrations.clash import (
    ClashApiClient,
    ClashApiError,
    ClashClanMember,
    map_clash_api_error_to_context,
)
from app.services.member_lifecycle import MemberLifecycleResult, MemberLifecycleService
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

SYNC_MEMBERS_JOB_NAME = "sync_members"

_CLAN_MEMBERS_ENTITY_TYPE = "clan_members"


class ClashClanMembersProvider(Protocol):
    """Contract Clash API provider для синхронизации состава клана."""

    async def get_clan_members(self, clan_tag: str) -> list[ClashClanMember]:
        """Получает актуальный состав клана.

        Args:
            clan_tag: Нормализованный тег клана.

        Returns:
            Список участников клана из Clash API.
        """


class MemberLifecycleProcessor(Protocol):
    """Contract сервиса обработки жизненного цикла участников."""

    async def process_clan_members(
        self,
        *,
        clan: Clan,
        members: list[ClashClanMember],
    ) -> MemberLifecycleResult:
        """Обрабатывает актуальный состав клана.

        Args:
            clan: Отслеживаемый клан.
            members: Участники клана из Clash API.

        Returns:
            Счётчики изменений состава.
        """


class SyncMembersRepository(Protocol):
    """Repository contract для sync members job."""

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает кланы, для которых включён мониторинг.

        Returns:
            Tuple active-кланов.
        """

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в unit of work.

        Args:
            api_error: Модель ошибки внешнего API.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemySyncMembersRepository:
    """SQLAlchemy repository для sync members job."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает active-кланы в стабильном порядке.

        Returns:
            Tuple active-кланов.
        """
        result = await self._session.execute(
            select(Clan).where(Clan.is_active.is_(True)).order_by(Clan.id)
        )
        return tuple(result.scalars().all())

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в текущую session.

        Args:
            api_error: Модель ошибки внешнего API.
        """
        self._session.add(api_error)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class SyncMembersJobResult:
    """Результат одного запуска sync members job."""

    discovered_count: int
    synced_count: int
    failed_count: int
    skipped_inactive_count: int
    created_count: int
    updated_count: int
    left_count: int
    moved_count: int
    current_count: int


class SyncMembersJob:
    """Job синхронизации состава active-кланов из Clash API."""

    def __init__(
        self,
        *,
        repository: SyncMembersRepository,
        clash_client: ClashClanMembersProvider,
        member_lifecycle_processor: MemberLifecycleProcessor,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository для active-кланов и ошибок API.
            clash_client: Clash API client или совместимый provider.
            member_lifecycle_processor: Сервис обработки состава клана.
        """
        self._repository = repository
        self._clash_client = clash_client
        self._member_lifecycle_processor = member_lifecycle_processor

    @classmethod
    def from_session(
        cls,
        *,
        session: AsyncSession,
        clash_client: ClashClanMembersProvider,
    ) -> "SyncMembersJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            clash_client: Clash API client или совместимый provider.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemySyncMembersRepository(session),
            clash_client=clash_client,
            member_lifecycle_processor=MemberLifecycleService.from_session(session=session),
        )

    async def run(self, context: WorkerJobContext) -> SyncMembersJobResult:
        """Синхронизирует составы active-кланов.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        clans = await self._repository.list_active_clans()

        synced_count = 0
        failed_count = 0
        skipped_inactive_count = 0
        created_count = 0
        updated_count = 0
        left_count = 0
        moved_count = 0
        current_count = 0

        for clan in clans:
            if context.should_stop:
                break

            if not clan.is_active:
                skipped_inactive_count += 1
                continue

            try:
                members = await self._clash_client.get_clan_members(clan.tag)
            except ClashApiError as error:
                self._handle_clash_api_error(
                    clan=clan,
                    error=error,
                    worker_name=context.job_name,
                )
                failed_count += 1
                continue

            lifecycle_result = await self._member_lifecycle_processor.process_clan_members(
                clan=clan,
                members=members,
            )
            synced_count += 1
            created_count += lifecycle_result.created_count
            updated_count += lifecycle_result.updated_count
            left_count += lifecycle_result.left_count
            moved_count += lifecycle_result.moved_count
            current_count += lifecycle_result.current_count

        await self._repository.flush()

        return SyncMembersJobResult(
            discovered_count=len(clans),
            synced_count=synced_count,
            failed_count=failed_count,
            skipped_inactive_count=skipped_inactive_count,
            created_count=created_count,
            updated_count=updated_count,
            left_count=left_count,
            moved_count=moved_count,
            current_count=current_count,
        )

    def _handle_clash_api_error(
        self,
        *,
        clan: Clan,
        error: ClashApiError,
        worker_name: str,
    ) -> None:
        """Обрабатывает typed ошибку Clash API для состава одного клана.

        Args:
            clan: Клан, при синхронизации состава которого возникла ошибка.
            error: Typed Clash API exception.
            worker_name: Имя текущей worker job для debug-контекста.
        """
        error_context = map_clash_api_error_to_context(
            error,
            entity_type=_CLAN_MEMBERS_ENTITY_TYPE,
            entity_tag=clan.tag,
            worker_name=worker_name,
            retry_count=0,
        )
        self._repository.add_api_error(ApiError(**error_context.to_api_error_values()))


def register_sync_members_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует sync members job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=SYNC_MEMBERS_JOB_NAME)
    async def sync_members(context: WorkerJobContext) -> None:
        """Запускает синхронизацию составов внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("sync_members требует DB session.")

        async with ClashApiClient.from_settings() as clash_client:
            job = SyncMembersJob.from_session(
                session=context.session,
                clash_client=clash_client,
            )
            await job.run(context)


__all__ = [
    "SYNC_MEMBERS_JOB_NAME",
    "ClashClanMembersProvider",
    "MemberLifecycleProcessor",
    "SqlAlchemySyncMembersRepository",
    "SyncMembersJob",
    "SyncMembersJobResult",
    "SyncMembersRepository",
    "register_sync_members_job",
]

```


## FILE: app/worker/jobs/sync_player_profiles.py

```python
"""Worker job синхронизации snapshot-профилей игроков."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiError, ClanMemberSnapshot, PlayerAccount, PlayerProfileSnapshot
from app.domain import normalize_player_tag
from app.integrations.clash import ClashApiClient, ClashApiError, map_clash_api_error_to_context
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

SYNC_PLAYER_PROFILES_JOB_NAME = "sync_player_profiles"

_PLAYER_PROFILE_ENTITY_TYPE = "player_profile"
type JsonObject = dict[str, object]


class ClashPlayerProfileProvider(Protocol):
    """Contract Clash API provider для синхронизации профилей игроков."""

    async def get_player(self, player_tag: str) -> Mapping[str, object]:
        """Получает профиль игрока.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            JSON object профиля игрока.
        """


class SyncPlayerProfilesRepository(Protocol):
    """Repository contract для sync player profiles job."""

    async def list_profile_player_tags(self) -> tuple[str, ...]:
        """Возвращает уникальные player tags для синхронизации профилей.

        Returns:
            Tuple тегов из active linked accounts и current clan members.
        """

    async def get_latest_profile_snapshot(
        self,
        player_tag: str,
    ) -> PlayerProfileSnapshot | None:
        """Возвращает последний snapshot профиля игрока.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            Последний snapshot или `None`.
        """

    def add_profile_snapshot(self, snapshot: PlayerProfileSnapshot) -> None:
        """Добавляет snapshot профиля в unit of work.

        Args:
            snapshot: Новый snapshot профиля игрока.
        """

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в unit of work.

        Args:
            api_error: Модель ошибки внешнего API.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemySyncPlayerProfilesRepository:
    """SQLAlchemy repository для sync player profiles job."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_profile_player_tags(self) -> tuple[str, ...]:
        """Возвращает уникальные player tags из linked accounts и current members.

        Returns:
            Tuple нормализованных тегов в стабильном порядке.
        """
        linked_accounts_result = await self._session.execute(
            select(PlayerAccount.player_tag).where(PlayerAccount.is_active.is_(True))
        )
        current_members_result = await self._session.execute(
            select(ClanMemberSnapshot.player_tag).where(ClanMemberSnapshot.is_current.is_(True))
        )

        player_tags = {
            normalize_player_tag(player_tag)
            for player_tag in [
                *linked_accounts_result.scalars().all(),
                *current_members_result.scalars().all(),
            ]
        }
        return tuple(sorted(player_tags))

    async def get_latest_profile_snapshot(
        self,
        player_tag: str,
    ) -> PlayerProfileSnapshot | None:
        """Возвращает последний snapshot профиля игрока.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            Последний snapshot или `None`.
        """
        result = await self._session.execute(
            select(PlayerProfileSnapshot)
            .where(PlayerProfileSnapshot.player_tag == player_tag)
            .order_by(
                PlayerProfileSnapshot.snapshot_at.desc(),
                PlayerProfileSnapshot.id.desc(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    def add_profile_snapshot(self, snapshot: PlayerProfileSnapshot) -> None:
        """Добавляет snapshot профиля в текущую session.

        Args:
            snapshot: Новый snapshot профиля игрока.
        """
        self._session.add(snapshot)

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в текущую session.

        Args:
            api_error: Модель ошибки внешнего API.
        """
        self._session.add(api_error)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class PlayerProfileData:
    """Нормализованные данные профиля игрока для snapshot."""

    player_tag: str
    name: str
    town_hall_level: int | None
    town_hall_weapon_level: int | None
    exp_level: int | None
    trophies: int | None
    best_trophies: int | None
    war_stars: int | None
    donations: int | None
    donations_received: int | None
    clan_capital_contributions: int | None
    heroes_json: list[JsonObject]
    troops_json: list[JsonObject]
    spells_json: list[JsonObject]
    achievements_json: list[JsonObject]


@dataclass(frozen=True, slots=True)
class SyncPlayerProfilesJobResult:
    """Результат одного запуска sync player profiles job."""

    discovered_count: int
    synced_count: int
    failed_count: int
    created_snapshot_count: int
    skipped_unchanged_count: int


class SyncPlayerProfilesJob:
    """Job синхронизации snapshot-профилей игроков из Clash API."""

    def __init__(
        self,
        *,
        repository: SyncPlayerProfilesRepository,
        clash_client: ClashPlayerProfileProvider,
        clock: object | None = None,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository для профилей и ошибок API.
            clash_client: Clash API client или совместимый provider.
            clock: Источник времени для тестов. Если объект callable, он должен
                возвращать timezone-aware `datetime`.
        """
        self._repository = repository
        self._clash_client = clash_client
        self._clock = clock

    @classmethod
    def from_session(
        cls,
        *,
        session: AsyncSession,
        clash_client: ClashPlayerProfileProvider,
    ) -> "SyncPlayerProfilesJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            clash_client: Clash API client или совместимый provider.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemySyncPlayerProfilesRepository(session),
            clash_client=clash_client,
        )

    async def run(self, context: WorkerJobContext) -> SyncPlayerProfilesJobResult:
        """Синхронизирует snapshot-профили игроков.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        player_tags = await self._repository.list_profile_player_tags()

        synced_count = 0
        failed_count = 0
        created_snapshot_count = 0
        skipped_unchanged_count = 0

        for player_tag in player_tags:
            if context.should_stop:
                break

            try:
                payload = await self._clash_client.get_player(player_tag)
            except ClashApiError as error:
                self._handle_clash_api_error(
                    player_tag=player_tag,
                    error=error,
                    worker_name=context.job_name,
                )
                failed_count += 1
                continue

            profile_data = _extract_profile_data(payload)
            latest_snapshot = await self._repository.get_latest_profile_snapshot(
                profile_data.player_tag
            )

            synced_count += 1
            if latest_snapshot is not None and _snapshot_matches_profile_data(
                latest_snapshot,
                profile_data,
            ):
                skipped_unchanged_count += 1
                continue

            self._repository.add_profile_snapshot(
                _build_profile_snapshot(
                    profile_data=profile_data,
                    snapshot_at=self._now(),
                )
            )
            created_snapshot_count += 1

        await self._repository.flush()

        return SyncPlayerProfilesJobResult(
            discovered_count=len(player_tags),
            synced_count=synced_count,
            failed_count=failed_count,
            created_snapshot_count=created_snapshot_count,
            skipped_unchanged_count=skipped_unchanged_count,
        )

    def _handle_clash_api_error(
        self,
        *,
        player_tag: str,
        error: ClashApiError,
        worker_name: str,
    ) -> None:
        """Обрабатывает typed ошибку Clash API для профиля игрока.

        Args:
            player_tag: Тег игрока.
            error: Typed Clash API exception.
            worker_name: Имя текущей worker job для debug-контекста.
        """
        error_context = map_clash_api_error_to_context(
            error,
            entity_type=_PLAYER_PROFILE_ENTITY_TYPE,
            entity_tag=player_tag,
            worker_name=worker_name,
            retry_count=0,
        )
        self._repository.add_api_error(ApiError(**error_context.to_api_error_values()))

    def _now(self) -> datetime:
        """Возвращает текущее время для snapshot.

        Returns:
            Timezone-aware UTC datetime.
        """
        if callable(self._clock):
            value = self._clock()
            if not isinstance(value, datetime):
                raise TypeError("clock должен возвращать datetime.")
            return value

        return datetime.now(UTC)


def register_sync_player_profiles_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует sync player profiles job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=SYNC_PLAYER_PROFILES_JOB_NAME)
    async def sync_player_profiles(context: WorkerJobContext) -> None:
        """Запускает синхронизацию профилей внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("sync_player_profiles требует DB session.")

        async with ClashApiClient.from_settings() as clash_client:
            job = SyncPlayerProfilesJob.from_session(
                session=context.session,
                clash_client=clash_client,
            )
            await job.run(context)


def _extract_profile_data(payload: Mapping[str, object]) -> PlayerProfileData:
    """Извлекает нормализованные данные профиля из payload Clash API.

    Args:
        payload: JSON object ответа `GET /players/{playerTag}`.

    Returns:
        Нормализованные данные профиля для snapshot.
    """
    return PlayerProfileData(
        player_tag=normalize_player_tag(_required_str_field(payload, "tag")),
        name=_required_str_field(payload, "name"),
        town_hall_level=_optional_int_field(payload, "townHallLevel"),
        town_hall_weapon_level=_optional_int_field(payload, "townHallWeaponLevel"),
        exp_level=_optional_int_field(payload, "expLevel"),
        trophies=_optional_int_field(payload, "trophies"),
        best_trophies=_optional_int_field(payload, "bestTrophies"),
        war_stars=_optional_int_field(payload, "warStars"),
        donations=_optional_int_field(payload, "donations"),
        donations_received=_optional_int_field(payload, "donationsReceived"),
        clan_capital_contributions=_optional_int_field(
            payload,
            "clanCapitalContributions",
        ),
        heroes_json=_optional_json_object_list(payload, "heroes"),
        troops_json=_optional_json_object_list(payload, "troops"),
        spells_json=_optional_json_object_list(payload, "spells"),
        achievements_json=_optional_json_object_list(payload, "achievements"),
    )


def _build_profile_snapshot(
    *,
    profile_data: PlayerProfileData,
    snapshot_at: datetime,
) -> PlayerProfileSnapshot:
    """Создаёт snapshot профиля игрока.

    Args:
        profile_data: Нормализованные данные профиля.
        snapshot_at: Время snapshot.

    Returns:
        Модель snapshot профиля.
    """
    return PlayerProfileSnapshot(
        player_tag=profile_data.player_tag,
        name=profile_data.name,
        town_hall_level=profile_data.town_hall_level,
        town_hall_weapon_level=profile_data.town_hall_weapon_level,
        exp_level=profile_data.exp_level,
        trophies=profile_data.trophies,
        best_trophies=profile_data.best_trophies,
        war_stars=profile_data.war_stars,
        donations=profile_data.donations,
        donations_received=profile_data.donations_received,
        clan_capital_contributions=profile_data.clan_capital_contributions,
        heroes_json=profile_data.heroes_json,
        troops_json=profile_data.troops_json,
        spells_json=profile_data.spells_json,
        achievements_json=profile_data.achievements_json,
        snapshot_at=snapshot_at,
    )


def _snapshot_matches_profile_data(
    snapshot: PlayerProfileSnapshot,
    profile_data: PlayerProfileData,
) -> bool:
    """Сравнивает последний snapshot с актуальным профилем.

    Args:
        snapshot: Последний сохранённый snapshot.
        profile_data: Актуальные данные профиля из Clash API.

    Returns:
        `True`, если новый snapshot не нужен.
    """
    return (
        snapshot.player_tag == profile_data.player_tag
        and snapshot.name == profile_data.name
        and snapshot.town_hall_level == profile_data.town_hall_level
        and snapshot.town_hall_weapon_level == profile_data.town_hall_weapon_level
        and snapshot.exp_level == profile_data.exp_level
        and snapshot.trophies == profile_data.trophies
        and snapshot.best_trophies == profile_data.best_trophies
        and snapshot.war_stars == profile_data.war_stars
        and snapshot.donations == profile_data.donations
        and snapshot.donations_received == profile_data.donations_received
        and snapshot.clan_capital_contributions == profile_data.clan_capital_contributions
        and snapshot.heroes_json == profile_data.heroes_json
        and snapshot.troops_json == profile_data.troops_json
        and snapshot.spells_json == profile_data.spells_json
        and snapshot.achievements_json == profile_data.achievements_json
    )


def _required_str_field(payload: Mapping[str, object], field_name: str) -> str:
    """Достаёт обязательное строковое поле.

    Args:
        payload: JSON object.
        field_name: Название поля.

    Returns:
        Непустая строка без пробелов по краям.

    Raises:
        ValueError: Если поле отсутствует, пустое или не строковое.
    """
    value = payload.get(field_name)
    if not isinstance(value, str):
        raise ValueError(f"Clash API response должен содержать строковое поле {field_name}.")

    normalized = value.strip()
    if not normalized:
        raise ValueError(f"Clash API response содержит пустое поле {field_name}.")

    return normalized


def _optional_int_field(payload: Mapping[str, object], field_name: str) -> int | None:
    """Достаёт опциональное целочисленное поле.

    Args:
        payload: JSON object.
        field_name: Название поля.

    Returns:
        Целочисленное значение или `None`.

    Raises:
        ValueError: Если поле есть, но не является int.
    """
    value = payload.get(field_name)
    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Clash API response поле {field_name} должно быть целым числом.")

    return value


def _optional_json_object_list(
    payload: Mapping[str, object],
    field_name: str,
) -> list[JsonObject]:
    """Достаёт JSON list object-полей профиля без сохранения raw profile.

    Args:
        payload: JSON object.
        field_name: Название поля.

    Returns:
        Глубокая копия списка JSON-объектов.

    Raises:
        ValueError: Если поле есть, но не является списком объектов.
    """
    value = payload.get(field_name)
    if value is None:
        return []

    if not isinstance(value, list):
        raise ValueError(f"Clash API response поле {field_name} должно быть списком.")

    items: list[JsonObject] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(
                f"Clash API response поле {field_name} должно содержать только объекты."
            )

        items.append(cast(JsonObject, deepcopy(dict(item))))

    return items


__all__ = [
    "SYNC_PLAYER_PROFILES_JOB_NAME",
    "ClashPlayerProfileProvider",
    "PlayerProfileData",
    "SqlAlchemySyncPlayerProfilesRepository",
    "SyncPlayerProfilesJob",
    "SyncPlayerProfilesJobResult",
    "SyncPlayerProfilesRepository",
    "register_sync_player_profiles_job",
]

```


## FILE: app/worker/jobs/sync_current_wars.py

```python
"""Worker job синхронизации текущих обычных войн."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiError, Clan, WarAttack, WarMember, WarSnapshot
from app.domain import build_war_event_key
from app.integrations.clash import (
    ClashApiClient,
    ClashApiError,
    ClashCurrentWar,
    ClashWarSideSummary,
    map_clash_api_error_to_context,
)
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

SYNC_CURRENT_WARS_JOB_NAME = "sync_current_wars"

_WAR_ENTITY_TYPE = "current_war"
_OUR_SIDE = "our"
_OPPONENT_SIDE = "opponent"
_DEFAULT_ATTACKS_PER_MEMBER = 2


class ClashCurrentWarProvider(Protocol):
    """Contract Clash API provider для синхронизации текущих войн."""

    async def get_current_war(self, clan_tag: str) -> ClashCurrentWar | None:
        """Получает текущую войну клана.

        Args:
            clan_tag: Нормализованный тег клана.

        Returns:
            DTO текущей войны или `None`, если войны нет.
        """


class SyncCurrentWarsRepository(Protocol):
    """Repository contract для sync current wars job."""

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает кланы, для которых включён мониторинг.

        Returns:
            Tuple active-кланов.
        """

    async def get_war_snapshot_by_event_key(self, war_event_key: str) -> WarSnapshot | None:
        """Возвращает snapshot войны по стабильному event key.

        Args:
            war_event_key: Стабильный hash-key войны.

        Returns:
            Snapshot войны или `None`.
        """

    def add_war_snapshot(self, war_snapshot: WarSnapshot) -> None:
        """Добавляет snapshot войны в unit of work.

        Args:
            war_snapshot: Новый snapshot войны.
        """

    async def replace_war_children(
        self,
        *,
        war_snapshot: WarSnapshot,
        members: tuple[WarMember, ...],
        attacks: tuple[WarAttack, ...],
    ) -> None:
        """Заменяет дочерние записи состава и атак войны.

        Args:
            war_snapshot: Snapshot войны.
            members: Актуальные участники войны.
            attacks: Актуальные атаки войны.
        """

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в unit of work.

        Args:
            api_error: Модель ошибки внешнего API.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemySyncCurrentWarsRepository:
    """SQLAlchemy repository для sync current wars job."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает active-кланы в стабильном порядке.

        Returns:
            Tuple active-кланов.
        """
        result = await self._session.execute(
            select(Clan).where(Clan.is_active.is_(True)).order_by(Clan.id)
        )
        return tuple(result.scalars().all())

    async def get_war_snapshot_by_event_key(self, war_event_key: str) -> WarSnapshot | None:
        """Возвращает snapshot войны по event key.

        Args:
            war_event_key: Стабильный hash-key войны.

        Returns:
            Snapshot войны или `None`.
        """
        result = await self._session.execute(
            select(WarSnapshot).where(WarSnapshot.war_event_key == war_event_key)
        )
        return result.scalar_one_or_none()

    def add_war_snapshot(self, war_snapshot: WarSnapshot) -> None:
        """Добавляет snapshot войны в текущую session.

        Args:
            war_snapshot: Новый snapshot войны.
        """
        self._session.add(war_snapshot)

    async def replace_war_children(
        self,
        *,
        war_snapshot: WarSnapshot,
        members: tuple[WarMember, ...],
        attacks: tuple[WarAttack, ...],
    ) -> None:
        """Заменяет дочерние записи войны без каскадного удаления snapshot.

        Args:
            war_snapshot: Snapshot войны.
            members: Актуальные участники войны.
            attacks: Актуальные атаки войны.
        """
        await self._session.flush()
        war_snapshot_id = _required_model_id(war_snapshot, model_name="WarSnapshot")

        await self._session.execute(
            delete(WarAttack).where(WarAttack.war_snapshot_id == war_snapshot_id)
        )
        await self._session.execute(
            delete(WarMember).where(WarMember.war_snapshot_id == war_snapshot_id)
        )

        for member in members:
            member.war_snapshot_id = war_snapshot_id
            member.war_snapshot = war_snapshot
            self._session.add(member)

        for attack in attacks:
            attack.war_snapshot_id = war_snapshot_id
            attack.war_snapshot = war_snapshot
            self._session.add(attack)

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в текущую session.

        Args:
            api_error: Модель ошибки внешнего API.
        """
        self._session.add(api_error)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class WarSnapshotData:
    """Нормализованные данные текущей войны для сохранения."""

    clan_id: int
    war_event_key: str
    state: str
    team_size: int
    attacks_per_member: int
    preparation_start_time: datetime
    start_time: datetime
    end_time: datetime
    opponent_tag: str
    opponent_name: str | None
    our_stars: int
    opponent_stars: int
    our_destruction: Decimal
    opponent_destruction: Decimal
    our_attacks: int
    opponent_attacks: int
    snapshot_at: datetime


@dataclass(frozen=True, slots=True)
class SyncCurrentWarsJobResult:
    """Результат одного запуска sync current wars job."""

    discovered_count: int
    synced_count: int
    no_war_count: int
    failed_count: int
    skipped_inactive_count: int
    created_count: int
    updated_count: int
    member_count: int
    attack_count: int


class SyncCurrentWarsJob:
    """Job синхронизации текущих обычных войн active-кланов."""

    def __init__(
        self,
        *,
        repository: SyncCurrentWarsRepository,
        clash_client: ClashCurrentWarProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository для войн и ошибок API.
            clash_client: Clash API client или совместимый provider.
            clock: Источник текущего времени для тестов.
        """
        self._repository = repository
        self._clash_client = clash_client
        self._clock = clock or _utc_now

    @classmethod
    def from_session(
        cls,
        *,
        session: AsyncSession,
        clash_client: ClashCurrentWarProvider,
    ) -> "SyncCurrentWarsJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            clash_client: Clash API client или совместимый provider.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemySyncCurrentWarsRepository(session),
            clash_client=clash_client,
        )

    async def run(self, context: WorkerJobContext) -> SyncCurrentWarsJobResult:
        """Синхронизирует текущие войны active-кланов.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        clans = await self._repository.list_active_clans()

        synced_count = 0
        no_war_count = 0
        failed_count = 0
        skipped_inactive_count = 0
        created_count = 0
        updated_count = 0
        member_count = 0
        attack_count = 0

        for clan in clans:
            if context.should_stop:
                break

            if not clan.is_active:
                skipped_inactive_count += 1
                continue

            try:
                current_war = await self._clash_client.get_current_war(clan.tag)
            except ClashApiError as error:
                self._handle_clash_api_error(
                    clan=clan,
                    error=error,
                    worker_name=context.job_name,
                )
                failed_count += 1
                continue

            if current_war is None or _is_no_current_war(current_war):
                no_war_count += 1
                continue

            snapshot_at = self._clock()
            snapshot_data = _build_war_snapshot_data(
                clan=clan,
                current_war=current_war,
                snapshot_at=snapshot_at,
            )
            war_snapshot = await self._repository.get_war_snapshot_by_event_key(
                snapshot_data.war_event_key
            )

            if war_snapshot is None:
                war_snapshot = _build_war_snapshot(snapshot_data)
                self._repository.add_war_snapshot(war_snapshot)
                created_count += 1
            else:
                _apply_war_snapshot_update(war_snapshot, snapshot_data)
                updated_count += 1

            members = _build_war_members(
                current_war,
                attacks_per_member=snapshot_data.attacks_per_member,
            )
            attacks = _build_war_attacks(current_war)
            await self._repository.replace_war_children(
                war_snapshot=war_snapshot,
                members=members,
                attacks=attacks,
            )

            synced_count += 1
            member_count += len(members)
            attack_count += len(attacks)

        await self._repository.flush()

        return SyncCurrentWarsJobResult(
            discovered_count=len(clans),
            synced_count=synced_count,
            no_war_count=no_war_count,
            failed_count=failed_count,
            skipped_inactive_count=skipped_inactive_count,
            created_count=created_count,
            updated_count=updated_count,
            member_count=member_count,
            attack_count=attack_count,
        )

    def _handle_clash_api_error(
        self,
        *,
        clan: Clan,
        error: ClashApiError,
        worker_name: str,
    ) -> None:
        """Обрабатывает typed ошибку Clash API для текущей войны клана.

        Args:
            clan: Клан, при синхронизации войны которого возникла ошибка.
            error: Typed Clash API exception.
            worker_name: Имя текущей worker job для debug-контекста.
        """
        error_context = map_clash_api_error_to_context(
            error,
            entity_type=_WAR_ENTITY_TYPE,
            entity_tag=clan.tag,
            worker_name=worker_name,
            retry_count=0,
        )
        self._repository.add_api_error(ApiError(**error_context.to_api_error_values()))


def register_sync_current_wars_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует sync current wars job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=SYNC_CURRENT_WARS_JOB_NAME)
    async def sync_current_wars(context: WorkerJobContext) -> None:
        """Запускает синхронизацию текущих войн внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("sync_current_wars требует DB session.")

        async with ClashApiClient.from_settings() as clash_client:
            job = SyncCurrentWarsJob.from_session(
                session=context.session,
                clash_client=clash_client,
            )
            await job.run(context)


def _is_no_current_war(current_war: ClashCurrentWar) -> bool:
    """Проверяет состояние отсутствия текущей войны.

    Args:
        current_war: DTO текущей войны.

    Returns:
        `True`, если API явно вернул состояние без войны.
    """
    return current_war.state.replace("_", "").replace("-", "").lower() == "notinwar"


def _build_war_snapshot_data(
    *,
    clan: Clan,
    current_war: ClashCurrentWar,
    snapshot_at: datetime,
) -> WarSnapshotData:
    """Собирает данные snapshot текущей войны.

    Args:
        clan: Отслеживаемый клан.
        current_war: DTO текущей войны.
        snapshot_at: Время синхронизации.

    Returns:
        Нормализованные данные snapshot войны.
    """
    clan_id = _required_model_id(clan, model_name="Clan")
    our_side = _required_side(current_war.clan, field_name="clan")
    opponent_side = _required_side(current_war.opponent, field_name="opponent")
    opponent_tag = _required_string(opponent_side.tag, field_name="opponent.tag")

    preparation_start_time = _parse_clash_datetime(
        current_war.preparation_start_time,
        field_name="preparationStartTime",
    )
    start_time = _parse_clash_datetime(current_war.start_time, field_name="startTime")
    end_time = _parse_clash_datetime(current_war.end_time, field_name="endTime")
    team_size = _required_int(current_war.team_size, field_name="teamSize")
    attacks_per_member = current_war.attacks_per_member or _DEFAULT_ATTACKS_PER_MEMBER

    war_event_key = build_war_event_key(
        clan_tag=clan.tag,
        opponent_tag=opponent_tag,
        preparation_start_time=preparation_start_time,
        start_time=start_time,
        end_time=end_time,
        team_size=team_size,
    )

    return WarSnapshotData(
        clan_id=clan_id,
        war_event_key=war_event_key,
        state=current_war.state,
        team_size=team_size,
        attacks_per_member=attacks_per_member,
        preparation_start_time=preparation_start_time,
        start_time=start_time,
        end_time=end_time,
        opponent_tag=opponent_tag,
        opponent_name=opponent_side.name,
        our_stars=our_side.stars or 0,
        opponent_stars=opponent_side.stars or 0,
        our_destruction=_decimal_from_optional(our_side.destruction_percentage),
        opponent_destruction=_decimal_from_optional(opponent_side.destruction_percentage),
        our_attacks=_side_attack_count(our_side),
        opponent_attacks=_side_attack_count(opponent_side),
        snapshot_at=snapshot_at,
    )


def _build_war_snapshot(snapshot_data: WarSnapshotData) -> WarSnapshot:
    """Создаёт модель snapshot войны.

    Args:
        snapshot_data: Нормализованные данные snapshot.

    Returns:
        Новая модель `WarSnapshot`.
    """
    return WarSnapshot(
        clan_id=snapshot_data.clan_id,
        war_tag=None,
        war_event_key=snapshot_data.war_event_key,
        state=snapshot_data.state,
        team_size=snapshot_data.team_size,
        attacks_per_member=snapshot_data.attacks_per_member,
        preparation_start_time=snapshot_data.preparation_start_time,
        start_time=snapshot_data.start_time,
        end_time=snapshot_data.end_time,
        opponent_tag=snapshot_data.opponent_tag,
        opponent_name=snapshot_data.opponent_name,
        our_stars=snapshot_data.our_stars,
        opponent_stars=snapshot_data.opponent_stars,
        our_destruction=snapshot_data.our_destruction,
        opponent_destruction=snapshot_data.opponent_destruction,
        our_attacks=snapshot_data.our_attacks,
        opponent_attacks=snapshot_data.opponent_attacks,
        snapshot_at=snapshot_data.snapshot_at,
    )


def _apply_war_snapshot_update(
    war_snapshot: WarSnapshot,
    snapshot_data: WarSnapshotData,
) -> None:
    """Обновляет существующий snapshot войны актуальными значениями.

    Args:
        war_snapshot: Существующий snapshot войны.
        snapshot_data: Нормализованные данные snapshot.
    """
    war_snapshot.state = snapshot_data.state
    war_snapshot.team_size = snapshot_data.team_size
    war_snapshot.attacks_per_member = snapshot_data.attacks_per_member
    war_snapshot.preparation_start_time = snapshot_data.preparation_start_time
    war_snapshot.start_time = snapshot_data.start_time
    war_snapshot.end_time = snapshot_data.end_time
    war_snapshot.opponent_tag = snapshot_data.opponent_tag
    war_snapshot.opponent_name = snapshot_data.opponent_name
    war_snapshot.our_stars = snapshot_data.our_stars
    war_snapshot.opponent_stars = snapshot_data.opponent_stars
    war_snapshot.our_destruction = snapshot_data.our_destruction
    war_snapshot.opponent_destruction = snapshot_data.opponent_destruction
    war_snapshot.our_attacks = snapshot_data.our_attacks
    war_snapshot.opponent_attacks = snapshot_data.opponent_attacks
    war_snapshot.snapshot_at = snapshot_data.snapshot_at


def _build_war_members(
    current_war: ClashCurrentWar,
    *,
    attacks_per_member: int,
) -> tuple[WarMember, ...]:
    """Создаёт участников войны для обеих сторон.

    Args:
        current_war: DTO текущей войны.
        attacks_per_member: Количество атак на участника.

    Returns:
        Tuple моделей `WarMember`.
    """
    members: list[WarMember] = []
    for side, side_summary in (
        (_OUR_SIDE, current_war.clan),
        (_OPPONENT_SIDE, current_war.opponent),
    ):
        if side_summary is None:
            continue

        for member in side_summary.members:
            attacks_done = len(member.attacks)
            members.append(
                WarMember(
                    side=side,
                    player_tag=member.player_tag,
                    name=member.name,
                    town_hall_level=member.town_hall_level,
                    map_position=member.map_position,
                    attacks_done=attacks_done,
                    attacks_left=max(attacks_per_member - attacks_done, 0),
                )
            )

    return tuple(members)


def _build_war_attacks(current_war: ClashCurrentWar) -> tuple[WarAttack, ...]:
    """Создаёт атаки войны для обеих сторон.

    Args:
        current_war: DTO текущей войны.

    Returns:
        Tuple моделей `WarAttack`.
    """
    attacks: list[WarAttack] = []
    for side_summary in (current_war.clan, current_war.opponent):
        if side_summary is None:
            continue

        for member in side_summary.members:
            for attack in member.attacks:
                attacks.append(
                    WarAttack(
                        attacker_tag=attack.attacker_tag,
                        defender_tag=attack.defender_tag,
                        stars=attack.stars or 0,
                        destruction_percentage=_decimal_from_optional(
                            attack.destruction_percentage
                        ),
                        duration=attack.duration,
                        order=_required_int(attack.order, field_name="attack.order"),
                    )
                )

    return tuple(sorted(attacks, key=lambda attack: attack.order))


def _side_attack_count(side_summary: ClashWarSideSummary) -> int:
    """Возвращает количество атак стороны войны.

    Args:
        side_summary: Сторона войны из Clash API.

    Returns:
        Количество использованных атак.
    """
    if side_summary.attacks is not None:
        return side_summary.attacks

    return sum(len(member.attacks) for member in side_summary.members)


def _parse_clash_datetime(value: str | None, *, field_name: str) -> datetime:
    """Парсит datetime из формата Clash API или ISO-строки.

    Args:
        value: Строка времени из Clash API.
        field_name: Название поля для текста ошибки.

    Returns:
        Timezone-aware UTC datetime.

    Raises:
        ValueError: Если значение отсутствует или формат не распознан.
    """
    normalized = _required_string(value, field_name=field_name)

    for date_format in ("%Y%m%dT%H%M%S.%fZ", "%Y%m%dT%H%M%SZ"):
        try:
            return datetime.strptime(normalized, date_format).replace(tzinfo=UTC)
        except ValueError:
            pass

    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} имеет неподдерживаемый формат datetime.") from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} должен содержать timezone.")

    return parsed.astimezone(UTC)


def _required_side(
    value: ClashWarSideSummary | None,
    *,
    field_name: str,
) -> ClashWarSideSummary:
    """Достаёт обязательную сторону войны.

    Args:
        value: DTO стороны войны.
        field_name: Название поля для текста ошибки.

    Returns:
        DTO стороны войны.

    Raises:
        ValueError: Если сторона войны отсутствует.
    """
    if value is None:
        raise ValueError(f"Current war должен содержать сторону {field_name}.")

    return value


def _required_string(value: str | None, *, field_name: str) -> str:
    """Достаёт обязательную непустую строку.

    Args:
        value: Строковое значение.
        field_name: Название поля для текста ошибки.

    Returns:
        Непустая строка.

    Raises:
        ValueError: Если значение пустое.
    """
    if value is None:
        raise ValueError(f"{field_name} не может быть пустым.")

    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} не может быть пустым.")

    return normalized


def _required_int(value: int | None, *, field_name: str) -> int:
    """Достаёт обязательное целочисленное значение.

    Args:
        value: Значение.
        field_name: Название поля для текста ошибки.

    Returns:
        Целое число.

    Raises:
        ValueError: Если значение отсутствует или не положительное.
    """
    if value is None:
        raise ValueError(f"{field_name} не может быть пустым.")

    if value <= 0:
        raise ValueError(f"{field_name} должен быть больше 0.")

    return value


def _decimal_from_optional(value: float | int | None) -> Decimal:
    """Преобразует числовое значение API в Decimal.

    Args:
        value: Число из Clash API или `None`.

    Returns:
        Decimal-значение для SQLAlchemy Numeric.
    """
    if value is None:
        return Decimal("0")

    return Decimal(str(value))


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.

    Raises:
        ValueError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise ValueError(f"{model_name} должен быть сохранён в БД.")


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "SYNC_CURRENT_WARS_JOB_NAME",
    "ClashCurrentWarProvider",
    "SqlAlchemySyncCurrentWarsRepository",
    "SyncCurrentWarsJob",
    "SyncCurrentWarsJobResult",
    "SyncCurrentWarsRepository",
    "WarSnapshotData",
    "register_sync_current_wars_job",
]

```


## FILE: app/worker/jobs/sync_cwl.py

```python
"""Worker job синхронизации Лиги войн кланов."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiError, Clan, CwlSeason, CwlWar
from app.domain import normalize_clan_tag
from app.integrations.clash import (
    ClashApiClient,
    ClashApiError,
    ClashCwlLeagueGroup,
    ClashCwlWar,
    ClashNotFoundError,
    ClashWarSideSummary,
    map_clash_api_error_to_context,
)
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

SYNC_CWL_JOB_NAME = "sync_cwl"

_CWL_GROUP_ENTITY_TYPE = "cwl_league_group"
_CWL_WAR_ENTITY_TYPE = "cwl_war"
_PLACEHOLDER_WAR_TAGS = {"#0", "0"}
_ENDED_STATES = {"ended", "warended"}


class ClashCwlProvider(Protocol):
    """Contract Clash API provider для синхронизации ЛВК."""

    async def get_cwl_league_group(self, clan_tag: str) -> ClashCwlLeagueGroup:
        """Получает текущую группу ЛВК клана.

        Args:
            clan_tag: Нормализованный тег клана.

        Returns:
            DTO группы ЛВК.
        """

    async def get_cwl_war(self, war_tag: str) -> ClashCwlWar:
        """Получает конкретную войну ЛВК.

        Args:
            war_tag: War tag из League Group.

        Returns:
            DTO конкретной войны ЛВК.
        """


class SyncCwlRepository(Protocol):
    """Repository contract для sync cwl job."""

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает кланы, для которых включён мониторинг.

        Returns:
            Tuple active-кланов.
        """

    async def get_cwl_season(self, *, clan_id: int, season: str) -> CwlSeason | None:
        """Возвращает CWL season для клана.

        Args:
            clan_id: DB ID клана.
            season: Ключ сезона из Clash API.

        Returns:
            Сезон ЛВК или `None`.
        """

    def add_cwl_season(self, cwl_season: CwlSeason) -> None:
        """Добавляет CWL season в unit of work.

        Args:
            cwl_season: Новая модель сезона.
        """

    async def get_cwl_war_by_tag(self, war_tag: str) -> CwlWar | None:
        """Возвращает CWL war по уникальному war tag.

        Args:
            war_tag: War tag из Clash API.

        Returns:
            Модель CWL war или `None`.
        """

    def add_cwl_war(self, cwl_war: CwlWar) -> None:
        """Добавляет CWL war в unit of work.

        Args:
            cwl_war: Новая модель войны ЛВК.
        """

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в unit of work.

        Args:
            api_error: Модель ошибки внешнего API.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemySyncCwlRepository:
    """SQLAlchemy repository для sync cwl job."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает active-кланы в стабильном порядке.

        Returns:
            Tuple active-кланов.
        """
        result = await self._session.execute(
            select(Clan).where(Clan.is_active.is_(True)).order_by(Clan.id)
        )
        return tuple(result.scalars().all())

    async def get_cwl_season(self, *, clan_id: int, season: str) -> CwlSeason | None:
        """Возвращает CWL season клана.

        Args:
            clan_id: DB ID клана.
            season: Ключ сезона.

        Returns:
            Модель сезона или `None`.
        """
        result = await self._session.execute(
            select(CwlSeason).where(
                CwlSeason.clan_id == clan_id,
                CwlSeason.season == season,
            )
        )
        return result.scalar_one_or_none()

    def add_cwl_season(self, cwl_season: CwlSeason) -> None:
        """Добавляет CWL season в текущую session.

        Args:
            cwl_season: Новая модель сезона.
        """
        self._session.add(cwl_season)

    async def get_cwl_war_by_tag(self, war_tag: str) -> CwlWar | None:
        """Возвращает CWL war по unique war tag.

        Args:
            war_tag: War tag из Clash API.

        Returns:
            Модель войны или `None`.
        """
        result = await self._session.execute(select(CwlWar).where(CwlWar.war_tag == war_tag))
        return result.scalar_one_or_none()

    def add_cwl_war(self, cwl_war: CwlWar) -> None:
        """Добавляет CWL war в текущую session.

        Args:
            cwl_war: Новая модель войны ЛВК.
        """
        self._session.add(cwl_war)

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в текущую session.

        Args:
            api_error: Модель ошибки внешнего API.
        """
        self._session.add(api_error)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class CwlWarData:
    """Нормализованные данные конкретной войны ЛВК."""

    cwl_season_id: int
    round_number: int
    war_tag: str
    state: str
    our_clan_tag: str
    opponent_clan_tag: str
    start_time: datetime
    end_time: datetime
    our_stars: int
    opponent_stars: int
    our_destruction: Decimal
    opponent_destruction: Decimal


@dataclass(frozen=True, slots=True)
class SyncCwlJobResult:
    """Результат одного запуска sync cwl job."""

    discovered_count: int
    synced_group_count: int
    no_cwl_count: int
    failed_group_count: int
    failed_war_count: int
    skipped_inactive_count: int
    skipped_placeholder_war_count: int
    skipped_unrelated_war_count: int
    created_season_count: int
    updated_season_count: int
    created_war_count: int
    updated_war_count: int


class SyncCwlJob:
    """Job синхронизации ЛВК active-кланов из Clash API."""

    def __init__(
        self,
        *,
        repository: SyncCwlRepository,
        clash_client: ClashCwlProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository для ЛВК и ошибок API.
            clash_client: Clash API client или совместимый provider.
            clock: Источник текущего времени для тестов.
        """
        self._repository = repository
        self._clash_client = clash_client
        self._clock = clock or _utc_now

    @classmethod
    def from_session(
        cls,
        *,
        session: AsyncSession,
        clash_client: ClashCwlProvider,
    ) -> "SyncCwlJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            clash_client: Clash API client или совместимый provider.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemySyncCwlRepository(session),
            clash_client=clash_client,
        )

    async def run(self, context: WorkerJobContext) -> SyncCwlJobResult:
        """Синхронизирует League Group и войны ЛВК.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        clans = await self._repository.list_active_clans()

        synced_group_count = 0
        no_cwl_count = 0
        failed_group_count = 0
        failed_war_count = 0
        skipped_inactive_count = 0
        skipped_placeholder_war_count = 0
        skipped_unrelated_war_count = 0
        created_season_count = 0
        updated_season_count = 0
        created_war_count = 0
        updated_war_count = 0

        for clan in clans:
            if context.should_stop:
                break

            if not clan.is_active:
                skipped_inactive_count += 1
                continue

            try:
                league_group = await self._clash_client.get_cwl_league_group(clan.tag)
            except ClashNotFoundError:
                no_cwl_count += 1
                continue
            except ClashApiError as error:
                self._handle_clash_api_error(
                    entity_type=_CWL_GROUP_ENTITY_TYPE,
                    entity_tag=clan.tag,
                    error=error,
                    worker_name=context.job_name,
                )
                failed_group_count += 1
                continue

            observed_at = self._clock()
            cwl_season, is_created = await self._get_or_create_cwl_season(
                clan=clan,
                league_group=league_group,
                observed_at=observed_at,
            )
            if is_created:
                created_season_count += 1
                await self._repository.flush()
            else:
                updated_season_count += 1

            for round_number, war_tag in _iter_round_war_tags(league_group):
                if context.should_stop:
                    break

                if not _is_real_war_tag(war_tag):
                    skipped_placeholder_war_count += 1
                    continue

                try:
                    cwl_war = await self._clash_client.get_cwl_war(war_tag)
                except ClashApiError as error:
                    self._handle_clash_api_error(
                        entity_type=_CWL_WAR_ENTITY_TYPE,
                        entity_tag=war_tag,
                        error=error,
                        worker_name=context.job_name,
                    )
                    failed_war_count += 1
                    continue

                cwl_war_data = _build_cwl_war_data(
                    cwl_season=cwl_season,
                    tracked_clan_tag=clan.tag,
                    round_number=round_number,
                    requested_war_tag=war_tag,
                    cwl_war=cwl_war,
                )
                if cwl_war_data is None:
                    skipped_unrelated_war_count += 1
                    continue

                existing_war = await self._repository.get_cwl_war_by_tag(cwl_war_data.war_tag)
                if existing_war is None:
                    self._repository.add_cwl_war(_build_cwl_war(cwl_war_data))
                    created_war_count += 1
                else:
                    _apply_cwl_war_update(existing_war, cwl_war_data)
                    updated_war_count += 1

            synced_group_count += 1

        await self._repository.flush()

        return SyncCwlJobResult(
            discovered_count=len(clans),
            synced_group_count=synced_group_count,
            no_cwl_count=no_cwl_count,
            failed_group_count=failed_group_count,
            failed_war_count=failed_war_count,
            skipped_inactive_count=skipped_inactive_count,
            skipped_placeholder_war_count=skipped_placeholder_war_count,
            skipped_unrelated_war_count=skipped_unrelated_war_count,
            created_season_count=created_season_count,
            updated_season_count=updated_season_count,
            created_war_count=created_war_count,
            updated_war_count=updated_war_count,
        )

    async def _get_or_create_cwl_season(
        self,
        *,
        clan: Clan,
        league_group: ClashCwlLeagueGroup,
        observed_at: datetime,
    ) -> tuple[CwlSeason, bool]:
        """Возвращает существующий сезон ЛВК или создаёт новый.

        Args:
            clan: Отслеживаемый клан.
            league_group: DTO League Group.
            observed_at: Время обнаружения.

        Returns:
            Tuple `(season, is_created)`.
        """
        clan_id = _required_model_id(clan, model_name="Clan")
        cwl_season = await self._repository.get_cwl_season(
            clan_id=clan_id,
            season=league_group.season,
        )

        if cwl_season is None:
            cwl_season = CwlSeason(
                clan_id=clan_id,
                season=league_group.season,
                state=league_group.state,
                started_at=observed_at,
                ended_at=observed_at if _is_ended_state(league_group.state) else None,
            )
            self._repository.add_cwl_season(cwl_season)
            return cwl_season, True

        cwl_season.state = league_group.state
        if _is_ended_state(league_group.state) and cwl_season.ended_at is None:
            cwl_season.ended_at = observed_at

        return cwl_season, False

    def _handle_clash_api_error(
        self,
        *,
        entity_type: str,
        entity_tag: str,
        error: ClashApiError,
        worker_name: str,
    ) -> None:
        """Обрабатывает typed ошибку Clash API.

        Args:
            entity_type: Тип сущности для `api_errors`.
            entity_tag: Тег сущности.
            error: Typed Clash API exception.
            worker_name: Имя текущей worker job для debug-контекста.
        """
        error_context = map_clash_api_error_to_context(
            error,
            entity_type=entity_type,
            entity_tag=entity_tag,
            worker_name=worker_name,
            retry_count=0,
        )
        self._repository.add_api_error(ApiError(**error_context.to_api_error_values()))


def register_sync_cwl_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует sync cwl job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=SYNC_CWL_JOB_NAME)
    async def sync_cwl(context: WorkerJobContext) -> None:
        """Запускает синхронизацию ЛВК внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("sync_cwl требует DB session.")

        async with ClashApiClient.from_settings() as clash_client:
            job = SyncCwlJob.from_session(
                session=context.session,
                clash_client=clash_client,
            )
            await job.run(context)


def _iter_round_war_tags(league_group: ClashCwlLeagueGroup) -> tuple[tuple[int, str], ...]:
    """Возвращает пары round_number/war_tag из League Group.

    Args:
        league_group: DTO League Group.

    Returns:
        Tuple пар `(round_number, war_tag)`.
    """
    result: list[tuple[int, str]] = []

    for round_index, war_tags in enumerate(league_group.rounds, start=1):
        for war_tag in war_tags:
            result.append((round_index, war_tag))

    return tuple(result)


def _is_real_war_tag(war_tag: str) -> bool:
    """Проверяет, является ли war tag реальной войной.

    Args:
        war_tag: War tag из League Group.

    Returns:
        `True`, если это не placeholder.
    """
    normalized = war_tag.strip().upper()
    return bool(normalized) and normalized not in _PLACEHOLDER_WAR_TAGS


def _build_cwl_war_data(
    *,
    cwl_season: CwlSeason,
    tracked_clan_tag: str,
    round_number: int,
    requested_war_tag: str,
    cwl_war: ClashCwlWar,
) -> CwlWarData | None:
    """Собирает данные конкретной CWL-war для tracked clan.

    Args:
        cwl_season: Сезон ЛВК tracked clan.
        tracked_clan_tag: Тег tracked clan.
        round_number: Номер раунда из League Group.
        requested_war_tag: War tag, по которому был сделан запрос.
        cwl_war: DTO конкретной войны ЛВК.

    Returns:
        Данные войны или `None`, если tracked clan в этой войне не участвует.
    """
    tracked_tag = normalize_clan_tag(tracked_clan_tag)
    sides = _select_tracked_sides(cwl_war, tracked_clan_tag=tracked_tag)
    if sides is None:
        return None

    our_side, opponent_side = sides
    opponent_tag = _required_string(opponent_side.tag, field_name="opponent.tag")
    war_tag = normalize_clan_tag(cwl_war.tag or requested_war_tag)

    return CwlWarData(
        cwl_season_id=_required_model_id(cwl_season, model_name="CwlSeason"),
        round_number=_required_positive_int(round_number, field_name="round_number"),
        war_tag=war_tag,
        state=cwl_war.state,
        our_clan_tag=tracked_tag,
        opponent_clan_tag=opponent_tag,
        start_time=_parse_clash_datetime(cwl_war.start_time, field_name="startTime"),
        end_time=_parse_clash_datetime(cwl_war.end_time, field_name="endTime"),
        our_stars=our_side.stars or 0,
        opponent_stars=opponent_side.stars or 0,
        our_destruction=_decimal_from_optional(our_side.destruction_percentage),
        opponent_destruction=_decimal_from_optional(opponent_side.destruction_percentage),
    )


def _select_tracked_sides(
    cwl_war: ClashCwlWar,
    *,
    tracked_clan_tag: str,
) -> tuple[ClashWarSideSummary, ClashWarSideSummary] | None:
    """Выбирает нашу и вражескую стороны CWL-war.

    Args:
        cwl_war: DTO войны ЛВК.
        tracked_clan_tag: Нормализованный тег tracked clan.

    Returns:
        Tuple `(our_side, opponent_side)` или `None`, если tracked clan не участвует.

    Raises:
        ValueError: Если tracked clan найден, но opponent side отсутствует.
    """
    if cwl_war.clan is not None and cwl_war.clan.tag == tracked_clan_tag:
        if cwl_war.opponent is None:
            raise ValueError("CWL war должен содержать opponent side.")
        return cwl_war.clan, cwl_war.opponent

    if cwl_war.opponent is not None and cwl_war.opponent.tag == tracked_clan_tag:
        if cwl_war.clan is None:
            raise ValueError("CWL war должен содержать clan side.")
        return cwl_war.opponent, cwl_war.clan

    return None


def _build_cwl_war(cwl_war_data: CwlWarData) -> CwlWar:
    """Создаёт модель CWL war.

    Args:
        cwl_war_data: Нормализованные данные войны.

    Returns:
        Новая модель `CwlWar`.
    """
    return CwlWar(
        cwl_season_id=cwl_war_data.cwl_season_id,
        round_number=cwl_war_data.round_number,
        war_tag=cwl_war_data.war_tag,
        state=cwl_war_data.state,
        our_clan_tag=cwl_war_data.our_clan_tag,
        opponent_clan_tag=cwl_war_data.opponent_clan_tag,
        start_time=cwl_war_data.start_time,
        end_time=cwl_war_data.end_time,
        our_stars=cwl_war_data.our_stars,
        opponent_stars=cwl_war_data.opponent_stars,
        our_destruction=cwl_war_data.our_destruction,
        opponent_destruction=cwl_war_data.opponent_destruction,
    )


def _apply_cwl_war_update(cwl_war: CwlWar, cwl_war_data: CwlWarData) -> None:
    """Обновляет существующую CWL war.

    Args:
        cwl_war: Существующая модель войны ЛВК.
        cwl_war_data: Актуальные данные войны.
    """
    cwl_war.cwl_season_id = cwl_war_data.cwl_season_id
    cwl_war.round_number = cwl_war_data.round_number
    cwl_war.state = cwl_war_data.state
    cwl_war.our_clan_tag = cwl_war_data.our_clan_tag
    cwl_war.opponent_clan_tag = cwl_war_data.opponent_clan_tag
    cwl_war.start_time = cwl_war_data.start_time
    cwl_war.end_time = cwl_war_data.end_time
    cwl_war.our_stars = cwl_war_data.our_stars
    cwl_war.opponent_stars = cwl_war_data.opponent_stars
    cwl_war.our_destruction = cwl_war_data.our_destruction
    cwl_war.opponent_destruction = cwl_war_data.opponent_destruction


def _is_ended_state(value: str) -> bool:
    """Проверяет ended-state League Group.

    Args:
        value: Состояние из Clash API.

    Returns:
        `True`, если состояние означает завершение.
    """
    return value.replace("_", "").replace("-", "").strip().lower() in _ENDED_STATES


def _parse_clash_datetime(value: str | None, *, field_name: str) -> datetime:
    """Парсит datetime из формата Clash API или ISO-строки.

    Args:
        value: Строка времени из Clash API.
        field_name: Название поля для текста ошибки.

    Returns:
        Timezone-aware UTC datetime.
    """
    normalized = _required_string(value, field_name=field_name)

    for date_format in ("%Y%m%dT%H%M%S.%fZ", "%Y%m%dT%H%M%SZ"):
        try:
            return datetime.strptime(normalized, date_format).replace(tzinfo=UTC)
        except ValueError:
            pass

    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} имеет неподдерживаемый формат datetime.") from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} должен содержать timezone.")

    return parsed.astimezone(UTC)


def _required_string(value: str | None, *, field_name: str) -> str:
    """Достаёт обязательную непустую строку.

    Args:
        value: Строковое значение.
        field_name: Название поля для текста ошибки.

    Returns:
        Непустая строка.
    """
    if value is None:
        raise ValueError(f"{field_name} не может быть пустым.")

    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} не может быть пустым.")

    return normalized


def _required_positive_int(value: int | None, *, field_name: str) -> int:
    """Достаёт обязательное положительное целое число.

    Args:
        value: Значение.
        field_name: Название поля для текста ошибки.

    Returns:
        Положительное число.
    """
    if value is None:
        raise ValueError(f"{field_name} не может быть пустым.")

    if value <= 0:
        raise ValueError(f"{field_name} должен быть больше 0.")

    return value


def _decimal_from_optional(value: float | int | None) -> Decimal:
    """Преобразует число API в Decimal.

    Args:
        value: Число из Clash API или `None`.

    Returns:
        Decimal-значение для SQLAlchemy Numeric.
    """
    if value is None:
        return Decimal("0")

    return Decimal(str(value))


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

    raise ValueError(f"{model_name} должен быть сохранён в БД.")


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "SYNC_CWL_JOB_NAME",
    "ClashCwlProvider",
    "CwlWarData",
    "SqlAlchemySyncCwlRepository",
    "SyncCwlJob",
    "SyncCwlJobResult",
    "SyncCwlRepository",
    "register_sync_cwl_job",
]

```


## FILE: app/worker/jobs/sync_raids.py

```python
"""Worker job синхронизации рейдов столицы клана."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiError, Clan, RaidMember, RaidSeason
from app.domain.enums import ClanType, RaidMemberStatus
from app.integrations.clash import (
    ClashApiClient,
    ClashApiError,
    ClashCapitalRaidSeason,
    map_clash_api_error_to_context,
)
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

SYNC_RAIDS_JOB_NAME = "sync_raids"

_RAID_ENTITY_TYPE = "capital_raid_seasons"
_RAID_EXPECTED_ATTACKS = 6
_RAID_SEASONS_LIMIT = 1
_RAID_SYNC_CLAN_TYPES = frozenset({ClanType.MAIN.value, ClanType.ACADEMY.value})


class ClashRaidSeasonsProvider(Protocol):
    """Contract Clash API provider для синхронизации рейдов."""

    async def get_capital_raid_seasons(
        self,
        clan_tag: str,
        *,
        limit: int | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> list[ClashCapitalRaidSeason]:
        """Получает рейдовые сезоны столицы клана.

        Args:
            clan_tag: Нормализованный тег клана.
            limit: Ограничение количества сезонов.
            after: Pagination marker after.
            before: Pagination marker before.

        Returns:
            Список рейдовых сезонов из Clash API.
        """


class SyncRaidsRepository(Protocol):
    """Repository contract для sync raids job."""

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает active-кланы.

        Returns:
            Tuple active-кланов.
        """

    async def get_raid_season_by_start_time(
        self,
        *,
        clan_id: int,
        start_time: datetime,
    ) -> RaidSeason | None:
        """Возвращает raid season по клану и времени старта.

        Args:
            clan_id: DB ID клана.
            start_time: Время начала рейдового сезона.

        Returns:
            Модель рейдового сезона или `None`.
        """

    def add_raid_season(self, raid_season: RaidSeason) -> None:
        """Добавляет raid season в unit of work.

        Args:
            raid_season: Новая модель рейдового сезона.
        """

    async def replace_raid_members(
        self,
        *,
        raid_season: RaidSeason,
        members: tuple[RaidMember, ...],
    ) -> None:
        """Заменяет участников рейдового сезона.

        Args:
            raid_season: Модель рейдового сезона.
            members: Актуальные участники сезона.
        """

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в unit of work.

        Args:
            api_error: Модель ошибки внешнего API.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemySyncRaidsRepository:
    """SQLAlchemy repository для sync raids job."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает active-кланы в стабильном порядке.

        Returns:
            Tuple active-кланов.
        """
        result = await self._session.execute(
            select(Clan)
            .where(
                Clan.is_active.is_(True),
                Clan.type.in_(sorted(_RAID_SYNC_CLAN_TYPES)),
            )
            .order_by(Clan.id)
        )
        return tuple(result.scalars().all())

    async def get_raid_season_by_start_time(
        self,
        *,
        clan_id: int,
        start_time: datetime,
    ) -> RaidSeason | None:
        """Возвращает raid season по клану и start_time.

        Args:
            clan_id: DB ID клана.
            start_time: Время начала рейдового сезона.

        Returns:
            Модель сезона или `None`.
        """
        result = await self._session.execute(
            select(RaidSeason).where(
                RaidSeason.clan_id == clan_id,
                RaidSeason.start_time == start_time,
            )
        )
        return result.scalar_one_or_none()

    def add_raid_season(self, raid_season: RaidSeason) -> None:
        """Добавляет raid season в текущую session.

        Args:
            raid_season: Новая модель рейдового сезона.
        """
        self._session.add(raid_season)

    async def replace_raid_members(
        self,
        *,
        raid_season: RaidSeason,
        members: tuple[RaidMember, ...],
    ) -> None:
        """Заменяет участников рейдового сезона без удаления season.

        Args:
            raid_season: Модель рейдового сезона.
            members: Актуальные участники сезона.
        """
        await self._session.flush()
        raid_season_id = _required_model_id(raid_season, model_name="RaidSeason")

        await self._session.execute(
            delete(RaidMember).where(RaidMember.raid_season_id == raid_season_id)
        )

        for member in members:
            member.raid_season_id = raid_season_id
            member.raid_season = raid_season
            self._session.add(member)

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в текущую session.

        Args:
            api_error: Модель ошибки внешнего API.
        """
        self._session.add(api_error)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class RaidSeasonData:
    """Нормализованные данные рейдового сезона."""

    clan_id: int
    state: str
    start_time: datetime
    end_time: datetime
    capital_total_loot: int
    raids_completed: int
    total_attacks: int
    enemy_districts_destroyed: int
    offensive_reward: int
    defensive_reward: int
    snapshot_at: datetime


@dataclass(frozen=True, slots=True)
class SyncRaidsJobResult:
    """Результат одного запуска sync raids job."""

    discovered_count: int
    synced_count: int
    no_raid_count: int
    failed_count: int
    skipped_inactive_count: int
    skipped_unsupported_clan_count: int
    created_count: int
    updated_count: int
    member_count: int


class SyncRaidsJob:
    """Job синхронизации рейдов столицы active-кланов."""

    def __init__(
        self,
        *,
        repository: SyncRaidsRepository,
        clash_client: ClashRaidSeasonsProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository для рейдов и ошибок API.
            clash_client: Clash API client или совместимый provider.
            clock: Источник текущего времени для тестов.
        """
        self._repository = repository
        self._clash_client = clash_client
        self._clock = clock or _utc_now

    @classmethod
    def from_session(
        cls,
        *,
        session: AsyncSession,
        clash_client: ClashRaidSeasonsProvider,
    ) -> "SyncRaidsJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            clash_client: Clash API client или совместимый provider.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemySyncRaidsRepository(session),
            clash_client=clash_client,
        )

    async def run(self, context: WorkerJobContext) -> SyncRaidsJobResult:
        """Синхронизирует последние рейдовые сезоны.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        clans = await self._repository.list_active_clans()

        synced_count = 0
        no_raid_count = 0
        failed_count = 0
        skipped_inactive_count = 0
        skipped_unsupported_clan_count = 0
        created_count = 0
        updated_count = 0
        member_count = 0

        for clan in clans:
            if context.should_stop:
                break

            if not clan.is_active:
                skipped_inactive_count += 1
                continue

            if clan.type not in _RAID_SYNC_CLAN_TYPES:
                skipped_unsupported_clan_count += 1
                continue

            try:
                raid_seasons = await self._clash_client.get_capital_raid_seasons(
                    clan.tag,
                    limit=_RAID_SEASONS_LIMIT,
                )
            except ClashApiError as error:
                self._handle_clash_api_error(
                    clan=clan,
                    error=error,
                    worker_name=context.job_name,
                )
                failed_count += 1
                continue

            if not raid_seasons:
                no_raid_count += 1
                continue

            raid_season_payload = raid_seasons[0]
            snapshot_at = self._clock()
            season_data = _build_raid_season_data(
                clan=clan,
                raid_season=raid_season_payload,
                snapshot_at=snapshot_at,
            )
            raid_season = await self._repository.get_raid_season_by_start_time(
                clan_id=season_data.clan_id,
                start_time=season_data.start_time,
            )

            if raid_season is None:
                raid_season = _build_raid_season(season_data)
                self._repository.add_raid_season(raid_season)
                created_count += 1
            else:
                _apply_raid_season_update(raid_season, season_data)
                updated_count += 1

            members = _build_raid_members(raid_season_payload)
            await self._repository.replace_raid_members(
                raid_season=raid_season,
                members=members,
            )

            synced_count += 1
            member_count += len(members)

        await self._repository.flush()

        return SyncRaidsJobResult(
            discovered_count=len(clans),
            synced_count=synced_count,
            no_raid_count=no_raid_count,
            failed_count=failed_count,
            skipped_inactive_count=skipped_inactive_count,
            skipped_unsupported_clan_count=skipped_unsupported_clan_count,
            created_count=created_count,
            updated_count=updated_count,
            member_count=member_count,
        )

    def _handle_clash_api_error(
        self,
        *,
        clan: Clan,
        error: ClashApiError,
        worker_name: str,
    ) -> None:
        """Обрабатывает typed ошибку Clash API для рейдов клана.

        Args:
            clan: Клан, при синхронизации рейдов которого возникла ошибка.
            error: Typed Clash API exception.
            worker_name: Имя текущей worker job для debug-контекста.
        """
        error_context = map_clash_api_error_to_context(
            error,
            entity_type=_RAID_ENTITY_TYPE,
            entity_tag=clan.tag,
            worker_name=worker_name,
            retry_count=0,
        )
        self._repository.add_api_error(ApiError(**error_context.to_api_error_values()))


def register_sync_raids_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует sync raids job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=SYNC_RAIDS_JOB_NAME)
    async def sync_raids(context: WorkerJobContext) -> None:
        """Запускает синхронизацию рейдов внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("sync_raids требует DB session.")

        async with ClashApiClient.from_settings() as clash_client:
            job = SyncRaidsJob.from_session(
                session=context.session,
                clash_client=clash_client,
            )
            await job.run(context)


def _build_raid_season_data(
    *,
    clan: Clan,
    raid_season: ClashCapitalRaidSeason,
    snapshot_at: datetime,
) -> RaidSeasonData:
    """Собирает данные рейдового сезона.

    Args:
        clan: Отслеживаемый клан.
        raid_season: DTO рейдового сезона.
        snapshot_at: Время синхронизации.

    Returns:
        Нормализованные данные сезона.
    """
    return RaidSeasonData(
        clan_id=_required_model_id(clan, model_name="Clan"),
        state=raid_season.state,
        start_time=_parse_clash_datetime(raid_season.start_time, field_name="startTime"),
        end_time=_parse_clash_datetime(raid_season.end_time, field_name="endTime"),
        capital_total_loot=raid_season.capital_total_loot or 0,
        raids_completed=raid_season.raids_completed or 0,
        total_attacks=raid_season.total_attacks or 0,
        enemy_districts_destroyed=raid_season.enemy_districts_destroyed or 0,
        offensive_reward=raid_season.offensive_reward or 0,
        defensive_reward=raid_season.defensive_reward or 0,
        snapshot_at=snapshot_at,
    )


def _build_raid_season(season_data: RaidSeasonData) -> RaidSeason:
    """Создаёт модель рейдового сезона.

    Args:
        season_data: Нормализованные данные сезона.

    Returns:
        Новая модель `RaidSeason`.
    """
    return RaidSeason(
        clan_id=season_data.clan_id,
        state=season_data.state,
        start_time=season_data.start_time,
        end_time=season_data.end_time,
        capital_total_loot=season_data.capital_total_loot,
        raids_completed=season_data.raids_completed,
        total_attacks=season_data.total_attacks,
        enemy_districts_destroyed=season_data.enemy_districts_destroyed,
        offensive_reward=season_data.offensive_reward,
        defensive_reward=season_data.defensive_reward,
        snapshot_at=season_data.snapshot_at,
    )


def _apply_raid_season_update(raid_season: RaidSeason, season_data: RaidSeasonData) -> None:
    """Обновляет существующий рейдовый сезон.

    Args:
        raid_season: Существующая модель сезона.
        season_data: Актуальные данные сезона.
    """
    raid_season.state = season_data.state
    raid_season.end_time = season_data.end_time
    raid_season.capital_total_loot = season_data.capital_total_loot
    raid_season.raids_completed = season_data.raids_completed
    raid_season.total_attacks = season_data.total_attacks
    raid_season.enemy_districts_destroyed = season_data.enemy_districts_destroyed
    raid_season.offensive_reward = season_data.offensive_reward
    raid_season.defensive_reward = season_data.defensive_reward
    raid_season.snapshot_at = season_data.snapshot_at


def _build_raid_members(raid_season: ClashCapitalRaidSeason) -> tuple[RaidMember, ...]:
    """Создаёт участников рейдового сезона.

    Args:
        raid_season: DTO рейдового сезона.

    Returns:
        Tuple моделей участников рейда.
    """
    members: list[RaidMember] = []

    for member in raid_season.members:
        attacks = _normalize_attacks(member.attacks)
        members.append(
            RaidMember(
                player_tag=member.player_tag,
                name=member.name,
                attacks=attacks,
                project_expected_attacks=_RAID_EXPECTED_ATTACKS,
                capital_resources_looted=member.capital_resources_looted or 0,
                status=_build_raid_member_status(attacks).value,
            )
        )

    return tuple(members)


def _build_raid_member_status(attacks: int) -> RaidMemberStatus:
    """Вычисляет статус участника рейдов.

    Args:
        attacks: Количество атак участника.

    Returns:
        Доменный статус участника рейдов.
    """
    if attacks >= _RAID_EXPECTED_ATTACKS:
        return RaidMemberStatus.RAID_FULL

    if attacks == _RAID_EXPECTED_ATTACKS - 1:
        return RaidMemberStatus.RAID_INCOMPLETE

    return RaidMemberStatus.RAID_MISSED


def _normalize_attacks(value: int | None) -> int:
    """Нормализует количество атак из Clash API.

    Args:
        value: Количество атак из API или `None`.

    Returns:
        Неотрицательное количество атак.
    """
    if value is None:
        return 0

    return max(value, 0)


def _parse_clash_datetime(value: str | None, *, field_name: str) -> datetime:
    """Парсит datetime из формата Clash API или ISO-строки.

    Args:
        value: Строка времени из Clash API.
        field_name: Название поля для текста ошибки.

    Returns:
        Timezone-aware UTC datetime.
    """
    normalized = _required_string(value, field_name=field_name)

    for date_format in ("%Y%m%dT%H%M%S.%fZ", "%Y%m%dT%H%M%SZ"):
        try:
            return datetime.strptime(normalized, date_format).replace(tzinfo=UTC)
        except ValueError:
            pass

    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} имеет неподдерживаемый формат datetime.") from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} должен содержать timezone.")

    return parsed.astimezone(UTC)


def _required_string(value: str | None, *, field_name: str) -> str:
    """Достаёт обязательную непустую строку.

    Args:
        value: Строковое значение.
        field_name: Название поля для текста ошибки.

    Returns:
        Непустая строка.
    """
    if value is None:
        raise ValueError(f"{field_name} не может быть пустым.")

    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} не может быть пустым.")

    return normalized


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

    raise ValueError(f"{model_name} должен быть сохранён в БД.")


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "SYNC_RAIDS_JOB_NAME",
    "ClashRaidSeasonsProvider",
    "RaidSeasonData",
    "SqlAlchemySyncRaidsRepository",
    "SyncRaidsJob",
    "SyncRaidsJobResult",
    "SyncRaidsRepository",
    "register_sync_raids_job",
]

```


## FILE: app/worker/jobs/detect_unlinked_accounts.py

```python
"""Worker job обнаружения непривязанных аккаунтов."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Clan, ClanMemberSnapshot, PlayerAccount
from app.domain.enums import ClanType
from app.services.kick_candidates import KickCandidateCreationResult, KickCandidateService
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

DETECT_UNLINKED_ACCOUNTS_JOB_NAME = "detect_unlinked_accounts"

_DETECT_UNLINKED_CLAN_TYPES = frozenset({ClanType.MAIN.value, ClanType.ACADEMY.value})


@dataclass(frozen=True, slots=True)
class UnlinkedAccountCandidate:
    """Данные непривязанного аккаунта для проверки кандидата на кик."""

    clan: Clan
    player_tag: str
    first_seen_at: datetime


class UnlinkedAccountCandidateCreator(Protocol):
    """Contract сервиса создания кандидатов по непривязанным аккаунтам."""

    async def create_for_unlinked_account(
        self,
        *,
        clan: Clan,
        player_tag: str,
        first_seen_at: datetime,
        observed_at: datetime | None = None,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата по непривязанному аккаунту.

        Args:
            clan: Клан, где найден непривязанный аккаунт.
            player_tag: Тег непривязанного аккаунта.
            first_seen_at: Первое появление аккаунта в API-составе.
            observed_at: Время текущей проверки.

        Returns:
            Результат создания кандидата.
        """


class DetectUnlinkedAccountsRepository(Protocol):
    """Repository contract для detect unlinked accounts job."""

    async def list_unlinked_current_members(self) -> tuple[UnlinkedAccountCandidate, ...]:
        """Возвращает текущие непривязанные аккаунты.

        Returns:
            Tuple данных непривязанных аккаунтов.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyDetectUnlinkedAccountsRepository:
    """SQLAlchemy repository для detect unlinked accounts job."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_unlinked_current_members(self) -> tuple[UnlinkedAccountCandidate, ...]:
        """Возвращает current members без активной Telegram-привязки.

        Returns:
            Tuple данных непривязанных аккаунтов.
        """
        linked_account_exists = (
            select(PlayerAccount.id)
            .where(
                PlayerAccount.player_tag == ClanMemberSnapshot.player_tag,
                PlayerAccount.is_active.is_(True),
                PlayerAccount.telegram_user_id.is_not(None),
            )
            .exists()
        )
        result = await self._session.execute(
            select(ClanMemberSnapshot, Clan)
            .join(Clan, Clan.id == ClanMemberSnapshot.clan_id)
            .where(
                ClanMemberSnapshot.is_current.is_(True),
                Clan.is_active.is_(True),
                Clan.type.in_(sorted(_DETECT_UNLINKED_CLAN_TYPES)),
                ~exists(linked_account_exists.select()),
            )
            .order_by(Clan.id, ClanMemberSnapshot.player_tag)
        )

        return tuple(
            UnlinkedAccountCandidate(
                clan=clan,
                player_tag=snapshot.player_tag,
                first_seen_at=snapshot.first_seen_at,
            )
            for snapshot, clan in result.all()
        )

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class DetectUnlinkedAccountsJobResult:
    """Результат одного запуска detect unlinked accounts job."""

    discovered_count: int
    created_count: int
    existing_count: int
    not_old_enough_count: int


class DetectUnlinkedAccountsJob:
    """Job обнаружения current accounts без Telegram-привязки."""

    def __init__(
        self,
        *,
        repository: DetectUnlinkedAccountsRepository,
        candidate_creator: UnlinkedAccountCandidateCreator,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository для поиска непривязанных current members.
            candidate_creator: Сервис создания кандидатов на кик.
            clock: Источник текущего времени для тестов.
        """
        self._repository = repository
        self._candidate_creator = candidate_creator
        self._clock = clock or _utc_now

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "DetectUnlinkedAccountsJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemyDetectUnlinkedAccountsRepository(session),
            candidate_creator=KickCandidateService.from_session(session=session),
        )

    async def run(self, context: WorkerJobContext) -> DetectUnlinkedAccountsJobResult:
        """Создаёт candidates по непривязанным current members старше 3 дней.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        candidates = await self._repository.list_unlinked_current_members()

        created_count = 0
        existing_count = 0
        not_old_enough_count = 0
        observed_at = self._clock()

        for candidate in candidates:
            if context.should_stop:
                break

            result = await self._candidate_creator.create_for_unlinked_account(
                clan=candidate.clan,
                player_tag=candidate.player_tag,
                first_seen_at=candidate.first_seen_at,
                observed_at=observed_at,
            )

            if result.created:
                created_count += 1
            elif result.candidate is not None:
                existing_count += 1
            else:
                not_old_enough_count += 1

        await self._repository.flush()

        return DetectUnlinkedAccountsJobResult(
            discovered_count=len(candidates),
            created_count=created_count,
            existing_count=existing_count,
            not_old_enough_count=not_old_enough_count,
        )


def register_detect_unlinked_accounts_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует detect unlinked accounts job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=DETECT_UNLINKED_ACCOUNTS_JOB_NAME)
    async def detect_unlinked_accounts(context: WorkerJobContext) -> None:
        """Запускает обнаружение непривязанных аккаунтов внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("detect_unlinked_accounts требует DB session.")

        job = DetectUnlinkedAccountsJob.from_session(session=context.session)
        await job.run(context)


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "DETECT_UNLINKED_ACCOUNTS_JOB_NAME",
    "DetectUnlinkedAccountsJob",
    "DetectUnlinkedAccountsJobResult",
    "DetectUnlinkedAccountsRepository",
    "SqlAlchemyDetectUnlinkedAccountsRepository",
    "UnlinkedAccountCandidate",
    "UnlinkedAccountCandidateCreator",
    "register_detect_unlinked_accounts_job",
]

```


## FILE: app/worker/jobs/detect_kick_candidates.py

```python
"""Worker job обнаружения кандидатов на кик."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Clan, ClanMemberSnapshot, PlayerAccount, TelegramUser, Warning
from app.domain.enums import ClanType, WarningSource, WarningStatus
from app.services.kick_candidates import KickCandidateCreationResult, KickCandidateService
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

DETECT_KICK_CANDIDATES_JOB_NAME = "detect_kick_candidates"

_NO_CWL_SEASON_KEY = "no-cwl-season"
_TRACKED_CLAN_TYPES_FOR_PRESENCE = frozenset(
    {
        ClanType.MAIN.value,
        ClanType.ACADEMY.value,
        ClanType.FREEZER.value,
    }
)


@dataclass(frozen=True, slots=True)
class ImpactfulWarningsCandidate:
    """Данные TelegramUser с active impactful warn."""

    telegram_user: TelegramUser
    warnings: tuple[Warning, ...]
    season_key: str


@dataclass(frozen=True, slots=True)
class LinkedAccountPresence:
    """Текущее присутствие одного привязанного аккаунта."""

    player_tag: str
    is_current_in_tracked_clan: bool


@dataclass(frozen=True, slots=True)
class TelegramUserAccountPresence:
    """Текущее присутствие всех активных аккаунтов TelegramUser."""

    telegram_user: TelegramUser
    accounts: tuple[LinkedAccountPresence, ...]


class KickCandidateCreator(Protocol):
    """Contract сервиса создания кандидатов на кик."""

    async def create_for_two_impactful_warnings(
        self,
        *,
        telegram_user: TelegramUser,
        warnings: list[Warning],
        season_key: str,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата по двум active impactful warn.

        Args:
            telegram_user: TelegramUser-кандидат.
            warnings: Warn-записи пользователя.
            season_key: Ключ сезона для dedup.

        Returns:
            Результат создания кандидата.
        """

    async def create_for_all_accounts_left(
        self,
        *,
        telegram_user: TelegramUser,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата по уходу всех аккаунтов TelegramUser.

        Args:
            telegram_user: TelegramUser-кандидат.

        Returns:
            Результат создания кандидата.
        """

    async def create_for_linked_account_left(
        self,
        *,
        telegram_user: TelegramUser,
        player_tag: str,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата по уходу одного linked account.

        Args:
            telegram_user: TelegramUser владельца аккаунта.
            player_tag: Тег ушедшего аккаунта.

        Returns:
            Результат создания кандидата.
        """


class DetectKickCandidatesRepository(Protocol):
    """Repository contract для detect kick candidates job."""

    async def list_impactful_warning_candidates(self) -> tuple[ImpactfulWarningsCandidate, ...]:
        """Возвращает TelegramUser с active impactful system warn.

        Returns:
            Tuple кандидатов по warn.
        """

    async def list_account_presence_candidates(self) -> tuple[TelegramUserAccountPresence, ...]:
        """Возвращает присутствие активных linked accounts по TelegramUser.

        Returns:
            Tuple данных присутствия аккаунтов.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyDetectKickCandidatesRepository:
    """SQLAlchemy repository для detect kick candidates job."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_impactful_warning_candidates(self) -> tuple[ImpactfulWarningsCandidate, ...]:
        """Возвращает TelegramUser с active impactful system warn.

        Returns:
            Tuple кандидатов по warn.
        """
        result = await self._session.execute(
            select(TelegramUser, Warning)
            .join(Warning, Warning.telegram_user_id == TelegramUser.id)
            .where(
                Warning.status == WarningStatus.ACTIVE.value,
                Warning.source == WarningSource.SYSTEM.value,
                Warning.is_impactful.is_(True),
            )
            .order_by(TelegramUser.id, Warning.id)
        )

        warnings_by_user_id: dict[int, list[Warning]] = {}
        users_by_id: dict[int, TelegramUser] = {}
        for telegram_user, warning in result.all():
            telegram_user_id = _required_model_id(telegram_user, model_name="TelegramUser")
            users_by_id[telegram_user_id] = telegram_user
            warnings_by_user_id.setdefault(telegram_user_id, []).append(warning)

        candidates: list[ImpactfulWarningsCandidate] = []
        for telegram_user_id, warnings in warnings_by_user_id.items():
            if len(warnings) < 2:
                continue

            candidates.append(
                ImpactfulWarningsCandidate(
                    telegram_user=users_by_id[telegram_user_id],
                    warnings=tuple(warnings),
                    season_key=_resolve_warning_season_key(warnings),
                )
            )

        return tuple(candidates)

    async def list_account_presence_candidates(self) -> tuple[TelegramUserAccountPresence, ...]:
        """Возвращает присутствие active linked accounts в tracked clans.

        Returns:
            Tuple данных присутствия аккаунтов.
        """
        current_member_exists = (
            select(ClanMemberSnapshot.id)
            .join(Clan, Clan.id == ClanMemberSnapshot.clan_id)
            .where(
                ClanMemberSnapshot.player_tag == PlayerAccount.player_tag,
                ClanMemberSnapshot.is_current.is_(True),
                Clan.is_active.is_(True),
                Clan.type.in_(sorted(_TRACKED_CLAN_TYPES_FOR_PRESENCE)),
            )
            .exists()
        )
        result = await self._session.execute(
            select(
                TelegramUser,
                PlayerAccount,
                current_member_exists.label("is_current_in_tracked_clan"),
            )
            .join(PlayerAccount, PlayerAccount.telegram_user_id == TelegramUser.id)
            .where(
                PlayerAccount.is_active.is_(True),
                PlayerAccount.telegram_user_id.is_not(None),
            )
            .order_by(TelegramUser.id, PlayerAccount.player_tag)
        )

        accounts_by_user_id: dict[int, list[LinkedAccountPresence]] = {}
        users_by_id: dict[int, TelegramUser] = {}
        for telegram_user, account, is_current_in_tracked_clan in result.all():
            telegram_user_id = _required_model_id(telegram_user, model_name="TelegramUser")
            users_by_id[telegram_user_id] = telegram_user
            accounts_by_user_id.setdefault(telegram_user_id, []).append(
                LinkedAccountPresence(
                    player_tag=account.player_tag,
                    is_current_in_tracked_clan=bool(is_current_in_tracked_clan),
                )
            )

        return tuple(
            TelegramUserAccountPresence(
                telegram_user=users_by_id[telegram_user_id],
                accounts=tuple(accounts),
            )
            for telegram_user_id, accounts in accounts_by_user_id.items()
        )

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class DetectKickCandidatesJobResult:
    """Результат одного запуска detect kick candidates job."""

    warning_candidate_users_count: int
    warning_candidates_created_count: int
    warning_candidates_existing_count: int
    warning_candidates_skipped_count: int
    account_presence_users_count: int
    all_accounts_left_created_count: int
    all_accounts_left_existing_count: int
    linked_account_left_created_count: int
    linked_account_left_existing_count: int


class DetectKickCandidatesJob:
    """Job обнаружения кандидатов на кик по warn и уходу аккаунтов."""

    def __init__(
        self,
        *,
        repository: DetectKickCandidatesRepository,
        candidate_creator: KickCandidateCreator,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository для поиска кандидатов.
            candidate_creator: Сервис создания кандидатов на кик.
        """
        self._repository = repository
        self._candidate_creator = candidate_creator

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "DetectKickCandidatesJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemyDetectKickCandidatesRepository(session),
            candidate_creator=KickCandidateService.from_session(session=session),
        )

    async def run(self, context: WorkerJobContext) -> DetectKickCandidatesJobResult:
        """Создаёт candidates по warn и уходу linked accounts.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        warning_candidates = await self._repository.list_impactful_warning_candidates()
        account_presences = await self._repository.list_account_presence_candidates()

        warning_candidates_created_count = 0
        warning_candidates_existing_count = 0
        warning_candidates_skipped_count = 0
        all_accounts_left_created_count = 0
        all_accounts_left_existing_count = 0
        linked_account_left_created_count = 0
        linked_account_left_existing_count = 0

        for candidate in warning_candidates:
            if context.should_stop:
                break

            result = await self._candidate_creator.create_for_two_impactful_warnings(
                telegram_user=candidate.telegram_user,
                warnings=list(candidate.warnings),
                season_key=candidate.season_key,
            )
            if result.created:
                warning_candidates_created_count += 1
            elif result.candidate is not None:
                warning_candidates_existing_count += 1
            else:
                warning_candidates_skipped_count += 1

        for presence in account_presences:
            if context.should_stop:
                break

            current_accounts = tuple(
                account for account in presence.accounts if account.is_current_in_tracked_clan
            )
            left_accounts = tuple(
                account for account in presence.accounts if not account.is_current_in_tracked_clan
            )

            if not left_accounts:
                continue

            if not current_accounts:
                result = await self._candidate_creator.create_for_all_accounts_left(
                    telegram_user=presence.telegram_user,
                )
                if result.created:
                    all_accounts_left_created_count += 1
                elif result.candidate is not None:
                    all_accounts_left_existing_count += 1
                continue

            for account in left_accounts:
                result = await self._candidate_creator.create_for_linked_account_left(
                    telegram_user=presence.telegram_user,
                    player_tag=account.player_tag,
                )
                if result.created:
                    linked_account_left_created_count += 1
                elif result.candidate is not None:
                    linked_account_left_existing_count += 1

        await self._repository.flush()

        return DetectKickCandidatesJobResult(
            warning_candidate_users_count=len(warning_candidates),
            warning_candidates_created_count=warning_candidates_created_count,
            warning_candidates_existing_count=warning_candidates_existing_count,
            warning_candidates_skipped_count=warning_candidates_skipped_count,
            account_presence_users_count=len(account_presences),
            all_accounts_left_created_count=all_accounts_left_created_count,
            all_accounts_left_existing_count=all_accounts_left_existing_count,
            linked_account_left_created_count=linked_account_left_created_count,
            linked_account_left_existing_count=linked_account_left_existing_count,
        )


def register_detect_kick_candidates_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует detect kick candidates job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=DETECT_KICK_CANDIDATES_JOB_NAME)
    async def detect_kick_candidates(context: WorkerJobContext) -> None:
        """Запускает обнаружение кандидатов на кик внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("detect_kick_candidates требует DB session.")

        job = DetectKickCandidatesJob.from_session(session=context.session)
        await job.run(context)


def _resolve_warning_season_key(warnings: list[Warning]) -> str:
    """Возвращает season key для candidate по двум warn.

    Args:
        warnings: Active impactful warn пользователя.

    Returns:
        CWL season key из warn или fallback для warn без сезона.
    """
    season_keys = sorted(
        {
            warning.created_cwl_season_key.strip()
            for warning in warnings
            if warning.created_cwl_season_key is not None and warning.created_cwl_season_key.strip()
        }
    )
    if season_keys:
        return season_keys[-1]

    return _NO_CWL_SEASON_KEY


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

    raise ValueError(f"{model_name} должен быть сохранён в БД.")


__all__ = [
    "DETECT_KICK_CANDIDATES_JOB_NAME",
    "DetectKickCandidatesJob",
    "DetectKickCandidatesJobResult",
    "DetectKickCandidatesRepository",
    "ImpactfulWarningsCandidate",
    "KickCandidateCreator",
    "LinkedAccountPresence",
    "SqlAlchemyDetectKickCandidatesRepository",
    "TelegramUserAccountPresence",
    "register_detect_kick_candidates_job",
]

```


## FILE: frontend/templates/base.html

```html
{% import "shared/macros/assets.html" as assets %}
{% import "shared/macros/badges.html" as badges %}
{% import "shared/macros/buttons.html" as buttons %}
{% import "shared/macros/cards.html" as cards %}
{% import "shared/macros/empty_states.html" as empty_states %}
{% import "shared/macros/frames.html" as frames %}
{% import "shared/macros/progress.html" as progress %}
{% import "shared/macros/status.html" as status %}
{% import "shared/macros/tables.html" as tables %}

<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ page_title }} · BestiaryNavigator_bot</title>
    <link rel="stylesheet" href="{{ url_for('static', path='/css/app.css') }}">
  </head>
  <body data-bn-admin="{{ 'true' if web_context.is_admin else 'false' }}" data-bn-readonly="{{ 'true' if web_context.is_readonly else 'false' }}">
    <div class="bn-layout">
      <aside class="bn-sidebar" aria-label="Основная навигация">
        <a class="bn-sidebar__brand" href="/">BestiaryNavigator_bot</a>
        <nav class="bn-sidebar__nav" aria-label="Разделы">
          <a class="bn-sidebar__link" href="/" {% if active_nav|default("dashboard") == "dashboard" %}aria-current="page"{% endif %}>Dashboard</a>
          {% if web_context.is_admin %}
            <a class="bn-sidebar__link" href="/admin/settings/clans" {% if active_nav|default("") == "admin_clans" %}aria-current="page"{% endif %} data-admin-only="true">
              Настройки кланов
            </a>
            <a class="bn-sidebar__link" href="/admin/settings/telegram" {% if active_nav|default("") == "admin_telegram" %}aria-current="page"{% endif %} data-admin-only="true">
              Настройки Telegram
            </a>
            <a class="bn-sidebar__link" href="/admin/settings/api-errors" {% if active_nav|default("") == "admin_api_errors" %}aria-current="page"{% endif %} data-admin-only="true">
              Dev API errors
            </a>
          {% endif %}
        </nav>
      </aside>

      <div class="bn-layout__content">
        <header class="bn-topbar">
          <a class="bn-brand" href="/">BestiaryNavigator_bot</a>
          <span class="bn-topbar__status">UI skeleton</span>
          <span class="bn-topbar__status">{{ web_context.role_label }}</span>
          {% if web_context.is_admin %}
            <span class="bn-topbar__status" data-admin-only="true">Админ</span>
            <a class="bn-button bn-button--ghost" href="/auth/logout" data-admin-only="true">Выйти</a>
          {% endif %}
        </header>

        <main class="bn-page">
          {% block content %}{% endblock %}
        </main>
      </div>
    </div>
    {% block scripts %}{% endblock %}
  </body>
</html>
```


## FILE: frontend/templates/dashboard/index.html

```html
{% extends "base.html" %}

{% block content %}
  <section class="bn-section bn-dashboard" aria-labelledby="dashboard-title">
    <div class="bn-section-header">
      {{ badges.badge("UI skeleton", variant="info") }}
      <h1 id="dashboard-title" class="bn-page-title">Dashboard</h1>
      <p class="bn-page-lead">
        Ранний web-skeleton подключён. Данные кланов, войн, рейдов, ЛВК,
        warn и Telegram-маршрутов будут добавлены в следующих feature-коммитах.
      </p>
    </div>

    {% call cards.card(class_name="bn-empty-state") %}
      {{ badges.badge("Пустое состояние", variant="muted") }}
      <div>
        <p class="bn-card-eyebrow">Нет данных</p>
        <h2 class="bn-card-title">Кланы ещё не загружены</h2>
        <p class="bn-card-text">
          Эта страница не зависит от базы данных и проверяет только Jinja2,
          базовый layout и подключение static CSS.
        </p>
      </div>
      <div class="bn-empty-state__actions">
        {{ buttons.button_link("/health", "Проверить health", variant="primary") }}
        {{ buttons.button_link("/static/css/app.css", "Открыть CSS", variant="ghost") }}
      </div>
    {% endcall %}
  </section>
{% endblock %}
```


## FILE: frontend/templates/admin/clans/settings.html

```html
{% extends "base.html" %}

{% block content %}
  <section class="bn-section bn-admin-clans" aria-labelledby="clan-settings-title">
    <div class="bn-section-header">
      {{ badges.badge("Admin", variant="info") }}
      <h1 id="clan-settings-title" class="bn-page-title">Настройки кланов</h1>
      <p class="bn-page-lead">
        Управление отслеживаемыми кланами. Удаление здесь означает отключение мониторинга:
        история, события, warn и связанные данные не удаляются.
      </p>

      {% if notice %}
        <p class="bn-form-message bn-form-message--success" role="status">{{ notice }}</p>
      {% endif %}
    </div>

    <div class="bn-admin-clans__forms">
      {% call cards.card(title="Проверить клан", eyebrow="Clash API", class_name="bn-admin-clans__form-card") %}
        <form class="bn-form" method="post" action="/admin/settings/clans/check">
          <div class="bn-field">
            <label class="bn-label" for="check-clan-tag">Тег клана</label>
            <input
              class="bn-input"
              id="check-clan-tag"
              name="clan_tag"
              type="text"
              value="{{ form_values.get('clan_tag', '') }}"
              placeholder="#2ABC"
              autocomplete="off"
            >
          </div>

          <div class="bn-form__actions">
            <button class="bn-button bn-button--ghost" type="submit">Проверить тег</button>
          </div>

          {% if action_feedback and action_feedback.target == "check" %}
            <p
              class="bn-form-message bn-form-message--{{ action_feedback.variant }}"
              data-action-target="check"
            >
              {{ action_feedback.message }}
            </p>
          {% endif %}
        </form>

        {% if verified_clan %}
          <div class="bn-admin-clans__verified">
            {{ badges.badge("Клан найден", variant="info") }}
            <p class="bn-card-title">{{ verified_clan.name }}</p>
            <p class="bn-card-text">
              {{ verified_clan.tag }}
              {% if verified_clan.level %} · Уровень {{ verified_clan.level }}{% endif %}
              {% if verified_clan.members_count is not none %}
                · Участников {{ verified_clan.members_count }}
              {% endif %}
            </p>
          </div>
        {% endif %}
      {% endcall %}

      {% call cards.card(title="Добавить клан", eyebrow="Мониторинг", class_name="bn-admin-clans__form-card") %}
        <form class="bn-form" method="post" action="/admin/settings/clans/add">
          <div class="bn-form__grid">
            <div class="bn-field">
              <label class="bn-label" for="add-clan-tag">Тег клана</label>
              <input
                class="bn-input"
                id="add-clan-tag"
                name="clan_tag"
                type="text"
                value="{{ form_values.get('clan_tag', '') }}"
                placeholder="#2ABC"
                autocomplete="off"
              >
            </div>

            <div class="bn-field">
              <label class="bn-label" for="add-clan-type">Тип клана</label>
              <select class="bn-select" id="add-clan-type" name="clan_type">
                {% for clan_type in clan_types %}
                  <option value="{{ clan_type.value }}" {% if form_values.get('clan_type') == clan_type.value %}selected{% endif %}>
                    {{ clan_type.label }}
                  </option>
                {% endfor %}
              </select>
            </div>
          </div>

          <p class="bn-card-text">
            Добавление всегда сначала проверяет тег через Clash API.
          </p>

          <div class="bn-form__actions">
            <button class="bn-button bn-button--primary" type="submit">Добавить клан</button>
          </div>

          {% if action_feedback and action_feedback.target == "add" %}
            <p
              class="bn-form-message bn-form-message--{{ action_feedback.variant }}"
              data-action-target="add"
            >
              {{ action_feedback.message }}
            </p>
          {% endif %}
        </form>
      {% endcall %}
    </div>

    {% if clans %}
      <div class="bn-admin-clans__grid" aria-label="Список кланов">
        {% for clan in clans %}
          {% call cards.card(class_name="bn-admin-clan-card{% if not clan.is_active %} bn-admin-clan-card--disabled{% endif %}") %}
            <div class="bn-admin-clan-card__header">
              <div>
                <p class="bn-card-eyebrow">{{ clan.tag }}</p>
                <h2 class="bn-card-title">{{ clan.name }}</h2>
              </div>
              <div class="bn-admin-clan-card__badges">
                {{ badges.badge(clan.type_label, variant="info") }}
                {% if clan.is_active %}
                  {{ badges.badge("Активен", variant="info") }}
                {% else %}
                  {{ badges.badge("Отключён", variant="muted") }}
                {% endif %}
              </div>
            </div>

            {% if clan.badge_url %}
              <img class="bn-admin-clan-card__badge" src="{{ clan.badge_url }}" alt="Эмблема {{ clan.name }}">
            {% endif %}

            <dl class="bn-admin-clan-card__meta">
              <div>
                <dt>Тип</dt>
                <dd>{{ clan.type_label }}</dd>
              </div>
              <div>
                <dt>Уровень</dt>
                <dd>{{ clan.level if clan.level is not none else "нет данных" }}</dd>
              </div>
              <div>
                <dt>Sync</dt>
                <dd>{{ clan.sync_status }}</dd>
              </div>
              <div>
                <dt>Последняя синхронизация</dt>
                <dd>{{ clan.last_sync_text }}</dd>
              </div>
            </dl>

            <div class="bn-admin-clan-card__actions">
              <form class="bn-form bn-form--inline" method="post" action="/admin/settings/clans/type">
                <input type="hidden" name="clan_tag" value="{{ clan.tag }}">
                <div class="bn-field">
                  <label class="bn-label" for="clan-type-{{ loop.index }}">Тип клана</label>
                  <select class="bn-select" id="clan-type-{{ loop.index }}" name="clan_type">
                    {% for clan_type in clan_types %}
                      <option value="{{ clan_type.value }}" {% if clan.type == clan_type.value %}selected{% endif %}>
                        {{ clan_type.label }}
                      </option>
                    {% endfor %}
                  </select>
                </div>
                <button class="bn-button bn-button--ghost" type="submit">Изменить тип</button>
              </form>

              <p class="bn-card-text">
                Смена типа меняет правила warn, напоминаний и боевых блоков.
              </p>

              {% if action_feedback and action_feedback.target == clan.type_action_target %}
                <p
                  class="bn-form-message bn-form-message--{{ action_feedback.variant }}"
                  data-action-target="{{ clan.type_action_target }}"
                >
                  {{ action_feedback.message }}
                </p>
              {% endif %}

              <form class="bn-form bn-form--inline" method="post" action="/admin/settings/clans/refresh">
                <input type="hidden" name="clan_tag" value="{{ clan.tag }}">
                <button class="bn-button bn-button--ghost" type="submit">Обновить из Clash API</button>
              </form>

              {% if action_feedback and action_feedback.target == clan.refresh_action_target %}
                <p
                  class="bn-form-message bn-form-message--{{ action_feedback.variant }}"
                  data-action-target="{{ clan.refresh_action_target }}"
                >
                  {{ action_feedback.message }}
                </p>
              {% endif %}

              <form class="bn-form bn-form--inline" method="post" action="/admin/settings/clans/deactivate">
                <input type="hidden" name="clan_tag" value="{{ clan.tag }}">
                <button
                  class="bn-button bn-button--ghost"
                  type="submit"
                  {% if not clan.is_active %}disabled{% endif %}
                >
                  Удалить / деактивировать
                </button>
              </form>

              {% if action_feedback and action_feedback.target == clan.deactivate_action_target %}
                <p
                  class="bn-form-message bn-form-message--{{ action_feedback.variant }}"
                  data-action-target="{{ clan.deactivate_action_target }}"
                >
                  {{ action_feedback.message }}
                </p>
              {% endif %}
            </div>
          {% endcall %}
        {% endfor %}
      </div>
    {% else %}
      {% call empty_states.empty_state(
        title="Кланы ещё не добавлены",
        text="Добавь первый клан через форму выше. Перед сохранением система проверит тег через Clash API.",
        eyebrow="Пусто"
      ) %}
      {% endcall %}
    {% endif %}
  </section>
{% endblock %}
```


## FILE: frontend/templates/admin/telegram/settings.html

```html
{% extends "base.html" %}

{% block content %}
  <section
    class="bn-section bn-admin-telegram"
    aria-labelledby="telegram-settings-title"
    data-admin-telegram-settings
  >
    <div class="bn-section-header">
      {{ badges.badge("Admin", variant="info") }}
      <h1 id="telegram-settings-title" class="bn-page-title">Настройки Telegram</h1>
      <p class="bn-page-lead">
        Управление маршрутами уведомлений. Команду регистрации нужно скопировать и отправить
        в нужный Telegram-чат или топик. Бот сам сохранит chat_id и message_thread_id.
      </p>
    </div>

    {% if clans %}
      <div class="bn-admin-telegram__grid" aria-label="Telegram routes по кланам">
        {% for clan in clans %}
          {% call cards.card(class_name="bn-telegram-clan-card") %}
            <div class="bn-telegram-clan-card__header">
              <div>
                <p class="bn-card-eyebrow">{{ clan.tag }}</p>
                <h2 class="bn-card-title">{{ clan.name }}</h2>
                <p class="bn-card-text">{{ clan.type_label }}</p>
              </div>

              {% if clan.badge_url %}
                <img
                  class="bn-telegram-clan-card__badge"
                  src="{{ clan.badge_url }}"
                  alt="Эмблема {{ clan.name }}"
                >
              {% endif %}
            </div>

            <div class="bn-notification-types" aria-label="Типы уведомлений">
              {% for item in clan.notification_types %}
                <article class="bn-notification-type" data-notification-type="{{ item.value }}">
                  <div class="bn-notification-type__header">
                    <div>
                      <p class="bn-card-eyebrow">{{ item.value }}</p>
                      <h3 class="bn-notification-type__title">{{ item.label }}</h3>
                    </div>
                    {{ badges.badge(item.status_label, variant=item.status_variant) }}
                  </div>

                  <div class="bn-command-copy">
                    <code class="bn-command-copy__value">{{ item.command }}</code>
                    <button
                      class="bn-button bn-button--ghost"
                      type="button"
                      data-copy-command="{{ item.command }}"
                    >
                      Скопировать команду
                    </button>
                  </div>

                  {% if item.routes %}
                    <div class="bn-route-list" aria-label="Подключённые маршруты">
                      {% for route in item.routes %}
                        <article
                          class="bn-route-row{% if not route.enabled %} bn-route-row--disabled{% endif %}"
                          data-route-id="{{ route.id }}"
                        >
                          <div class="bn-route-row__body">
                            <div>
                              <p class="bn-route-row__title">{{ route.chat_title }}</p>
                              <p class="bn-card-text">
                                chat_id: {{ route.chat_id }}
                                · topic:
                                {% if route.message_thread_id is not none %}
                                  {{ route.message_thread_id }}
                                {% else %}
                                  без топика
                                {% endif %}
                              </p>
                            </div>
                            {{ badges.badge(route.status_label, variant=route.status_variant, class_name="bn-route-row__status") }}
                          </div>

                          <div class="bn-route-row__actions">
                            <button
                              class="bn-button bn-button--ghost"
                              type="button"
                              data-route-test-url="{{ route.test_url }}"
                              {% if not route.enabled %}disabled{% endif %}
                            >
                              Тест
                            </button>
                            <button
                              class="bn-button bn-button--ghost"
                              type="button"
                              data-route-disable-url="{{ route.disable_url }}"
                              {% if not route.enabled %}disabled{% endif %}
                            >
                              Отключить
                            </button>
                          </div>

                          <p class="bn-route-row__message" data-route-message aria-live="polite"></p>
                        </article>
                      {% endfor %}
                    </div>
                  {% else %}
                    <p class="bn-card-text">Маршрут ещё не подключён.</p>
                  {% endif %}
                </article>
              {% endfor %}
            </div>
          {% endcall %}
        {% endfor %}
      </div>
    {% else %}
      {% call empty_states.empty_state(
        title="Кланы ещё не добавлены",
        text="Сначала добавь кланы на странице настроек кланов, затем подключай Telegram-маршруты.",
        eyebrow="Пусто"
      ) %}
        {{ buttons.button_link("/admin/settings/clans", "Открыть настройки кланов", variant="primary") }}
      {% endcall %}
    {% endif %}
  </section>
{% endblock %}

{% block scripts %}
  <script src="{{ url_for('static', path='/js/pages/admin_telegram_settings.js') }}" defer></script>
{% endblock %}
```


## FILE: frontend/templates/admin/api_errors/settings.html

```html
{% extends "base.html" %}

{% block content %}
  <section
    class="bn-section bn-admin-api-errors"
    aria-labelledby="api-errors-title"
    data-admin-api-errors
  >
    <div class="bn-section-header">
      {{ badges.badge("Dev", variant="info") }}
      <h1 id="api-errors-title" class="bn-page-title">Dev API errors</h1>
      <p class="bn-page-lead">
        Краткий и debug-режим ошибок внешних API. Debug-блоки раскрываются через
        браузерный details/summary, без тяжёлого JS. Чувствительные строки редактируются.
      </p>
    </div>

    {% call cards.card(title="Фильтр", eyebrow="Summary mode", class_name="bn-api-errors-filter") %}
      <form class="bn-form bn-api-errors-filter__form" method="get" action="/admin/settings/api-errors">
        <div class="bn-field">
          <label class="bn-label" for="api-error-status">Статус</label>
          <select class="bn-select" id="api-error-status" name="status">
            <option value="">Все статусы</option>
            {% for option in status_options %}
              <option value="{{ option.value }}" {% if status_filter == option.value %}selected{% endif %}>
                {{ option.label }}
              </option>
            {% endfor %}
          </select>
        </div>

        <div class="bn-field">
          <label class="bn-label" for="api-error-limit">Limit</label>
          <input
            class="bn-input"
            id="api-error-limit"
            name="limit"
            type="number"
            min="1"
            max="200"
            value="{{ limit }}"
          >
        </div>

        <div class="bn-form__actions">
          <button class="bn-button bn-button--primary" type="submit">Применить</button>
        </div>
      </form>

      <p class="bn-card-text">Показано записей: {{ total_count }}</p>
    {% endcall %}

    {% if errors %}
      <div class="bn-api-errors-list" aria-label="Список API errors">
        {% for error in errors %}
          {% call cards.card(class_name="bn-api-error-card bn-api-error-card--" ~ error.status_group) %}
            <div class="bn-api-error-card__header">
              <div>
                <p class="bn-card-eyebrow">{{ error.method }} · {{ error.endpoint }}</p>
                <h2 class="bn-api-error-card__title">{{ error.message }}</h2>
              </div>
              <div class="bn-api-error-card__badges">
                {{ badges.badge(error.status_label, variant=error.status_variant) }}
                {% if error.status_code is not none %}
                  {{ badges.badge("HTTP " ~ error.status_code, variant="muted") }}
                {% else %}
                  {{ badges.badge("No HTTP status", variant="muted") }}
                {% endif %}
              </div>
            </div>

            <dl class="bn-api-error-card__meta">
              <div>
                <dt>Entity</dt>
                <dd>
                  {{ error.entity_type or "нет данных" }}
                  {% if error.entity_tag %} · {{ error.entity_tag }}{% endif %}
                </dd>
              </div>
              <div>
                <dt>Worker</dt>
                <dd>{{ error.worker_name or "нет данных" }}</dd>
              </div>
              <div>
                <dt>Retry count</dt>
                <dd>{{ error.retry_count }}</dd>
              </div>
              <div>
                <dt>Created</dt>
                <dd>{{ error.created_at_text }}</dd>
              </div>
              <div>
                <dt>Status group</dt>
                <dd>{{ error.status_group }}</dd>
              </div>
              <div>
                <dt>Resolved</dt>
                <dd>{{ error.resolved_at_text or "не закрыта" }}</dd>
              </div>
            </dl>

            <details class="bn-api-error-debug">
              <summary>Debug</summary>
              <dl class="bn-api-error-debug__grid">
                <div>
                  <dt>Endpoint</dt>
                  <dd><code>{{ error.endpoint }}</code></dd>
                </div>
                <div>
                  <dt>Method</dt>
                  <dd><code>{{ error.method }}</code></dd>
                </div>
                <div>
                  <dt>Exception class</dt>
                  <dd><code>{{ error.exception_class or "нет данных" }}</code></dd>
                </div>
                <div>
                  <dt>Raw status</dt>
                  <dd><code>{{ error.status }}</code></dd>
                </div>
              </dl>

              <div class="bn-api-error-snippet">
                <p class="bn-card-eyebrow">Response snippet</p>
                <pre><code>{{ error.response_snippet or "нет данных" }}</code></pre>
              </div>
            </details>

            {% if error.can_resolve and error.resolve_url %}
              <div class="bn-api-error-card__actions">
                <button
                  class="bn-button bn-button--ghost"
                  type="button"
                  data-api-error-resolve-url="{{ error.resolve_url }}"
                >
                  Пометить resolved
                </button>
                <p class="bn-route-row__message" data-api-error-message aria-live="polite"></p>
              </div>
            {% endif %}
          {% endcall %}
        {% endfor %}
      </div>
    {% else %}
      {% call empty_states.empty_state(
        title="API errors не найдены",
        text="По выбранному фильтру ошибок нет.",
        eyebrow="Пусто"
      ) %}
      {% endcall %}
    {% endif %}
  </section>
{% endblock %}

{% block scripts %}
  <script src="{{ url_for('static', path='/js/pages/admin_api_errors.js') }}" defer></script>
{% endblock %}
```


## FILE: frontend/templates/shared/macros/assets.html

```html
{% macro image(src, alt, class_name="") -%}
  {% if src %}
    <img
      src="{{ src }}"
      alt="{{ alt }}"
      class="{{ class_name }}"
      loading="lazy"
    >
  {% else %}
    <span
      class="bn-asset-placeholder{% if class_name %} {{ class_name }}{% endif %}"
      aria-hidden="true"
    ></span>
  {% endif %}
{%- endmacro %}
```


## FILE: frontend/templates/shared/macros/badges.html

```html
{% macro badge(text, variant="muted", class_name="") -%}
  <span class="bn-badge bn-badge--{{ variant }}{% if class_name %} {{ class_name }}{% endif %}">
    {{ text }}
  </span>
{%- endmacro %}
```


## FILE: frontend/templates/shared/macros/buttons.html

```html
{% macro button_link(href, label, variant="ghost", class_name="", disabled=false) -%}
  <a
    class="bn-button bn-button--{{ variant }}{% if class_name %} {{ class_name }}{% endif %}"
    href="{{ href }}"
    {% if disabled %}aria-disabled="true" tabindex="-1"{% endif %}
  >
    {{ label }}
  </a>
{%- endmacro %}
```


## FILE: frontend/templates/shared/macros/cards.html

```html
{% macro card(title="", eyebrow="", text="", class_name="") -%}
  <article class="bn-card{% if class_name %} {{ class_name }}{% endif %}">
    <div class="bn-card-body">
      {% if eyebrow %}
        <p class="bn-card-eyebrow">{{ eyebrow }}</p>
      {% endif %}
      {% if title %}
        <h2 class="bn-card-title">{{ title }}</h2>
      {% endif %}
      {% if text %}
        <p class="bn-card-text">{{ text }}</p>
      {% endif %}
      {% if caller is defined %}
        {{ caller() }}
      {% endif %}
    </div>
  </article>
{%- endmacro %}
```


## FILE: frontend/templates/shared/macros/empty_states.html

```html
{% import "shared/macros/cards.html" as cards %}

{% macro empty_state(title, text, eyebrow="Нет данных", class_name="") -%}
  {% call cards.card(title=title, eyebrow=eyebrow, class_name="bn-empty-state " ~ class_name) %}
    <p class="bn-card-text">{{ text }}</p>
    {% if caller is defined %}
      <div class="bn-empty-state__actions">
        {{ caller() }}
      </div>
    {% endif %}
  {% endcall %}
{%- endmacro %}
```


## FILE: frontend/templates/shared/macros/frames.html

```html
{% macro frame(variant="default", class_name="") -%}
  <div class="bn-frame bn-frame--{{ variant }}{% if class_name %} {{ class_name }}{% endif %}">
    {% if caller is defined %}
      {{ caller() }}
    {% endif %}
  </div>
{%- endmacro %}
```


## FILE: frontend/templates/shared/macros/progress.html

```html
{% macro progress(value=0, max_value=100, label="") -%}
  <div
    class="bn-progress"
    role="progressbar"
    aria-valuenow="{{ value }}"
    aria-valuemin="0"
    aria-valuemax="{{ max_value }}"
    {% if label %}aria-label="{{ label }}"{% endif %}
  >
    <div class="bn-progress-track">
      <div class="bn-progress-fill"></div>
    </div>
  </div>
{%- endmacro %}
```


## FILE: frontend/templates/shared/macros/status.html

```html
{% import "shared/macros/badges.html" as badges %}

{% macro status_label(text, variant="muted", class_name="") -%}
  {{ badges.badge(text, variant=variant, class_name=class_name) }}
{%- endmacro %}
```


## FILE: frontend/templates/shared/macros/tables.html

```html
{% macro empty_table(message="Нет данных") -%}
  <div class="bn-table-empty">
    <p class="bn-card-text">{{ message }}</p>
  </div>
{%- endmacro %}
```


## FILE: frontend/static/css/app.css

```css
@import url("./tokens.css");
@import url("./base.css");
@import url("./components.css");
@import url("./utilities.css");
@import url("./pages/dashboard.css");
@import url("./pages/admin_clans.css");
@import url("./pages/admin_telegram.css");
@import url("./pages/admin_api_errors.css");

```


## FILE: frontend/static/css/tokens.css

```css
:root {
  color-scheme: dark;

  --bn-bg-page: #070a12;
  --bn-bg-sidebar: #090d16;
  --bn-bg-surface: #0e1420;
  --bn-bg-surface-2: #131b2a;
  --bn-bg-surface-3: #1a2436;

  --bn-text-main: #f8fafc;
  --bn-text-soft: #cbd5e1;
  --bn-text-muted: #7b8497;
  --bn-text-disabled: #4b5563;

  --bn-border-soft: rgba(148, 163, 184, 0.18);
  --bn-border-strong: rgba(226, 232, 240, 0.28);

  --bn-primary: #7c3aed;
  --bn-primary-soft: rgba(124, 58, 237, 0.18);

  --bn-status-ok: #22c55e;
  --bn-status-ok-soft: rgba(34, 197, 94, 0.18);

  --bn-status-warning: #f59e0b;
  --bn-status-warning-soft: rgba(245, 158, 11, 0.18);

  --bn-status-danger: #ef4444;
  --bn-status-danger-soft: rgba(239, 68, 68, 0.18);

  --bn-status-info: #38bdf8;
  --bn-status-info-soft: rgba(56, 189, 248, 0.18);

  --bn-status-muted: #7b8497;
  --bn-status-muted-soft: rgba(123, 132, 151, 0.18);

  --bn-status-gold: #facc15;
  --bn-status-gold-soft: rgba(250, 204, 21, 0.18);

  --bn-radius-sm: 0.5rem;
  --bn-radius-md: 0.875rem;
  --bn-radius-lg: 1.25rem;

  --bn-shadow-card: 0 18px 60px rgba(0, 0, 0, 0.24);
  --bn-topbar-bg: rgba(14, 20, 32, 0.86);
  --bn-card-glow: radial-gradient(circle at top left, var(--bn-primary-soft), transparent 34rem);
}
```


## FILE: frontend/static/css/base.css

```css
* {
  box-sizing: border-box;
}

html {
  min-height: 100%;
  background: var(--bn-bg-page);
}

body {
  min-height: 100%;
  margin: 0;
  font-family:
    Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bn-bg-page);
  color: var(--bn-text-main);
}

a {
  color: inherit;
}
```


## FILE: frontend/static/css/components.css

```css
.bn-layout {
  display: grid;
  min-height: 100vh;
  background: var(--bn-bg-page);
}

.bn-layout__content {
  min-width: 0;
}

.bn-sidebar {
  border-bottom: 1px solid var(--bn-border-soft);
  background: var(--bn-bg-sidebar);
  padding: 1rem;
}

.bn-sidebar__brand {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 900;
  letter-spacing: 0.02em;
  text-decoration: none;
}

.bn-sidebar__nav {
  display: flex;
  gap: 0.5rem;
  overflow-x: auto;
  padding-top: 1rem;
}

.bn-sidebar__link {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 2.5rem;
  border: 1px solid var(--bn-border-soft);
  border-radius: var(--bn-radius-md);
  background: var(--bn-bg-surface);
  color: var(--bn-text-soft);
  font-size: 0.875rem;
  font-weight: 700;
  padding: 0.625rem 0.875rem;
  text-decoration: none;
  white-space: nowrap;
}

.bn-sidebar__link:hover,
.bn-sidebar__link[aria-current="page"] {
  border-color: var(--bn-border-strong);
  background: var(--bn-primary-soft);
  color: var(--bn-text-main);
}

.bn-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  border-bottom: 1px solid var(--bn-border-soft);
  background: var(--bn-topbar-bg);
  backdrop-filter: blur(16px);
  padding: 1rem;
}

.bn-brand {
  font-weight: 800;
  letter-spacing: 0.02em;
  text-decoration: none;
}

.bn-topbar__status {
  color: var(--bn-text-muted);
  font-size: 0.875rem;
}

.bn-page {
  width: min(1120px, 100%);
  margin: 0 auto;
  padding: 1.25rem 1rem 2.5rem;
}

.bn-section {
  display: grid;
  gap: 1rem;
}

.bn-page-title {
  margin: 0;
  font-size: clamp(1.75rem, 6vw, 3rem);
  line-height: 1;
}

.bn-page-lead {
  max-width: 42rem;
  margin: 0;
  color: var(--bn-text-soft);
  line-height: 1.6;
}

.bn-card {
  border: 1px solid var(--bn-border-soft);
  border-radius: var(--bn-radius-lg);
  background: var(--bn-card-glow), var(--bn-bg-surface);
  box-shadow: var(--bn-shadow-card);
  padding: 1rem;
}

.bn-card:hover {
  border-color: var(--bn-border-strong);
}

.bn-section-header,
.bn-card-header,
.bn-card-body,
.bn-card-footer,
.bn-card__header,
.bn-card__body,
.bn-card__footer {
  display: grid;
  gap: 0.75rem;
}

.bn-card-eyebrow,
.bn-card__eyebrow {
  margin: 0 0 0.5rem;
  color: var(--bn-primary);
  font-size: 0.75rem;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.bn-card-title,
.bn-card__title {
  margin: 0 0 0.5rem;
  font-size: 1.25rem;
}

.bn-card-text,
.bn-card__text {
  margin: 0;
  color: var(--bn-text-soft);
  line-height: 1.6;
}

.bn-badge {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  min-height: 1.75rem;
  border: 1px solid var(--bn-border-soft);
  border-radius: 999px;
  background: var(--bn-status-muted-soft);
  color: var(--bn-text-soft);
  font-size: 0.75rem;
  font-weight: 800;
  letter-spacing: 0.04em;
  padding: 0.25rem 0.625rem;
  text-transform: uppercase;
}

.bn-badge--info {
  border-color: var(--bn-status-info-soft);
  background: var(--bn-status-info-soft);
  color: var(--bn-text-main);
}

.bn-badge--muted {
  border-color: var(--bn-border-soft);
  background: var(--bn-status-muted-soft);
  color: var(--bn-text-soft);
}

.bn-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 2.5rem;
  border: 1px solid var(--bn-border-soft);
  border-radius: var(--bn-radius-md);
  background: var(--bn-bg-surface-2);
  color: var(--bn-text-main);
  cursor: pointer;
  font: inherit;
  font-size: 0.875rem;
  font-weight: 800;
  padding: 0.625rem 0.875rem;
  text-decoration: none;
}

.bn-button:hover {
  border-color: var(--bn-border-strong);
  background: var(--bn-bg-surface-3);
}

.bn-button:focus-visible,
.bn-sidebar__link:focus-visible,
.bn-brand:focus-visible,
.bn-sidebar__brand:focus-visible {
  outline: 2px solid var(--bn-status-info);
  outline-offset: 3px;
}

.bn-button[disabled],
.bn-button[aria-disabled="true"] {
  color: var(--bn-text-disabled);
  cursor: not-allowed;
  opacity: 0.68;
}

.bn-button--primary {
  border-color: var(--bn-primary);
  background: var(--bn-primary);
  color: var(--bn-text-main);
}

.bn-button--ghost {
  background: transparent;
  color: var(--bn-text-soft);
}

.bn-empty-state {
  display: grid;
  gap: 1rem;
}

.bn-empty-state__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  padding-top: 0.25rem;
}

@media (min-width: 768px) {
  .bn-layout {
    grid-template-columns: 17rem minmax(0, 1fr);
  }

  .bn-sidebar {
    position: sticky;
    top: 0;
    min-height: 100vh;
    border-right: 1px solid var(--bn-border-soft);
    border-bottom: 0;
    padding: 1.25rem;
  }

  .bn-sidebar__nav {
    display: grid;
    overflow: visible;
  }

  .bn-topbar {
    padding-inline: 2rem;
  }

  .bn-page {
    padding: 2rem;
  }

  .bn-card {
    padding: 1.5rem;
  }

  .bn-empty-state {
    grid-template-columns: minmax(0, 1fr);
  }
}
```


## FILE: frontend/static/css/utilities.css

```css
.bn-sr-only {
  position: absolute;
  overflow: hidden;
  width: 1px;
  height: 1px;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
}
```


## FILE: frontend/static/css/pages/dashboard.css

```css
.bn-dashboard {
  display: grid;
  gap: 1rem;
}

```


## FILE: frontend/static/css/pages/admin_clans.css

```css
.bn-admin-clans {
  display: grid;
  gap: 1.25rem;
}

.bn-admin-clans__forms,
.bn-admin-clans__grid {
  display: grid;
  gap: 1rem;
}

.bn-admin-clans__verified {
  display: grid;
  gap: 0.5rem;
  border: 1px solid var(--bn-border-soft);
  border-radius: var(--bn-radius-md);
  background: var(--bn-primary-soft);
  padding: 0.875rem;
}

.bn-admin-clan-card--disabled {
  opacity: 0.72;
}

.bn-admin-clan-card__header {
  display: grid;
  gap: 0.75rem;
}

.bn-admin-clan-card__badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.bn-admin-clan-card__badge {
  width: 4rem;
  height: 4rem;
  border: 1px solid var(--bn-border-soft);
  border-radius: var(--bn-radius-md);
  background: var(--bn-bg-surface-2);
  object-fit: contain;
}

.bn-admin-clan-card__meta {
  display: grid;
  gap: 0.75rem;
  margin: 0;
}

.bn-admin-clan-card__meta div {
  display: grid;
  gap: 0.2rem;
  border-top: 1px solid var(--bn-border-soft);
  padding-top: 0.75rem;
}

.bn-admin-clan-card__meta dt {
  color: var(--bn-text-muted);
  font-size: 0.75rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.bn-admin-clan-card__meta dd {
  margin: 0;
  color: var(--bn-text-main);
  font-weight: 700;
}

.bn-admin-clan-card__actions,
.bn-form {
  display: grid;
  gap: 0.75rem;
}

.bn-form__grid {
  display: grid;
  gap: 0.75rem;
}

.bn-form__actions,
.bn-form--inline {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  align-items: end;
}

.bn-field {
  display: grid;
  gap: 0.375rem;
  min-width: 0;
}

.bn-label {
  color: var(--bn-text-soft);
  font-size: 0.8125rem;
  font-weight: 800;
}

.bn-input,
.bn-select {
  width: 100%;
  min-height: 2.75rem;
  border: 1px solid var(--bn-border-soft);
  border-radius: var(--bn-radius-md);
  background: var(--bn-bg-surface-2);
  color: var(--bn-text-main);
  font: inherit;
  padding: 0.625rem 0.75rem;
}

.bn-input:focus,
.bn-select:focus {
  border-color: var(--bn-border-strong);
  outline: 2px solid var(--bn-primary-soft);
  outline-offset: 2px;
}

.bn-form-message {
  border: 1px solid var(--bn-border-soft);
  border-radius: var(--bn-radius-md);
  margin: 0;
  padding: 0.75rem;
  line-height: 1.5;
}

.bn-form-message--error {
  border-color: var(--bn-status-danger-soft);
  background: var(--bn-status-danger-soft);
  color: var(--bn-text-main);
}

.bn-form-message--success {
  border-color: var(--bn-status-ok-soft);
  background: var(--bn-status-ok-soft);
  color: var(--bn-text-main);
}

@media (min-width: 768px) {
  .bn-admin-clans__forms,
  .bn-admin-clans__grid {
    grid-template-columns: repeat(2, minmax(16rem, 1fr));
  }

  .bn-admin-clan-card__header {
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: start;
  }

  .bn-admin-clan-card__meta {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .bn-form__grid {
    grid-template-columns: minmax(0, 1fr) 13rem;
  }

  .bn-form--inline .bn-field {
    min-width: 12rem;
  }
}
```


## FILE: frontend/static/css/pages/admin_telegram.css

```css
.bn-admin-telegram {
  display: grid;
  gap: 1.25rem;
}

.bn-admin-telegram__grid,
.bn-notification-types,
.bn-route-list {
  display: grid;
  gap: 1rem;
}

.bn-telegram-clan-card__header {
  display: grid;
  gap: 0.75rem;
}

.bn-telegram-clan-card__badge {
  width: 4rem;
  height: 4rem;
  border: 1px solid var(--bn-border-soft);
  border-radius: var(--bn-radius-md);
  background: var(--bn-bg-surface-2);
  object-fit: contain;
}

.bn-notification-type {
  display: grid;
  gap: 0.75rem;
  border: 1px solid var(--bn-border-soft);
  border-radius: var(--bn-radius-md);
  background: var(--bn-bg-surface-2);
  padding: 0.875rem;
}

.bn-notification-type__header,
.bn-route-row__body {
  display: grid;
  gap: 0.75rem;
}

.bn-notification-type__title,
.bn-route-row__title {
  margin: 0;
  color: var(--bn-text-main);
  font-size: 1rem;
}

.bn-command-copy {
  display: grid;
  gap: 0.75rem;
}

.bn-command-copy__value {
  overflow-x: auto;
  border: 1px solid var(--bn-border-soft);
  border-radius: var(--bn-radius-md);
  background: var(--bn-bg-surface);
  color: var(--bn-text-soft);
  padding: 0.75rem;
  white-space: nowrap;
}

.bn-route-row {
  display: grid;
  gap: 0.75rem;
  border: 1px solid var(--bn-border-soft);
  border-radius: var(--bn-radius-md);
  background: var(--bn-bg-surface);
  padding: 0.75rem;
}

.bn-route-row--disabled {
  opacity: 0.68;
}

.bn-route-row__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
}

.bn-route-row__message {
  min-height: 1.25rem;
  margin: 0;
  color: var(--bn-text-soft);
  font-size: 0.875rem;
}

.bn-route-row__message[data-state="success"] {
  color: var(--bn-status-ok);
}

.bn-route-row__message[data-state="error"] {
  color: var(--bn-status-danger);
}

@media (min-width: 768px) {
  .bn-admin-telegram__grid {
    grid-template-columns: repeat(2, minmax(18rem, 1fr));
  }

  .bn-telegram-clan-card__header,
  .bn-notification-type__header,
  .bn-route-row__body {
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: start;
  }

  .bn-command-copy {
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
  }
}
```


## FILE: frontend/static/css/pages/admin_api_errors.css

```css
.bn-admin-api-errors,
.bn-api-errors-list {
  display: grid;
  gap: 1.25rem;
}

.bn-api-errors-filter__form {
  display: grid;
  gap: 0.75rem;
}

.bn-api-error-card {
  min-width: 0;
}

.bn-api-error-card--unresolved {
  border-color: var(--bn-status-info-soft);
}

.bn-api-error-card--retrying {
  border-color: var(--bn-status-warning-soft);
}

.bn-api-error-card--resolved,
.bn-api-error-card--stale {
  opacity: 0.78;
}

.bn-api-error-card__header {
  display: grid;
  gap: 0.75rem;
}

.bn-api-error-card__title {
  overflow-wrap: anywhere;
  margin: 0;
  color: var(--bn-text-main);
  font-size: 1.125rem;
  line-height: 1.35;
}

.bn-api-error-card__badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.bn-api-error-card__meta,
.bn-api-error-debug__grid {
  display: grid;
  gap: 0.75rem;
  margin: 0;
}

.bn-api-error-card__meta div,
.bn-api-error-debug__grid div {
  display: grid;
  gap: 0.2rem;
  border-top: 1px solid var(--bn-border-soft);
  min-width: 0;
  padding-top: 0.75rem;
}

.bn-api-error-card__meta dt,
.bn-api-error-debug__grid dt {
  color: var(--bn-text-muted);
  font-size: 0.75rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.bn-api-error-card__meta dd,
.bn-api-error-debug__grid dd {
  overflow-wrap: anywhere;
  margin: 0;
  color: var(--bn-text-main);
  font-weight: 700;
}

.bn-api-error-debug {
  border: 1px solid var(--bn-border-soft);
  border-radius: var(--bn-radius-md);
  background: var(--bn-bg-surface-2);
  padding: 0.75rem;
}

.bn-api-error-debug summary {
  cursor: pointer;
  font-weight: 800;
}

.bn-api-error-snippet {
  display: grid;
  gap: 0.5rem;
  margin-top: 0.75rem;
}

.bn-api-error-snippet pre {
  overflow-x: auto;
  max-width: 100%;
  border: 1px solid var(--bn-border-soft);
  border-radius: var(--bn-radius-md);
  background: var(--bn-bg-surface);
  margin: 0;
  padding: 0.75rem;
  white-space: pre-wrap;
}

.bn-api-error-snippet code {
  overflow-wrap: anywhere;
  color: var(--bn-text-soft);
}

.bn-api-error-card__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  align-items: center;
}

@media (min-width: 768px) {
  .bn-api-errors-filter__form {
    grid-template-columns: minmax(0, 1fr) 10rem auto;
    align-items: end;
  }

  .bn-api-error-card__header {
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: start;
  }

  .bn-api-error-card__meta,
  .bn-api-error-debug__grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
```


## FILE: tests/test_web_skeleton.py

```python
"""Тесты раннего web-skeleton."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PROJECT_ROOT / "frontend" / "templates"
MACROS_DIR = TEMPLATES_DIR / "shared" / "macros"


def test_dashboard_returns_html_page() -> None:
    """Проверяет, что `/` отдаёт HTML dashboard skeleton."""
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "BestiaryNavigator_bot" in response.text
    assert "Dashboard" in response.text
    assert "/static/css/app.css" in response.text
    assert 'class="bn-layout"' in response.text
    assert 'data-bn-admin="false"' in response.text
    assert 'data-bn-readonly="true"' in response.text
    assert 'data-admin-only="true"' not in response.text
    assert "/admin/settings/clans" not in response.text
    assert "/admin/settings/telegram" not in response.text
    assert "/admin/settings/api-errors" not in response.text
    assert 'class="bn-sidebar"' in response.text
    assert 'class="bn-topbar"' in response.text
    assert 'class="bn-page"' in response.text
    assert 'class="bn-section-header"' in response.text
    assert 'class="bn-card-body"' in response.text
    assert 'class="bn-section bn-dashboard"' in response.text
    assert 'class="bn-card bn-empty-state"' in response.text
    assert "bn-badge" in response.text
    assert "bn-button" in response.text


def test_static_css_is_served() -> None:
    """Проверяет раздачу CSS через static mount."""
    with TestClient(app) as client:
        response = client.get("/static/css/app.css")

    assert response.status_code == 200
    assert '@import url("./tokens.css");' in response.text
    assert '@import url("./base.css");' in response.text
    assert '@import url("./components.css");' in response.text
    assert '@import url("./utilities.css");' in response.text
    assert '@import url("./pages/dashboard.css");' in response.text
    assert '@import url("./pages/admin_clans.css");' in response.text
    assert '@import url("./pages/admin_telegram.css");' in response.text
    assert '@import url("./pages/admin_api_errors.css");' in response.text


def test_css_layers_are_served() -> None:
    """Проверяет раздачу CSS-слоёв через static mount."""
    with TestClient(app) as client:
        tokens_response = client.get("/static/css/tokens.css")
        base_response = client.get("/static/css/base.css")
        components_response = client.get("/static/css/components.css")
        utilities_response = client.get("/static/css/utilities.css")
        dashboard_response = client.get("/static/css/pages/dashboard.css")
        admin_clans_response = client.get("/static/css/pages/admin_clans.css")
        admin_telegram_response = client.get("/static/css/pages/admin_telegram.css")
        admin_api_errors_response = client.get("/static/css/pages/admin_api_errors.css")

    assert tokens_response.status_code == 200
    assert base_response.status_code == 200
    assert components_response.status_code == 200
    assert utilities_response.status_code == 200
    assert dashboard_response.status_code == 200
    assert admin_clans_response.status_code == 200
    assert admin_telegram_response.status_code == 200
    assert admin_api_errors_response.status_code == 200

    assert "--bn-bg-page" in tokens_response.text
    assert "color-scheme: dark" in tokens_response.text
    assert "body" in base_response.text
    assert ".bn-card" in components_response.text
    assert ".bn-layout" in components_response.text
    assert ".bn-sidebar" in components_response.text
    assert ".bn-topbar" in components_response.text
    assert ".bn-page" in components_response.text
    assert ".bn-section" in components_response.text
    assert ".bn-card-header" in components_response.text
    assert ".bn-card-body" in components_response.text
    assert ".bn-card-footer" in components_response.text
    assert ".bn-badge" in components_response.text
    assert ".bn-button" in components_response.text
    assert ".bn-empty-state" in components_response.text
    assert "minmax(0, 1fr)" in components_response.text
    assert ".bn-sr-only" in utilities_response.text
    assert ".bn-dashboard" in dashboard_response.text
    assert ".bn-admin-clans" in admin_clans_response.text
    assert "minmax(16rem, 1fr)" in admin_clans_response.text
    assert ".bn-admin-telegram" in admin_telegram_response.text
    assert ".bn-notification-type" in admin_telegram_response.text
    assert ".bn-admin-api-errors" in admin_api_errors_response.text
    assert ".bn-api-error-card" in admin_api_errors_response.text


def test_templates_do_not_use_inline_styles() -> None:
    """Проверяет, что ранние шаблоны не используют inline CSS."""
    with TestClient(app) as client:
        response = client.get("/")

    assert 'style="' not in response.text


def test_shared_macro_files_exist() -> None:
    """Проверяет наличие всех shared Jinja macro-файлов."""
    expected_macro_files = {
        "assets.html",
        "badges.html",
        "buttons.html",
        "cards.html",
        "empty_states.html",
        "frames.html",
        "progress.html",
        "status.html",
        "tables.html",
    }

    actual_macro_files = {path.name for path in MACROS_DIR.iterdir() if path.is_file()}

    assert expected_macro_files == actual_macro_files


def test_base_template_imports_shared_macros() -> None:
    """Проверяет, что base layout импортирует shared macros."""
    base_template = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")

    for macro_name in (
        "assets",
        "badges",
        "buttons",
        "cards",
        "empty_states",
        "frames",
        "progress",
        "status",
        "tables",
    ):
        assert f"as {macro_name}" in base_template


def test_dashboard_uses_macro_rendered_components() -> None:
    """Проверяет, что macro-rendered badge/button/card видны в HTML."""
    with TestClient(app) as client:
        response = client.get("/")

    assert 'class="bn-badge bn-badge--info"' in response.text
    assert 'class="bn-card bn-empty-state"' in response.text
    assert 'class="bn-button bn-button--primary"' in response.text
    assert 'class="bn-button bn-button--ghost"' in response.text

```


## FILE: tests/test_web_access_context.py

```python
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

```


## FILE: tests/test_web_admin_clan_routes.py

```python
"""Route-level тесты admin clan routes."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core.settings import Settings
from app.db.models import Clan
from app.domain import ClanType
from app.integrations.clash import ClashClan, ClashNotFoundError
from app.services import ClanNotFoundError
from app.web.admin_clans import get_admin_clan_service
from app.web.security import build_admin_cookie_value

VALID_SETTINGS = {
    "APP_ENV": "local",
    "APP_BASE_URL": "http://localhost:8000",
    "DATABASE_URL": "postgresql+asyncpg://bn:bn@postgres:5432/bestiary",
    "CLASH_API_BASE_URL": "https://api.clashofclans.com/v1",
    "CLASH_API_TOKEN": "test-clash-token-123",
    "CLASH_API_TIMEOUT_SECONDS": 7,
    "TELEGRAM_BOT_TOKEN": "test-telegram-token-123",
    "TELEGRAM_ADMIN_ID": 123456789,
    "WEB_SESSION_SECRET": "test-session-secret-value-1234567890",
    "WEB_ADMIN_COOKIE_NAME": "bn_admin_session",
    "SYNC_DEFAULT_INTERVAL_SECONDS": 900,
    "ROLE_SNAPSHOT_MAX_AGE_MINUTES": 30,
}


class FakeAdminClanService:
    """Fake service для route-level тестов admin clan routes."""

    def __init__(
        self,
        *,
        clans: list[Clan] | None = None,
        verified_clan: ClashClan | None = None,
        error: Exception | None = None,
    ) -> None:
        """Инициализирует fake service.

        Args:
            clans: Локальные кланы для list route.
            verified_clan: DTO проверенного клана.
            error: Ошибка, которую fake должен выбросить.
        """
        self.clans = clans or []
        self.verified_clan = verified_clan or _make_verified_clan()
        self.error = error
        self.calls: list[tuple[object, ...]] = []

    async def list_clans(self) -> tuple[Clan, ...]:
        """Возвращает список fake-кланов."""
        self.calls.append(("list_clans",))
        return tuple(self.clans)

    async def check_clan(self, *, clan_tag: str) -> ClashClan:
        """Проверяет клан через fake Clash API."""
        self.calls.append(("check_clan", clan_tag))
        self._raise_if_needed()

        return self.verified_clan

    async def add_clan(self, *, clan_tag: str, clan_type: ClanType | str) -> Clan:
        """Добавляет fake-клан."""
        normalized_type = clan_type.value if isinstance(clan_type, ClanType) else clan_type
        self.calls.append(("add_clan", clan_tag, normalized_type))
        self._raise_if_needed()

        return _make_clan(clan_type=normalized_type)

    async def refresh_clan(self, *, clan_tag: str) -> Clan:
        """Обновляет fake-клан."""
        self.calls.append(("refresh_clan", clan_tag))
        self._raise_if_needed()

        return _make_clan(
            name="Fresh name",
            level=20,
            badge_url="https://example.test/fresh-badge.png",
            sync_status="ok",
        )

    async def update_clan_type(self, *, clan_tag: str, clan_type: ClanType | str) -> Clan:
        """Меняет тип fake-клана."""
        normalized_type = clan_type.value if isinstance(clan_type, ClanType) else clan_type
        self.calls.append(("update_clan_type", clan_tag, normalized_type))
        self._raise_if_needed()

        return _make_clan(clan_type=normalized_type)

    async def deactivate_clan(self, *, clan_tag: str) -> Clan:
        """Деактивирует fake-клан без физического удаления."""
        self.calls.append(("deactivate_clan", clan_tag))
        self._raise_if_needed()

        return _make_clan(is_active=False)

    def _raise_if_needed(self) -> None:
        """Выбрасывает настроенную ошибку, если она есть."""
        if self.error is not None:
            raise self.error


def make_settings() -> Settings:
    """Создаёт settings для admin route-тестов.

    Returns:
        Провалидированный settings.
    """
    return Settings(**VALID_SETTINGS)


def test_admin_clan_routes_reject_anonymous_user() -> None:
    """Проверяет, что anonymous user не может читать admin clan routes."""
    service = FakeAdminClanService()

    with _override_admin_clan_service(service), TestClient(app) as client:
        response = client.get("/admin/clans")

    assert response.status_code == 403
    assert service.calls == []


def test_admin_clan_routes_list_clans_for_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет JSON route списка кланов для админа."""
    clan = _make_clan()
    service = FakeAdminClanService(clans=[clan])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get("/admin/clans")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "clans": [
            {
                "id": 1,
                "tag": "#2ABC",
                "name": "Bestiary",
                "type": "main",
                "level": 17,
                "badge_url": "https://example.test/badge.png",
                "is_active": True,
                "last_sync_at": "2026-05-22T12:00:00Z",
                "sync_status": "ok",
            }
        ],
    }
    assert service.calls == [("list_clans",)]


def test_admin_check_clan_route_returns_verified_clan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет route проверки клана через Clash API."""
    verified_clan = _make_verified_clan()
    service = FakeAdminClanService(verified_clan=verified_clan)

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post("/admin/clans/check", json={"clan_tag": "2abc"})

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "clan": {
            "tag": "#2ABC",
            "name": "Bestiary",
            "level": 17,
            "badge_url": "https://example.test/badge.png",
            "members_count": 44,
        },
    }
    assert service.calls == [("check_clan", "2abc")]


def test_admin_add_clan_route_calls_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет route добавления клана."""
    service = FakeAdminClanService()

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post(
            "/admin/clans",
            json={"clan_tag": "2abc", "clan_type": "academy"},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["clan"]["tag"] == "#2ABC"
    assert response.json()["clan"]["type"] == "academy"
    assert service.calls == [("add_clan", "2abc", "academy")]


def test_admin_update_clan_type_route_calls_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет route смены типа клана."""
    service = FakeAdminClanService()

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post(
            "/admin/clans/type",
            json={"clan_tag": "#2ABC", "clan_type": "freezer"},
        )

    assert response.status_code == 200
    assert response.json()["clan"]["type"] == "freezer"
    assert service.calls == [("update_clan_type", "#2ABC", "freezer")]


def test_admin_refresh_clan_route_calls_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет route ручного обновления клана."""
    service = FakeAdminClanService()

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post("/admin/clans/refresh", json={"clan_tag": "#2ABC"})

    assert response.status_code == 200
    assert response.json()["clan"]["name"] == "Fresh name"
    assert response.json()["clan"]["level"] == 20
    assert service.calls == [("refresh_clan", "#2ABC")]


def test_admin_deactivate_clan_route_is_soft_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет, что delete/deactivate route делает soft deactivate."""
    service = FakeAdminClanService()

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post("/admin/clans/deactivate", json={"clan_tag": "#2ABC"})

    assert response.status_code == 200
    assert response.json()["clan"]["is_active"] is False
    assert service.calls == [("deactivate_clan", "#2ABC")]


def test_admin_clan_route_maps_missing_local_clan_to_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет понятную 404-ошибку для отсутствующего локального клана."""
    service = FakeAdminClanService(error=ClanNotFoundError("Клан #2ABC не найден."))

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post("/admin/clans/refresh", json={"clan_tag": "#2ABC"})

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "clan_not_found",
        "message": "Клан #2ABC не найден.",
    }


def test_admin_clan_route_maps_clash_not_found_to_clear_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет понятную ошибку, если Clash API не нашёл клан."""
    service = FakeAdminClanService(
        error=ClashNotFoundError(
            "Clash API returned HTTP 404 for GET clans/%232ABC.",
            endpoint="clans/%232ABC",
            method="GET",
            status_code=404,
            response_snippet=None,
        )
    )

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post("/admin/clans/check", json={"clan_tag": "#2ABC"})

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "clash_clan_not_found",
        "message": "Клан не найден в Clash of Clans API.",
        "clash_status_code": 404,
    }


@contextmanager
def _admin_client(
    *,
    monkeypatch: pytest.MonkeyPatch,
    service: FakeAdminClanService,
) -> Iterator[TestClient]:
    """Создаёт TestClient с валидной admin-cookie.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        service: Fake service admin clan routes.

    Yields:
        TestClient с admin-cookie.
    """
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with _override_admin_clan_service(service), TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        yield client


@contextmanager
def _override_admin_clan_service(service: FakeAdminClanService) -> Iterator[None]:
    """Подменяет dependency сервиса кланов.

    Args:
        service: Fake service admin clan routes.

    Yields:
        Управление тесту.
    """
    previous_override = app.dependency_overrides.get(get_admin_clan_service)
    app.dependency_overrides[get_admin_clan_service] = lambda: service

    try:
        yield
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_admin_clan_service, None)
        else:
            app.dependency_overrides[get_admin_clan_service] = previous_override


def _make_clan(
    *,
    clan_type: str = ClanType.MAIN.value,
    name: str = "Bestiary",
    level: int | None = 17,
    badge_url: str | None = "https://example.test/badge.png",
    is_active: bool = True,
    sync_status: str | None = "ok",
) -> Clan:
    """Создаёт модель клана для route-тестов.

    Args:
        clan_type: Тип клана.
        name: Название клана.
        level: Уровень клана.
        badge_url: URL badge.
        is_active: Признак активного мониторинга.
        sync_status: Статус синхронизации.

    Returns:
        Модель клана.
    """
    return Clan(
        id=1,
        tag="#2ABC",
        name=name,
        type=clan_type,
        level=level,
        badge_url=badge_url,
        is_active=is_active,
        last_sync_at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        sync_status=sync_status,
    )


def _make_verified_clan() -> ClashClan:
    """Создаёт DTO проверенного клана.

    Returns:
        DTO ClashClan.
    """
    return ClashClan(
        tag="#2ABC",
        name="Bestiary",
        level=17,
        badge_url="https://example.test/badge.png",
        members_count=44,
    )

```


## FILE: tests/test_web_admin_clan_settings_page.py

```python
"""Тесты SSR-страницы настроек кланов."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core.settings import Settings
from app.db.models import Clan
from app.domain import ClanType
from app.integrations.clash import ClashClan, ClashNotFoundError
from app.web.admin_clans import get_admin_clan_service
from app.web.security import build_admin_cookie_value

VALID_SETTINGS = {
    "APP_ENV": "local",
    "APP_BASE_URL": "http://localhost:8000",
    "DATABASE_URL": "postgresql+asyncpg://bn:bn@postgres:5432/bestiary",
    "CLASH_API_BASE_URL": "https://api.clashofclans.com/v1",
    "CLASH_API_TOKEN": "test-clash-token-123",
    "CLASH_API_TIMEOUT_SECONDS": 7,
    "TELEGRAM_BOT_TOKEN": "test-telegram-token-123",
    "TELEGRAM_ADMIN_ID": 123456789,
    "WEB_SESSION_SECRET": "test-session-secret-value-1234567890",
    "WEB_ADMIN_COOKIE_NAME": "bn_admin_session",
    "SYNC_DEFAULT_INTERVAL_SECONDS": 900,
    "ROLE_SNAPSHOT_MAX_AGE_MINUTES": 30,
}


class FakeClanSettingsService:
    """Fake service для SSR-страницы настроек кланов."""

    def __init__(
        self,
        *,
        clans: list[Clan] | None = None,
        verified_clan: ClashClan | None = None,
        error: Exception | None = None,
    ) -> None:
        """Инициализирует fake service.

        Args:
            clans: Кланы для страницы.
            verified_clan: DTO проверенного клана.
            error: Ошибка для action methods.
        """
        self.clans = clans or []
        self.verified_clan = verified_clan or _make_verified_clan()
        self.error = error
        self.calls: list[tuple[object, ...]] = []

    async def list_clans(self) -> tuple[Clan, ...]:
        """Возвращает список fake-кланов."""
        self.calls.append(("list_clans",))
        return tuple(self.clans)

    async def check_clan(self, *, clan_tag: str) -> ClashClan:
        """Проверяет fake-клан."""
        self.calls.append(("check_clan", clan_tag))
        self._raise_if_needed()
        return self.verified_clan

    async def add_clan(self, *, clan_tag: str, clan_type: ClanType | str) -> Clan:
        """Добавляет fake-клан."""
        normalized_type = clan_type.value if isinstance(clan_type, ClanType) else clan_type
        self.calls.append(("add_clan", clan_tag, normalized_type))
        self._raise_if_needed()
        return _make_clan(clan_type=normalized_type)

    async def refresh_clan(self, *, clan_tag: str) -> Clan:
        """Обновляет fake-клан."""
        self.calls.append(("refresh_clan", clan_tag))
        self._raise_if_needed()
        return _make_clan(name="Fresh name")

    async def update_clan_type(self, *, clan_tag: str, clan_type: ClanType | str) -> Clan:
        """Меняет тип fake-клана."""
        normalized_type = clan_type.value if isinstance(clan_type, ClanType) else clan_type
        self.calls.append(("update_clan_type", clan_tag, normalized_type))
        self._raise_if_needed()
        return _make_clan(clan_type=normalized_type)

    async def deactivate_clan(self, *, clan_tag: str) -> Clan:
        """Деактивирует fake-клан."""
        self.calls.append(("deactivate_clan", clan_tag))
        self._raise_if_needed()
        return _make_clan(is_active=False)

    def _raise_if_needed(self) -> None:
        """Выбрасывает заданную ошибку, если она есть."""
        if self.error is not None:
            raise self.error


def make_settings() -> Settings:
    """Создаёт settings для тестов страницы.

    Returns:
        Провалидированный settings.
    """
    return Settings(**VALID_SETTINGS)


def test_clan_settings_page_rejects_anonymous_user() -> None:
    """Проверяет admin-only доступ к странице настроек кланов."""
    service = FakeClanSettingsService(clans=[_make_clan()])

    with _override_clan_service(service), TestClient(app) as client:
        response = client.get("/admin/settings/clans")

    assert response.status_code == 403
    assert service.calls == []


def test_clan_settings_page_renders_admin_cards_and_forms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет SSR-страницу настроек кланов."""
    service = FakeClanSettingsService(clans=[_make_clan()])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get("/admin/settings/clans")

    assert response.status_code == 200
    assert "Настройки кланов" in response.text
    assert 'class="bn-card' in response.text
    assert 'class="bn-button' in response.text
    assert 'class="bn-badge' in response.text
    assert "Основа" in response.text
    assert "#2ABC" in response.text
    assert "/admin/settings/clans/add" in response.text
    assert "/admin/settings/clans/check" in response.text
    assert "/admin/settings/clans/type" in response.text
    assert "/admin/settings/clans/refresh" in response.text
    assert "/admin/settings/clans/deactivate" in response.text
    assert 'style="' not in response.text
    assert service.calls == [("list_clans",)]


def test_admin_sidebar_shows_clan_settings_link_for_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет admin-only ссылку на настройки кланов в sidebar."""
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        response = client.get("/")

    assert response.status_code == 200
    assert "/admin/settings/clans" in response.text
    assert "Настройки кланов" in response.text


def test_clan_settings_check_action_renders_verified_clan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет проверку тега через Clash API с отображением результата."""
    service = FakeClanSettingsService(clans=[_make_clan()])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post(
            "/admin/settings/clans/check",
            data={"clan_tag": "2abc"},
        )

    assert response.status_code == 200
    assert "Клан найден" in response.text
    assert "Bestiary" in response.text
    assert "#2ABC" in response.text
    assert service.calls == [("check_clan", "2abc"), ("list_clans",)]


def test_clan_settings_add_action_redirects_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет успешное добавление клана через форму."""
    service = FakeClanSettingsService()

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post(
            "/admin/settings/clans/add",
            data={"clan_tag": "2abc", "clan_type": "academy"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings/clans?notice=clan_added"
    assert service.calls == [("add_clan", "2abc", "academy")]


def test_clan_settings_update_type_action_redirects_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет смену типа клана через форму."""
    service = FakeClanSettingsService()

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post(
            "/admin/settings/clans/type",
            data={"clan_tag": "#2ABC", "clan_type": "freezer"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings/clans?notice=clan_type_updated"
    assert service.calls == [("update_clan_type", "#2ABC", "freezer")]


def test_clan_settings_refresh_action_redirects_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет ручное обновление клана через форму."""
    service = FakeClanSettingsService()

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post(
            "/admin/settings/clans/refresh",
            data={"clan_tag": "#2ABC"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings/clans?notice=clan_refreshed"
    assert service.calls == [("refresh_clan", "#2ABC")]


def test_clan_settings_deactivate_action_is_soft_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет soft deactivate через форму."""
    service = FakeClanSettingsService()

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post(
            "/admin/settings/clans/deactivate",
            data={"clan_tag": "#2ABC"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings/clans?notice=clan_deactivated"
    assert service.calls == [("deactivate_clan", "#2ABC")]


def test_clan_settings_form_error_is_rendered_near_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет отображение ошибки рядом с формой."""
    service = FakeClanSettingsService(
        clans=[_make_clan()],
        error=ClashNotFoundError(
            "Clash API returned HTTP 404 for GET clans/%23BAD.",
            endpoint="clans/%23BAD",
            method="GET",
            status_code=404,
            response_snippet=None,
        ),
    )

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post(
            "/admin/settings/clans/add",
            data={"clan_tag": "#BAD", "clan_type": "main"},
        )

    assert response.status_code == 400
    assert 'data-action-target="add"' in response.text
    assert "Клан не найден в Clash of Clans API." in response.text
    assert service.calls == [("add_clan", "#BAD", "main"), ("list_clans",)]


@contextmanager
def _admin_client(
    *,
    monkeypatch: pytest.MonkeyPatch,
    service: FakeClanSettingsService,
) -> Iterator[TestClient]:
    """Создаёт TestClient с admin-cookie и fake service.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        service: Fake clan settings service.

    Yields:
        TestClient с admin-cookie.
    """
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with _override_clan_service(service), TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        yield client


@contextmanager
def _override_clan_service(service: FakeClanSettingsService) -> Iterator[None]:
    """Подменяет dependency сервиса кланов.

    Args:
        service: Fake clan settings service.

    Yields:
        Управление тесту.
    """
    previous_override = app.dependency_overrides.get(get_admin_clan_service)
    app.dependency_overrides[get_admin_clan_service] = lambda: service

    try:
        yield
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_admin_clan_service, None)
        else:
            app.dependency_overrides[get_admin_clan_service] = previous_override


def _make_clan(
    *,
    clan_type: str = ClanType.MAIN.value,
    name: str = "Bestiary",
    level: int | None = 17,
    badge_url: str | None = "https://example.test/badge.png",
    is_active: bool = True,
    sync_status: str | None = "ok",
) -> Clan:
    """Создаёт модель клана для тестов страницы.

    Args:
        clan_type: Тип клана.
        name: Название клана.
        level: Уровень клана.
        badge_url: URL badge.
        is_active: Признак активного мониторинга.
        sync_status: Статус синхронизации.

    Returns:
        Модель Clan.
    """
    return Clan(
        id=1,
        tag="#2ABC",
        name=name,
        type=clan_type,
        level=level,
        badge_url=badge_url,
        is_active=is_active,
        last_sync_at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        sync_status=sync_status,
    )


def _make_verified_clan() -> ClashClan:
    """Создаёт DTO проверенного клана.

    Returns:
        DTO ClashClan.
    """
    return ClashClan(
        tag="#2ABC",
        name="Bestiary",
        level=17,
        badge_url="https://example.test/badge.png",
        members_count=44,
    )

```


## FILE: tests/test_web_admin_telegram_settings_page.py

```python
"""Тесты SSR-страницы Telegram settings."""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core.settings import Settings
from app.db.models import Clan, NotificationRoute, TelegramChat
from app.domain import ClanType, NotificationType
from app.web.admin_telegram_settings import (
    TelegramSettingsDependencies,
    get_telegram_settings_dependencies,
)
from app.web.security import build_admin_cookie_value

VALID_SETTINGS = {
    "APP_ENV": "local",
    "APP_BASE_URL": "http://localhost:8000",
    "DATABASE_URL": "postgresql+asyncpg://bn:bn@postgres:5432/bestiary",
    "CLASH_API_BASE_URL": "https://api.clashofclans.com/v1",
    "CLASH_API_TOKEN": "test-clash-token-123",
    "CLASH_API_TIMEOUT_SECONDS": 7,
    "TELEGRAM_BOT_TOKEN": "test-telegram-token-123",
    "TELEGRAM_ADMIN_ID": 123456789,
    "WEB_SESSION_SECRET": "test-session-secret-value-1234567890",
    "WEB_ADMIN_COOKIE_NAME": "bn_admin_session",
    "SYNC_DEFAULT_INTERVAL_SECONDS": 900,
    "ROLE_SNAPSHOT_MAX_AGE_MINUTES": 30,
}


class FakeTelegramSettingsClanService:
    """Fake clan service страницы Telegram settings."""

    def __init__(self, clans: list[Clan] | None = None) -> None:
        """Инициализирует fake clan service.

        Args:
            clans: Кланы страницы.
        """
        self.clans = clans or []
        self.calls: list[tuple[object, ...]] = []

    async def list_clans(self) -> tuple[Clan, ...]:
        """Возвращает fake-кланы."""
        self.calls.append(("list_clans",))
        return tuple(self.clans)


class FakeTelegramSettingsRouteService:
    """Fake notification route service страницы Telegram settings."""

    def __init__(self, routes: list[NotificationRoute] | None = None) -> None:
        """Инициализирует fake route service.

        Args:
            routes: Routes страницы.
        """
        self.routes = routes or []
        self.calls: list[tuple[object, ...]] = []

    async def list_all_routes(
        self,
        *,
        include_disabled: bool = True,
    ) -> tuple[NotificationRoute, ...]:
        """Возвращает fake routes."""
        self.calls.append(("list_all_routes", include_disabled))
        return tuple(route for route in self.routes if include_disabled or route.enabled)


def make_settings() -> Settings:
    """Создаёт settings для тестов страницы.

    Returns:
        Провалидированный settings.
    """
    return Settings(**VALID_SETTINGS)


def test_telegram_settings_page_rejects_anonymous_user() -> None:
    """Проверяет admin-only доступ к странице Telegram settings."""
    dependencies = _make_dependencies(clans=[_make_clan()])

    with _override_dependencies(dependencies), TestClient(app) as client:
        response = client.get("/admin/settings/telegram")

    assert response.status_code == 403
    assert dependencies.clan_service.calls == []
    assert dependencies.route_service.calls == []


def test_telegram_settings_page_renders_clans_types_and_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет рендер страницы Telegram settings."""
    clan = _make_clan()
    enabled_route = _make_route(route_id=1, clan=clan, enabled=True, message_thread_id=321)
    disabled_route = _make_route(
        route_id=2,
        clan=clan,
        enabled=False,
        chat_id=-100456,
        chat_title="Archive topic",
        message_thread_id=None,
    )
    dependencies = _make_dependencies(clans=[clan], routes=[enabled_route, disabled_route])

    with _admin_client(monkeypatch=monkeypatch, dependencies=dependencies) as client:
        response = client.get("/admin/settings/telegram")

    assert response.status_code == 200
    assert "Настройки Telegram" in response.text
    assert "Bestiary" in response.text
    assert "#2ABC" in response.text
    assert "Война началась" in response.text
    assert "/register #2ABC war_started" in response.text
    assert "War topic" in response.text
    assert "Archive topic" in response.text
    assert "topic:" in response.text
    assert "321" in response.text
    assert "без топика" in response.text
    assert 'data-copy-command="/register #2ABC war_started"' in response.text
    assert 'data-route-test-url="/admin/notification-routes/1/test"' in response.text
    assert 'data-route-disable-url="/admin/notification-routes/1/disable"' in response.text
    assert 'data-route-id="2"' in response.text
    assert "bn-route-row--disabled" in response.text
    assert 'class="bn-card' in response.text
    assert 'class="bn-button' in response.text
    assert 'class="bn-badge' in response.text
    assert 'style="' not in response.text
    assert dependencies.clan_service.calls == [("list_clans",)]
    assert dependencies.route_service.calls == [("list_all_routes", True)]


def test_telegram_settings_page_shows_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет пустое состояние без кланов."""
    dependencies = _make_dependencies(clans=[])

    with _admin_client(monkeypatch=monkeypatch, dependencies=dependencies) as client:
        response = client.get("/admin/settings/telegram")

    assert response.status_code == 200
    assert "Кланы ещё не добавлены" in response.text
    assert "/admin/settings/clans" in response.text


def test_admin_sidebar_shows_telegram_settings_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет admin-only ссылку на Telegram settings в sidebar."""
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        response = client.get("/")

    assert response.status_code == 200
    assert "/admin/settings/telegram" in response.text
    assert "Настройки Telegram" in response.text


def test_telegram_settings_static_assets_are_served() -> None:
    """Проверяет CSS и JS страницы Telegram settings."""
    with TestClient(app) as client:
        css_response = client.get("/static/css/pages/admin_telegram.css")
        js_response = client.get("/static/js/pages/admin_telegram_settings.js")

    assert css_response.status_code == 200
    assert ".bn-admin-telegram" in css_response.text
    assert ".bn-notification-type" in css_response.text
    assert js_response.status_code == 200
    assert "navigator.clipboard.writeText" in js_response.text
    assert 'method: "POST"' in js_response.text


@contextmanager
def _admin_client(
    *,
    monkeypatch: pytest.MonkeyPatch,
    dependencies: TelegramSettingsDependencies,
) -> Iterator[TestClient]:
    """Создаёт TestClient с admin-cookie.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        dependencies: Fake dependencies страницы.

    Yields:
        TestClient с admin-cookie.
    """
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with _override_dependencies(dependencies), TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        yield client


@contextmanager
def _override_dependencies(dependencies: TelegramSettingsDependencies) -> Iterator[None]:
    """Подменяет зависимости страницы Telegram settings.

    Args:
        dependencies: Fake dependencies.

    Yields:
        Управление тесту.
    """
    previous_override = app.dependency_overrides.get(get_telegram_settings_dependencies)
    app.dependency_overrides[get_telegram_settings_dependencies] = lambda: dependencies

    try:
        yield
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_telegram_settings_dependencies, None)
        else:
            app.dependency_overrides[get_telegram_settings_dependencies] = previous_override


def _make_dependencies(
    *,
    clans: list[Clan] | None = None,
    routes: list[NotificationRoute] | None = None,
) -> TelegramSettingsDependencies:
    """Создаёт fake dependencies страницы.

    Args:
        clans: Кланы.
        routes: Routes.

    Returns:
        Dependencies страницы.
    """
    return TelegramSettingsDependencies(
        clan_service=FakeTelegramSettingsClanService(clans),
        route_service=FakeTelegramSettingsRouteService(routes),
    )


def _make_clan() -> Clan:
    """Создаёт клан для тестов.

    Returns:
        Модель Clan.
    """
    return Clan(
        id=7,
        tag="#2ABC",
        name="Bestiary",
        type=ClanType.MAIN.value,
        badge_url="https://example.test/badge.png",
        is_active=True,
    )


def _make_route(
    *,
    route_id: int,
    clan: Clan,
    enabled: bool,
    chat_id: int = -100123,
    chat_title: str = "War topic",
    message_thread_id: int | None,
) -> NotificationRoute:
    """Создаёт route для тестов.

    Args:
        route_id: DB ID route.
        clan: Клан route.
        enabled: Флаг активности.
        chat_id: Telegram chat id.
        chat_title: Название чата.
        message_thread_id: Topic/thread id.

    Returns:
        Модель NotificationRoute.
    """
    chat = TelegramChat(
        id=route_id + 100,
        chat_id=chat_id,
        title=chat_title,
        type="supergroup",
        is_forum=message_thread_id is not None,
    )
    return NotificationRoute(
        id=route_id,
        clan_id=clan.id,
        clan=clan,
        notification_type=NotificationType.WAR_STARTED.value,
        chat_id=chat_id,
        chat=chat,
        message_thread_id=message_thread_id,
        enabled=enabled,
    )

```


## FILE: tests/test_web_admin_api_error_routes.py

```python
"""Route-level тесты admin API errors routes."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core.settings import Settings
from app.db.models import ApiError
from app.services.api_error_policies import (
    API_ERROR_STATUS_STALE,
    API_ERROR_STATUS_UNRESOLVED,
)
from app.services.api_errors import API_ERROR_STATUS_RESOLVED, ApiErrorNotFoundError
from app.web.admin_api_errors import get_admin_api_error_service
from app.web.security import build_admin_cookie_value

VALID_SETTINGS = {
    "APP_ENV": "local",
    "APP_BASE_URL": "http://localhost:8000",
    "DATABASE_URL": "postgresql+asyncpg://bn:bn@postgres:5432/bestiary",
    "CLASH_API_BASE_URL": "https://api.clashofclans.com/v1",
    "CLASH_API_TOKEN": "test-clash-token-123",
    "CLASH_API_TIMEOUT_SECONDS": 7,
    "TELEGRAM_BOT_TOKEN": "test-telegram-token-123",
    "TELEGRAM_ADMIN_ID": 123456789,
    "WEB_SESSION_SECRET": "test-session-secret-value-1234567890",
    "WEB_ADMIN_COOKIE_NAME": "bn_admin_session",
    "SYNC_DEFAULT_INTERVAL_SECONDS": 900,
    "ROLE_SNAPSHOT_MAX_AGE_MINUTES": 30,
}


class FakeAdminApiErrorService:
    """Fake service API errors для route-level тестов."""

    def __init__(self, errors: list[ApiError] | None = None) -> None:
        """Инициализирует fake service.

        Args:
            errors: API errors.
        """
        self.errors = errors or []
        self.calls: list[tuple[object, ...]] = []

    async def list_errors(
        self,
        *,
        status_filter: str | None = None,
        limit: int = 50,
    ) -> tuple[ApiError, ...]:
        """Возвращает fake API errors."""
        self.calls.append(("list_errors", status_filter, limit))
        errors = [
            api_error
            for api_error in self.errors
            if status_filter is None or api_error.status == status_filter
        ]

        return tuple(errors[:limit])

    async def resolve_error(
        self,
        *,
        api_error_id: int,
        resolved_at: datetime,
    ) -> ApiError:
        """Помечает fake API error как resolved."""
        self.calls.append(("resolve_error", api_error_id))
        for api_error in self.errors:
            if api_error.id == api_error_id:
                api_error.status = API_ERROR_STATUS_RESOLVED
                api_error.resolved_at = resolved_at
                return api_error

        raise ApiErrorNotFoundError(f"API error {api_error_id} не найдена.")


def make_settings() -> Settings:
    """Создаёт settings для route-тестов.

    Returns:
        Провалидированный settings.
    """
    return Settings(**VALID_SETTINGS)


def test_admin_api_errors_reject_anonymous_user() -> None:
    """Проверяет, что API errors доступны только админу."""
    service = FakeAdminApiErrorService([_make_api_error()])

    with _override_admin_api_error_service(service), TestClient(app) as client:
        response = client.get("/admin/api-errors")

    assert response.status_code == 403
    assert service.calls == []


def test_admin_api_errors_summary_mode_hides_debug_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет summary mode без debug-полей."""
    api_error = _make_api_error(
        response_snippet='{"authorization":"Bearer secret"}',
        exception_class="ClashForbiddenError",
    )
    service = FakeAdminApiErrorService([api_error])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get("/admin/api-errors")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "mode": "summary",
        "count": 1,
        "limit": 50,
        "errors": [
            {
                "id": 1,
                "endpoint": "clans/%23MAIN/currentwar",
                "method": "GET",
                "entity_type": "clan",
                "entity_tag": "#MAIN",
                "status_code": 403,
                "message": "Forbidden by Clash API",
                "worker_name": "sync_current_wars",
                "retry_count": 2,
                "status": "unresolved",
                "is_stale": False,
                "created_at": "2026-05-22T12:00:00Z",
            }
        ],
    }
    assert "authorization" not in response.text.lower()
    assert "response_snippet" not in response.text
    assert "exception_class" not in response.text


def test_admin_api_errors_debug_mode_sanitizes_sensitive_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет debug mode и редактирование секретов."""
    api_error = _make_api_error(
        status=API_ERROR_STATUS_STALE,
        response_snippet='{"token":"secret-token-value"}',
        exception_class="ClashNotFoundError",
    )
    service = FakeAdminApiErrorService([api_error])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get("/admin/api-errors", params={"mode": "debug"})

    assert response.status_code == 200
    payload = response.json()

    assert payload["mode"] == "debug"
    assert payload["errors"][0]["is_stale"] is True
    assert payload["errors"][0]["response_snippet"] == "[redacted]"
    assert payload["errors"][0]["exception_class"] == "ClashNotFoundError"
    assert "secret-token-value" not in response.text


def test_admin_api_errors_status_filter_and_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет status filter и limit."""
    stale_error = _make_api_error(api_error_id=1, status=API_ERROR_STATUS_STALE)
    unresolved_error = _make_api_error(api_error_id=2, status=API_ERROR_STATUS_UNRESOLVED)
    service = FakeAdminApiErrorService([stale_error, unresolved_error])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get(
            "/admin/api-errors",
            params={"status": API_ERROR_STATUS_STALE, "limit": "1"},
        )

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["status_filter"] == API_ERROR_STATUS_STALE
    assert response.json()["errors"][0]["status"] == API_ERROR_STATUS_STALE
    assert service.calls == [("list_errors", API_ERROR_STATUS_STALE, 1)]


def test_admin_api_errors_limit_has_max_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет FastAPI validation для limit max 200."""
    service = FakeAdminApiErrorService([_make_api_error()])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get("/admin/api-errors", params={"limit": "201"})

    assert response.status_code == 422
    assert service.calls == []


def test_admin_api_errors_resolve_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет ручное закрытие API error."""
    api_error = _make_api_error(status=API_ERROR_STATUS_UNRESOLVED)
    service = FakeAdminApiErrorService([api_error])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post("/admin/api-errors/1/resolve")

    assert response.status_code == 200
    assert response.json()["api_error"]["status"] == API_ERROR_STATUS_RESOLVED
    assert response.json()["api_error"]["resolved_at"] is not None
    assert api_error.status == API_ERROR_STATUS_RESOLVED
    assert api_error.resolved_at is not None
    assert service.calls == [("resolve_error", 1)]


def test_admin_api_errors_resolve_unknown_error_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет 404 для неизвестной API error."""
    service = FakeAdminApiErrorService([])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post("/admin/api-errors/404/resolve")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "api_error_not_found",
        "message": "API error 404 не найдена.",
    }


@contextmanager
def _admin_client(
    *,
    monkeypatch: pytest.MonkeyPatch,
    service: FakeAdminApiErrorService,
) -> Iterator[TestClient]:
    """Создаёт TestClient с admin-cookie и fake service.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        service: Fake service API errors.

    Yields:
        TestClient с admin-cookie.
    """
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with _override_admin_api_error_service(service), TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        yield client


@contextmanager
def _override_admin_api_error_service(service: FakeAdminApiErrorService) -> Iterator[None]:
    """Подменяет dependency admin API errors.

    Args:
        service: Fake service API errors.

    Yields:
        Управление тесту.
    """
    previous_override = app.dependency_overrides.get(get_admin_api_error_service)
    app.dependency_overrides[get_admin_api_error_service] = lambda: service

    try:
        yield
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_admin_api_error_service, None)
        else:
            app.dependency_overrides[get_admin_api_error_service] = previous_override


def _make_api_error(
    *,
    api_error_id: int = 1,
    status: str = API_ERROR_STATUS_UNRESOLVED,
    status_code: int | None = 403,
    response_snippet: str | None = '{"reason":"accessDenied"}',
    exception_class: str | None = "ClashForbiddenError",
) -> ApiError:
    """Создаёт ApiError для route-тестов.

    Args:
        api_error_id: DB ID ошибки.
        status: Статус ошибки.
        status_code: HTTP status code.
        response_snippet: Response snippet.
        exception_class: Имя exception class.

    Returns:
        Модель ApiError.
    """
    return ApiError(
        id=api_error_id,
        endpoint="clans/%23MAIN/currentwar",
        method="GET",
        entity_type="clan",
        entity_tag="#MAIN",
        status_code=status_code,
        message="Forbidden by Clash API",
        response_snippet=response_snippet,
        exception_class=exception_class,
        worker_name="sync_current_wars",
        retry_count=2,
        status=status,
        created_at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
    )

```


## FILE: tests/test_web_admin_api_error_settings_page.py

```python
"""Тесты SSR-страницы Dev API errors."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core.settings import Settings
from app.db.models import ApiError
from app.services.api_error_policies import (
    API_ERROR_STATUS_RETRY_NEXT_RUN,
    API_ERROR_STATUS_STALE,
    API_ERROR_STATUS_UNRESOLVED,
)
from app.services.api_errors import API_ERROR_STATUS_RESOLVED
from app.web.admin_api_error_settings import get_api_error_settings_service
from app.web.security import build_admin_cookie_value

VALID_SETTINGS = {
    "APP_ENV": "local",
    "APP_BASE_URL": "http://localhost:8000",
    "DATABASE_URL": "postgresql+asyncpg://bn:bn@postgres:5432/bestiary",
    "CLASH_API_BASE_URL": "https://api.clashofclans.com/v1",
    "CLASH_API_TOKEN": "test-clash-token-123",
    "CLASH_API_TIMEOUT_SECONDS": 7,
    "TELEGRAM_BOT_TOKEN": "test-telegram-token-123",
    "TELEGRAM_ADMIN_ID": 123456789,
    "WEB_SESSION_SECRET": "test-session-secret-value-1234567890",
    "WEB_ADMIN_COOKIE_NAME": "bn_admin_session",
    "SYNC_DEFAULT_INTERVAL_SECONDS": 900,
    "ROLE_SNAPSHOT_MAX_AGE_MINUTES": 30,
}


class FakeApiErrorSettingsService:
    """Fake service Dev API errors страницы."""

    def __init__(self, errors: list[ApiError] | None = None) -> None:
        """Инициализирует fake service.

        Args:
            errors: API errors для страницы.
        """
        self.errors = errors or []
        self.calls: list[tuple[object, ...]] = []

    async def list_errors(
        self,
        *,
        status_filter: str | None = None,
        limit: int = 50,
    ) -> tuple[ApiError, ...]:
        """Возвращает fake API errors."""
        self.calls.append(("list_errors", status_filter, limit))
        errors = [
            api_error
            for api_error in self.errors
            if status_filter is None or api_error.status == status_filter
        ]

        return tuple(errors[:limit])


def make_settings() -> Settings:
    """Создаёт settings для route-тестов.

    Returns:
        Провалидированный settings.
    """
    return Settings(**VALID_SETTINGS)


def test_api_error_settings_page_rejects_anonymous_user() -> None:
    """Проверяет admin-only доступ к Dev API errors page."""
    service = FakeApiErrorSettingsService([_make_api_error()])

    with _override_service(service), TestClient(app) as client:
        response = client.get("/admin/settings/api-errors")

    assert response.status_code == 403
    assert service.calls == []


def test_api_error_settings_page_renders_summary_and_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет карточки, summary и раскрываемый debug."""
    service = FakeApiErrorSettingsService(
        [
            _make_api_error(
                status=API_ERROR_STATUS_UNRESOLVED,
                response_snippet='{"authorization":"Bearer secret"}',
            ),
            _make_api_error(
                api_error_id=2,
                status=API_ERROR_STATUS_RETRY_NEXT_RUN,
                status_code=500,
                response_snippet='{"reason":"serverError"}',
            ),
            _make_api_error(
                api_error_id=3,
                status=API_ERROR_STATUS_RESOLVED,
                status_code=404,
                response_snippet='{"reason":"notFound"}',
                resolved_at=datetime(2026, 5, 22, 13, 0, tzinfo=UTC),
            ),
        ]
    )

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get("/admin/settings/api-errors")

    assert response.status_code == 200
    assert "Dev API errors" in response.text
    assert "clans/%23MAIN/currentwar" in response.text
    assert "GET" in response.text
    assert "clan" in response.text
    assert "#MAIN" in response.text
    assert "HTTP 403" in response.text
    assert "Retry count" in response.text
    assert "sync_current_wars" in response.text
    assert "ClashForbiddenError" in response.text
    assert "Unresolved" in response.text
    assert "Retrying" in response.text
    assert "Resolved" in response.text
    assert "<details" in response.text
    assert "Response snippet" in response.text
    assert "[redacted]" in response.text
    assert "Bearer secret" not in response.text
    assert 'data-api-error-resolve-url="/admin/api-errors/1/resolve"' in response.text
    assert 'data-api-error-resolve-url="/admin/api-errors/3/resolve"' not in response.text
    assert 'class="bn-card' in response.text
    assert 'class="bn-badge' in response.text
    assert 'style="' not in response.text
    assert service.calls == [("list_errors", None, 50)]


def test_api_error_settings_page_applies_status_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет фильтр status и limit."""
    service = FakeApiErrorSettingsService(
        [
            _make_api_error(status=API_ERROR_STATUS_STALE),
            _make_api_error(api_error_id=2, status=API_ERROR_STATUS_UNRESOLVED),
        ]
    )

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get(
            "/admin/settings/api-errors",
            params={"status": API_ERROR_STATUS_STALE, "limit": "1"},
        )

    assert response.status_code == 200
    assert "Stale" in response.text
    assert "Показано записей: 1" in response.text
    assert service.calls == [("list_errors", API_ERROR_STATUS_STALE, 1)]


def test_api_error_settings_page_shows_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет empty state."""
    service = FakeApiErrorSettingsService([])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get("/admin/settings/api-errors")

    assert response.status_code == 200
    assert "API errors не найдены" in response.text


def test_admin_sidebar_shows_api_errors_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет admin-only ссылку на Dev API errors в sidebar."""
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        response = client.get("/")

    assert response.status_code == 200
    assert "/admin/settings/api-errors" in response.text
    assert "Dev API errors" in response.text


def test_api_error_settings_static_assets_are_served() -> None:
    """Проверяет CSS и JS страницы Dev API errors."""
    with TestClient(app) as client:
        css_response = client.get("/static/css/pages/admin_api_errors.css")
        js_response = client.get("/static/js/pages/admin_api_errors.js")

    assert css_response.status_code == 200
    assert ".bn-admin-api-errors" in css_response.text
    assert ".bn-api-error-card" in css_response.text
    assert js_response.status_code == 200
    assert "data-api-error-resolve-url" in js_response.text
    assert 'method: "POST"' in js_response.text


@contextmanager
def _admin_client(
    *,
    monkeypatch: pytest.MonkeyPatch,
    service: FakeApiErrorSettingsService,
) -> Iterator[TestClient]:
    """Создаёт TestClient с admin-cookie.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        service: Fake service страницы.

    Yields:
        TestClient с admin-cookie.
    """
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with _override_service(service), TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        yield client


@contextmanager
def _override_service(service: FakeApiErrorSettingsService) -> Iterator[None]:
    """Подменяет dependency сервиса страницы.

    Args:
        service: Fake service страницы.

    Yields:
        Управление тесту.
    """
    previous_override = app.dependency_overrides.get(get_api_error_settings_service)
    app.dependency_overrides[get_api_error_settings_service] = lambda: service

    try:
        yield
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_api_error_settings_service, None)
        else:
            app.dependency_overrides[get_api_error_settings_service] = previous_override


def _make_api_error(
    *,
    api_error_id: int = 1,
    status: str = API_ERROR_STATUS_UNRESOLVED,
    status_code: int | None = 403,
    response_snippet: str | None = '{"reason":"accessDenied"}',
    resolved_at: datetime | None = None,
) -> ApiError:
    """Создаёт ApiError для page-тестов.

    Args:
        api_error_id: DB ID ошибки.
        status: Статус ошибки.
        status_code: HTTP status code.
        response_snippet: Response snippet.
        resolved_at: Время закрытия ошибки.

    Returns:
        Модель ApiError.
    """
    return ApiError(
        id=api_error_id,
        endpoint="clans/%23MAIN/currentwar",
        method="GET",
        entity_type="clan",
        entity_tag="#MAIN",
        status_code=status_code,
        message="Forbidden by Clash API",
        response_snippet=response_snippet,
        exception_class="ClashForbiddenError",
        worker_name="sync_current_wars",
        retry_count=2,
        status=status,
        created_at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        resolved_at=resolved_at,
    )

```


## FILE: tests/test_user_clan_player_models.py

```python
"""Тесты моделей пользователей, кланов и игроков."""

from sqlalchemy import DateTime, Index, inspect
from sqlalchemy.dialects.postgresql import JSONB

from app.db.models import (
    Clan,
    ClanMemberSnapshot,
    PlayerAccount,
    PlayerProfileSnapshot,
    TelegramUser,
)


def test_telegram_user_model_contract() -> None:
    """Проверяет контракт модели TelegramUser."""
    table = TelegramUser.__table__
    columns = table.c

    assert table.name == "telegram_users"
    assert columns.telegram_id.unique is True
    assert columns.telegram_id.nullable is False
    assert columns.username.nullable is True
    assert columns.display_name.nullable is True
    assert columns.is_admin_cached.nullable is False

    first_seen_at_type = columns.first_seen_at.type
    last_seen_at_type = columns.last_seen_at.type

    assert isinstance(first_seen_at_type, DateTime)
    assert isinstance(last_seen_at_type, DateTime)
    assert first_seen_at_type.timezone is True
    assert last_seen_at_type.timezone is True


def test_player_account_model_contract_and_relationships() -> None:
    """Проверяет контракт модели PlayerAccount и связь с TelegramUser."""
    table = PlayerAccount.__table__
    columns = table.c

    assert table.name == "player_accounts"
    assert columns.player_tag.unique is True
    assert columns.player_tag.nullable is False
    assert columns.telegram_user_id.nullable is True
    assert columns.last_seen_clan_id.nullable is True
    assert columns.is_active.nullable is False

    mapper = inspect(PlayerAccount)

    assert mapper.relationships["telegram_user"].mapper.class_ is TelegramUser
    assert mapper.relationships["last_seen_clan"].mapper.class_ is Clan

    user_mapper = inspect(TelegramUser)

    assert user_mapper.relationships["accounts"].mapper.class_ is PlayerAccount


def test_clan_model_contract() -> None:
    """Проверяет контракт модели Clan."""
    table = Clan.__table__
    columns = table.c

    assert table.name == "clans"
    assert columns.tag.unique is True
    assert columns.tag.nullable is False
    assert columns.name.nullable is False
    assert columns.type.nullable is False
    assert columns.level.nullable is True
    assert columns.badge_url.nullable is True
    assert columns.is_active.nullable is False
    assert columns.last_sync_at.nullable is True
    assert columns.sync_status.nullable is True


def test_clan_member_snapshot_model_contract_and_index() -> None:
    """Проверяет контракт модели ClanMemberSnapshot и обязательный индекс."""
    table = ClanMemberSnapshot.__table__
    columns = table.c

    assert table.name == "clan_member_snapshots"
    assert columns.clan_id.nullable is False
    assert columns.player_tag.nullable is False
    assert columns.name.nullable is False
    assert columns.is_current.nullable is False

    snapshot_at_type = columns.snapshot_at.type

    assert isinstance(snapshot_at_type, DateTime)
    assert snapshot_at_type.timezone is True

    expected_index_columns = ("clan_id", "player_tag", "snapshot_at")
    index_columns = {
        tuple(index.columns.keys()) for index in table.indexes if isinstance(index, Index)
    }

    assert expected_index_columns in index_columns

    mapper = inspect(ClanMemberSnapshot)

    assert mapper.relationships["clan"].mapper.class_ is Clan


def test_player_profile_snapshot_model_contract_and_jsonb_fields() -> None:
    """Проверяет контракт модели PlayerProfileSnapshot и JSONB-поля."""
    table = PlayerProfileSnapshot.__table__
    columns = table.c

    assert table.name == "player_profile_snapshots"
    assert columns.player_tag.nullable is False
    assert columns.name.nullable is False
    assert columns.snapshot_at.nullable is False

    for field_name in (
        "heroes_json",
        "troops_json",
        "spells_json",
        "achievements_json",
    ):
        assert isinstance(columns[field_name].type, JSONB)
        assert columns[field_name].nullable is False

    expected_index_columns = ("player_tag", "snapshot_at")
    index_columns = {
        tuple(index.columns.keys()) for index in table.indexes if isinstance(index, Index)
    }

    assert expected_index_columns in index_columns

```


## FILE: tests/test_war_cwl_raid_models.py

```python
"""Тесты моделей войн, ЛВК и рейдов."""

from sqlalchemy import DateTime, Numeric, String, inspect

from app.db.models import (
    Clan,
    CwlSeason,
    CwlWar,
    RaidMember,
    RaidSeason,
    WarAttack,
    WarMember,
    WarSnapshot,
)


def get_fk_targets(column: object) -> set[str]:
    """Возвращает target_fullname всех FK колонки.

    Args:
        column: SQLAlchemy column.

    Returns:
        Набор строк вида `table.column`.
    """
    return {foreign_key.target_fullname for foreign_key in column.foreign_keys}


def assert_no_ondelete_cascade(column: object) -> None:
    """Проверяет, что FK не использует каскадное удаление.

    Args:
        column: SQLAlchemy column.
    """
    assert column.foreign_keys
    assert all(foreign_key.ondelete is None for foreign_key in column.foreign_keys)


def test_war_snapshot_model_contract() -> None:
    """Проверяет контракт модели WarSnapshot."""
    table = WarSnapshot.__table__
    columns = table.c

    assert table.name == "war_snapshots"
    assert get_fk_targets(columns.clan_id) == {"clans.id"}
    assert columns.war_event_key.unique is True
    assert columns.war_event_key.nullable is False
    assert columns.war_tag.nullable is True
    assert isinstance(columns.state.type, String)
    assert isinstance(columns.preparation_start_time.type, DateTime)
    assert isinstance(columns.start_time.type, DateTime)
    assert isinstance(columns.end_time.type, DateTime)
    assert columns.preparation_start_time.type.timezone is True
    assert columns.start_time.type.timezone is True
    assert columns.end_time.type.timezone is True
    assert isinstance(columns.our_destruction.type, Numeric)
    assert isinstance(columns.opponent_destruction.type, Numeric)

    assert_no_ondelete_cascade(columns.clan_id)

    mapper = inspect(WarSnapshot)

    assert mapper.relationships["clan"].mapper.class_ is Clan


def test_war_member_model_contract_and_fk_policy() -> None:
    """Проверяет контракт модели WarMember и FK без cascade-delete."""
    table = WarMember.__table__
    columns = table.c

    assert table.name == "war_members"
    assert get_fk_targets(columns.war_snapshot_id) == {"war_snapshots.id"}
    assert columns.side.nullable is False
    assert columns.player_tag.nullable is False
    assert columns.name.nullable is False
    assert columns.attacks_done.nullable is False
    assert columns.attacks_left.nullable is False

    assert_no_ondelete_cascade(columns.war_snapshot_id)

    mapper = inspect(WarMember)

    assert mapper.relationships["war_snapshot"].mapper.class_ is WarSnapshot


def test_war_attack_model_contract_and_fk_policy() -> None:
    """Проверяет контракт модели WarAttack и FK без cascade-delete."""
    table = WarAttack.__table__
    columns = table.c

    assert table.name == "war_attacks"
    assert get_fk_targets(columns.war_snapshot_id) == {"war_snapshots.id"}
    assert columns.attacker_tag.nullable is False
    assert columns.defender_tag.nullable is False
    assert columns.stars.nullable is False
    assert isinstance(columns.destruction_percentage.type, Numeric)
    assert columns.order.nullable is False

    assert_no_ondelete_cascade(columns.war_snapshot_id)

    mapper = inspect(WarAttack)

    assert mapper.relationships["war_snapshot"].mapper.class_ is WarSnapshot


def test_cwl_season_model_contract_and_fk_policy() -> None:
    """Проверяет контракт модели CwlSeason и FK без cascade-delete."""
    table = CwlSeason.__table__
    columns = table.c

    assert table.name == "cwl_seasons"
    assert get_fk_targets(columns.clan_id) == {"clans.id"}
    assert columns.season.nullable is False
    assert isinstance(columns.state.type, String)
    assert isinstance(columns.started_at.type, DateTime)
    assert columns.started_at.type.timezone is True
    assert columns.ended_at.nullable is True

    assert_no_ondelete_cascade(columns.clan_id)

    mapper = inspect(CwlSeason)

    assert mapper.relationships["clan"].mapper.class_ is Clan


def test_cwl_war_model_contract_and_unique_war_tag() -> None:
    """Проверяет контракт модели CwlWar и уникальность war_tag."""
    table = CwlWar.__table__
    columns = table.c

    assert table.name == "cwl_wars"
    assert get_fk_targets(columns.cwl_season_id) == {"cwl_seasons.id"}
    assert columns.round_number.nullable is False
    assert columns.war_tag.unique is True
    assert columns.war_tag.nullable is False
    assert isinstance(columns.state.type, String)
    assert columns.our_clan_tag.nullable is False
    assert columns.opponent_clan_tag.nullable is False
    assert isinstance(columns.our_destruction.type, Numeric)
    assert isinstance(columns.opponent_destruction.type, Numeric)

    assert_no_ondelete_cascade(columns.cwl_season_id)

    mapper = inspect(CwlWar)

    assert mapper.relationships["cwl_season"].mapper.class_ is CwlSeason


def test_raid_season_model_contract_and_fk_policy() -> None:
    """Проверяет контракт модели RaidSeason и FK без cascade-delete."""
    table = RaidSeason.__table__
    columns = table.c

    assert table.name == "raid_seasons"
    assert get_fk_targets(columns.clan_id) == {"clans.id"}
    assert isinstance(columns.state.type, String)
    assert isinstance(columns.start_time.type, DateTime)
    assert isinstance(columns.end_time.type, DateTime)
    assert columns.start_time.type.timezone is True
    assert columns.end_time.type.timezone is True
    assert columns.snapshot_at.type.timezone is True

    assert_no_ondelete_cascade(columns.clan_id)


def test_raid_member_model_contract_expected_attacks_and_status() -> None:
    """Проверяет контракт модели RaidMember, default 6 и статусное поле."""
    table = RaidMember.__table__
    columns = table.c

    assert table.name == "raid_members"
    assert get_fk_targets(columns.raid_season_id) == {"raid_seasons.id"}
    assert columns.player_tag.nullable is False
    assert columns.name.nullable is False
    assert columns.attacks.nullable is False
    assert columns.project_expected_attacks.nullable is False
    assert columns.project_expected_attacks.default is not None
    assert columns.project_expected_attacks.default.arg == 6
    assert columns.capital_resources_looted.nullable is False
    assert isinstance(columns.status.type, String)

    assert_no_ondelete_cascade(columns.raid_season_id)

    mapper = inspect(RaidMember)

    assert mapper.relationships["raid_season"].mapper.class_ is RaidSeason

```


## FILE: tests/test_warning_kick_models.py

```python
"""Тесты моделей warn и кандидатов на кик."""

from sqlalchemy import DateTime, String, inspect
from sqlalchemy.dialects.postgresql import JSONB

from app.db.models import Clan, CwlSeason, KickCandidate, TelegramUser, Warning


def get_fk_targets(column: object) -> set[str]:
    """Возвращает target_fullname всех FK колонки.

    Args:
        column: SQLAlchemy column.

    Returns:
        Набор строк вида `table.column`.
    """
    return {foreign_key.target_fullname for foreign_key in column.foreign_keys}


def assert_no_ondelete_cascade(column: object) -> None:
    """Проверяет, что FK не использует каскадное удаление.

    Args:
        column: SQLAlchemy column.
    """
    assert column.foreign_keys
    assert all(foreign_key.ondelete is None for foreign_key in column.foreign_keys)


def test_warning_model_contract_and_unique_nullable_event_key() -> None:
    """Проверяет основной контракт модели Warning."""
    table = Warning.__table__
    columns = table.c

    assert table.name == "warnings"
    assert get_fk_targets(columns.telegram_user_id) == {"telegram_users.id"}
    assert columns.telegram_user_id.nullable is False
    assert columns.source.nullable is False
    assert isinstance(columns.source.type, String)
    assert columns.status.nullable is False
    assert isinstance(columns.status.type, String)
    assert columns.reason_code.nullable is False
    assert columns.category.nullable is False
    assert columns.is_impactful.nullable is False
    assert columns.event_key.unique is True
    assert columns.event_key.nullable is True

    assert_no_ondelete_cascade(columns.telegram_user_id)


def test_warning_cancellation_expiration_and_context_fields_are_nullable() -> None:
    """Проверяет nullable-поля отмены, истечения и контекста warn."""
    columns = Warning.__table__.c

    assert columns.comment.nullable is True
    assert columns.author_telegram_user_id.nullable is True
    assert columns.clan_id.nullable is True
    assert columns.created_cwl_season_key.nullable is True
    assert columns.active_until_cwl_season_id.nullable is True
    assert columns.expired_at.nullable is True
    assert columns.cancelled_at.nullable is True
    assert columns.cancelled_by_telegram_user_id.nullable is True
    assert columns.cancelled_reason.nullable is True

    assert get_fk_targets(columns.author_telegram_user_id) == {"telegram_users.id"}
    assert get_fk_targets(columns.cancelled_by_telegram_user_id) == {"telegram_users.id"}
    assert get_fk_targets(columns.clan_id) == {"clans.id"}
    assert get_fk_targets(columns.active_until_cwl_season_id) == {"cwl_seasons.id"}

    assert_no_ondelete_cascade(columns.author_telegram_user_id)
    assert_no_ondelete_cascade(columns.cancelled_by_telegram_user_id)
    assert_no_ondelete_cascade(columns.clan_id)
    assert_no_ondelete_cascade(columns.active_until_cwl_season_id)

    assert isinstance(columns.expired_at.type, DateTime)
    assert isinstance(columns.cancelled_at.type, DateTime)
    assert columns.expired_at.type.timezone is True
    assert columns.cancelled_at.type.timezone is True


def test_warning_affected_accounts_are_jsonb_fields() -> None:
    """Проверяет JSONB-поля затронутых аккаунтов warn."""
    columns = Warning.__table__.c

    assert isinstance(columns.affected_player_tags_json.type, JSONB)
    assert isinstance(columns.affected_player_names_json.type, JSONB)
    assert columns.affected_player_tags_json.nullable is False
    assert columns.affected_player_names_json.nullable is False


def test_warning_relationships_target_expected_models() -> None:
    """Проверяет связи Warning с пользователем, кланом и сезоном ЛВК."""
    mapper = inspect(Warning)

    assert mapper.relationships["telegram_user"].mapper.class_ is TelegramUser
    assert mapper.relationships["author"].mapper.class_ is TelegramUser
    assert mapper.relationships["cancelled_by"].mapper.class_ is TelegramUser
    assert mapper.relationships["clan"].mapper.class_ is Clan
    assert mapper.relationships["active_until_cwl_season"].mapper.class_ is CwlSeason


def test_kick_candidate_model_contract_and_unique_nullable_event_key() -> None:
    """Проверяет основной контракт модели KickCandidate."""
    table = KickCandidate.__table__
    columns = table.c

    assert table.name == "kick_candidates"
    assert columns.telegram_user_id.nullable is True
    assert columns.player_tag.nullable is True
    assert columns.reason_code.nullable is False
    assert columns.status.nullable is False
    assert isinstance(columns.status.type, String)
    assert columns.event_key.unique is True
    assert columns.event_key.nullable is True
    assert columns.created_by.nullable is True

    assert get_fk_targets(columns.telegram_user_id) == {"telegram_users.id"}
    assert get_fk_targets(columns.created_by) == {"telegram_users.id"}

    assert_no_ondelete_cascade(columns.telegram_user_id)
    assert_no_ondelete_cascade(columns.created_by)


def test_kick_candidate_decision_fields_are_nullable() -> None:
    """Проверяет nullable decision-поля кандидата на кик."""
    columns = KickCandidate.__table__.c

    assert columns.deadline_at.nullable is True
    assert columns.decision_by_telegram_user_id.nullable is True
    assert columns.decision_at.nullable is True
    assert columns.decision_comment.nullable is True
    assert columns.executed_at.nullable is True

    assert get_fk_targets(columns.decision_by_telegram_user_id) == {"telegram_users.id"}
    assert_no_ondelete_cascade(columns.decision_by_telegram_user_id)

    assert isinstance(columns.deadline_at.type, DateTime)
    assert isinstance(columns.decision_at.type, DateTime)
    assert isinstance(columns.executed_at.type, DateTime)
    assert columns.deadline_at.type.timezone is True
    assert columns.decision_at.type.timezone is True
    assert columns.executed_at.type.timezone is True


def test_kick_candidate_relationships_target_expected_models() -> None:
    """Проверяет связи KickCandidate с TelegramUser."""
    mapper = inspect(KickCandidate)

    assert mapper.relationships["telegram_user"].mapper.class_ is TelegramUser
    assert mapper.relationships["created_by_user"].mapper.class_ is TelegramUser
    assert mapper.relationships["decision_by_user"].mapper.class_ is TelegramUser

```


## FILE: tests/test_telegram_notification_api_event_models.py

```python
"""Тесты моделей Telegram, уведомлений, API errors, событий и app settings."""

from sqlalchemy import CheckConstraint, DateTime, String, inspect
from sqlalchemy.dialects.postgresql import JSONB

from app.db.models import (
    ApiError,
    AppSetting,
    Clan,
    NotificationLog,
    NotificationRoute,
    PlayerEvent,
    TelegramChat,
    TelegramUser,
)


def get_fk_targets(column: object) -> set[str]:
    """Возвращает target_fullname всех FK колонки.

    Args:
        column: SQLAlchemy column.

    Returns:
        Набор строк вида `table.column`.
    """
    return {foreign_key.target_fullname for foreign_key in column.foreign_keys}


def assert_no_ondelete_cascade(column: object) -> None:
    """Проверяет, что FK не использует каскадное удаление.

    Args:
        column: SQLAlchemy column.
    """
    assert column.foreign_keys
    assert all(foreign_key.ondelete is None for foreign_key in column.foreign_keys)


def test_telegram_chat_model_contract() -> None:
    """Проверяет контракт модели TelegramChat."""
    table = TelegramChat.__table__
    columns = table.c

    assert table.name == "telegram_chats"
    assert columns.chat_id.unique is True
    assert columns.chat_id.nullable is False
    assert columns.title.nullable is False
    assert columns.type.nullable is False
    assert columns.is_forum.nullable is False
    assert columns.bot_is_admin.nullable is False
    assert columns.bot_can_restrict_members.nullable is False
    assert isinstance(columns.bot_permissions_json.type, JSONB)
    assert columns.bot_permissions_json.nullable is False
    assert columns.last_permissions_checked_at.nullable is True
    assert columns.last_permissions_checked_at.type.timezone is True


def test_notification_route_contract_and_unique_expression_index() -> None:
    """Проверяет контракт NotificationRoute и уникальность маршрута."""
    table = NotificationRoute.__table__
    columns = table.c

    assert table.name == "notification_routes"
    assert get_fk_targets(columns.clan_id) == {"clans.id"}
    assert get_fk_targets(columns.chat_id) == {"telegram_chats.chat_id"}
    assert get_fk_targets(columns.created_by_telegram_user_id) == {"telegram_users.id"}
    assert columns.notification_type.nullable is False
    assert columns.message_thread_id.nullable is True
    assert columns.enabled.nullable is False

    check_constraint_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_notification_routes_message_thread_id_positive" in check_constraint_names

    assert_no_ondelete_cascade(columns.clan_id)
    assert_no_ondelete_cascade(columns.chat_id)
    assert_no_ondelete_cascade(columns.created_by_telegram_user_id)

    route_unique_index = next(
        index
        for index in table.indexes
        if index.name == "uq_notification_routes_clan_notification_chat_thread"
    )

    assert route_unique_index.unique is True

    mapper = inspect(NotificationRoute)

    assert mapper.relationships["clan"].mapper.class_ is Clan
    assert mapper.relationships["chat"].mapper.class_ is TelegramChat
    assert mapper.relationships["created_by_user"].mapper.class_ is TelegramUser


def test_notification_log_contract_and_unique_nullable_event_key() -> None:
    """Проверяет контракт NotificationLog и event_key-дедупликацию."""
    table = NotificationLog.__table__
    columns = table.c

    assert table.name == "notification_logs"
    assert get_fk_targets(columns.route_id) == {"notification_routes.id"}
    assert get_fk_targets(columns.chat_id) == {"telegram_chats.chat_id"}
    assert columns.route_id.nullable is True
    assert columns.notification_type.nullable is False
    assert columns.event_key.unique is True
    assert columns.event_key.nullable is True
    assert columns.message_thread_id.nullable is True
    assert columns.status.nullable is False
    assert columns.telegram_message_id.nullable is True
    assert columns.payload_summary.nullable is False
    assert columns.error_text.nullable is True
    assert columns.created_at.type.timezone is True

    assert_no_ondelete_cascade(columns.route_id)
    assert_no_ondelete_cascade(columns.chat_id)

    mapper = inspect(NotificationLog)

    assert mapper.relationships["route"].mapper.class_ is NotificationRoute
    assert mapper.relationships["chat"].mapper.class_ is TelegramChat


def test_api_error_model_contract() -> None:
    """Проверяет контракт модели ApiError."""
    table = ApiError.__table__
    columns = table.c

    assert table.name == "api_errors"
    assert columns.endpoint.nullable is False
    assert columns.method.nullable is False
    assert columns.entity_type.nullable is True
    assert columns.entity_tag.nullable is True
    assert columns.status_code.nullable is True
    assert columns.message.nullable is False
    assert columns.response_snippet.nullable is True
    assert columns.exception_class.nullable is True
    assert columns.worker_name.nullable is True
    assert columns.retry_count.nullable is False
    assert columns.status.nullable is False
    assert columns.created_at.type.timezone is True
    assert columns.resolved_at.nullable is True
    assert columns.resolved_at.type.timezone is True


def test_player_event_contract_and_history_semantics() -> None:
    """Проверяет контракт PlayerEvent как истории игрока, а не audit log."""
    table = PlayerEvent.__table__
    columns = table.c

    assert table.name == "player_events"
    assert get_fk_targets(columns.telegram_user_id) == {"telegram_users.id"}
    assert columns.telegram_user_id.nullable is True
    assert columns.player_tag.nullable is True
    assert columns.event_type.nullable is False
    assert columns.title.nullable is False
    assert columns.description.nullable is True
    assert isinstance(columns.metadata_json.type, JSONB)
    assert columns.metadata_json.nullable is False
    assert columns.created_at.type.timezone is True

    assert "action" not in columns
    assert "before_json" not in columns
    assert "after_json" not in columns
    assert "ip_address" not in columns

    assert_no_ondelete_cascade(columns.telegram_user_id)

    mapper = inspect(PlayerEvent)

    assert mapper.relationships["telegram_user"].mapper.class_ is TelegramUser


def test_app_setting_uses_key_as_primary_key() -> None:
    """Проверяет key-value контракт модели AppSetting."""
    table = AppSetting.__table__
    columns = table.c

    assert table.name == "app_settings"
    assert columns.key.primary_key is True
    assert "id" not in columns
    assert isinstance(columns.value_json.type, JSONB)
    assert columns.value_json.nullable is False
    assert isinstance(columns.updated_at.type, DateTime)
    assert columns.updated_at.type.timezone is True


def test_status_like_fields_are_plain_strings_before_domain_enums() -> None:
    """Проверяет подготовку статусных полей под будущие доменные enum."""
    assert isinstance(NotificationRoute.__table__.c.notification_type.type, String)
    assert isinstance(NotificationLog.__table__.c.notification_type.type, String)
    assert isinstance(NotificationLog.__table__.c.status.type, String)
    assert isinstance(ApiError.__table__.c.status.type, String)
    assert isinstance(PlayerEvent.__table__.c.event_type.type, String)

```


## FILE: Makefile

```text
SHELL := /bin/bash

PYTHON ?= python
PIP ?= $(PYTHON) -m pip
COMPOSE_FILE ?= infra/docker-compose.yml
APP_SERVICES ?= backend worker bot
DC ?= docker compose -f $(COMPOSE_FILE)
ALEMBIC_CONFIG ?= app/db/alembic.ini
ALEMBIC_DATABASE_URL ?= postgresql+asyncpg://bn:bn@localhost:5432/bestiary
ALEMBIC ?= ALEMBIC_DATABASE_URL=$(ALEMBIC_DATABASE_URL) $(PYTHON) -m alembic -c $(ALEMBIC_CONFIG)

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "BestiaryNavigator_bot commands"
	@echo ""
	@echo "Local tooling:"
	@echo "  make install       Install runtime package"
	@echo "  make install-dev   Install runtime + dev dependencies"
	@echo "  make format        Format code with ruff"
	@echo "  make lint          Run ruff"
	@echo "  make lint-fix      Run safe ruff autofix"
	@echo "  make test          Run pytest"
	@echo "  make check         Run lint and tests"
	@echo "  make fix           Run safe autofix, format and full check"
	@echo ""
	@echo "Runtime:"
	@echo "  make first-run     Install deps, build images, run migrations and start services"
	@echo "  make up            Build images with frontend, run migrations and start services"
	@echo "  make down          Stop compose services"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-config Validate docker compose -f infra/docker-compose.yml config"
	@echo "  make docker-build  Build application images with frontend assets"
	@echo "  make docker-down   Stop and remove compose services"
	@echo ""
	@echo "Database:"
	@echo "  make db-upgrade    Run alembic upgrade head"
	@echo "  make db-downgrade  Run alembic downgrade base"
	@echo "  make db-current    Show current database revision"
	@echo "  make db-reset      Downgrade to base and upgrade to head"
	@echo "  make postgres-wait Wait until PostgreSQL is ready"
	@echo ""
	@echo "PostgreSQL:"
	@echo "  make postgres-up   Start PostgreSQL"
	@echo "  make postgres-logs Show PostgreSQL logs"
	@echo "  make postgres-ps   Show compose services"
	@echo "  make postgres-stop Stop PostgreSQL"
	@echo "  make postgres-clean Stop services and remove volumes"

.PHONY: install
install:
	$(PIP) install -e .

.PHONY: install-dev
install-dev:
	$(PIP) install -e ".[dev]"

.PHONY: format
format:
	$(PYTHON) -m ruff format .

.PHONY: lint
lint:
	$(PYTHON) -m ruff check .

.PHONY: lint-fix
lint-fix:
	$(PYTHON) -m ruff check . --fix

.PHONY: lint-fix-unsafe
lint-fix-unsafe:
	$(PYTHON) -m ruff check . --fix --unsafe-fixes

.PHONY: test
test:
	$(PYTHON) -m pytest

.PHONY: check
check: lint test

.PHONY: fix
fix: lint-fix format

.PHONY: fix-unsafe
fix-unsafe: lint-fix-unsafe format

.PHONY: docker-config
docker-config:
	$(DC) config

.PHONY: docker-build
docker-build:
	$(DC) build

.PHONY: docker-down
docker-down:
	$(DC) down

.PHONY: first-run
first-run: install-dev
	$(MAKE) up

.PHONY: up
up: docker-build postgres-up postgres-wait db-upgrade
	$(DC) up -d $(APP_SERVICES)

.PHONY: down
down:
	$(DC) down

.PHONY: db-upgrade
db-upgrade:
	$(ALEMBIC) upgrade head

.PHONY: db-downgrade
db-downgrade:
	$(ALEMBIC) downgrade base

.PHONY: db-current
db-current:
	$(ALEMBIC) current

.PHONY: db-reset
db-reset: db-downgrade db-upgrade

.PHONY: postgres-up
postgres-up:
	$(DC) up -d postgres

.PHONY: postgres-wait
postgres-wait:
	@until $(DC) exec -T postgres pg_isready -U bn -d bestiary; do \
		sleep 1; \
	done

.PHONY: postgres-logs
postgres-logs:
	$(DC) logs postgres --tail=50

.PHONY: postgres-ps
postgres-ps:
	$(DC) ps

.PHONY: postgres-stop
postgres-stop:
	$(DC) stop postgres

.PHONY: postgres-clean
postgres-clean:
	$(DC) down -v

```


## FILE: pyproject.toml

```toml
[build-system]
requires = ["setuptools>=75.0.0", "wheel>=0.44.0"]
build-backend = "setuptools.build_meta"

[project]
name = "bestiarynavigator-bot"
version = "0.1.0"
description = "Web application, Telegram bot and worker for Clash of Clans clan monitoring."
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi>=0.115.0,<1.0.0",
    "uvicorn[standard]>=0.30.0,<1.0.0",
    "sqlalchemy[asyncio]>=2.0.0,<3.0.0",
    "alembic>=1.13.0,<2.0.0",
    "asyncpg>=0.29.0,<1.0.0",
    "httpx>=0.27.0,<1.0.0",
    "pydantic-settings>=2.3.0,<3.0.0",
    "aiogram>=3.13.0,<4.0.0",
    "jinja2>=3.1.0,<4.0.0",
    "typing-extensions>=4.12.0,<5.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0,<10.0.0",
    "pytest-asyncio>=0.24.0,<2.0.0",
    "ruff>=0.8.0,<1.0.0",
    "mypy>=1.13.0,<2.0.0",
]

[tool.setuptools.packages.find]
where = ["."]
include = ["app*"]

[tool.pytest.ini_options]
minversion = "8.0"
addopts = "-q"
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
target-version = "py312"
line-length = 100
src = ["app", "tests"]

[tool.ruff.lint]
select = [
    "E",
    "F",
    "I",
    "B",
    "UP",
    "SIM",
    "RUF",
]
ignore = [
    "RUF001",
    "RUF002",
    "RUF003",
]

[tool.ruff.lint.isort]
known-first-party = ["app"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
line-ending = "lf"

[tool.mypy]
python_version = "3.12"
packages = ["app"]
strict = false
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
no_implicit_optional = true
show_error_codes = true
pretty = true
exclude = [
    "^app/db/migrations/",
]
```


## FILE: .env.example

```env
# Application
APP_ENV=local
APP_BASE_URL=http://localhost:8000

# Database
DATABASE_URL=postgresql+asyncpg://bn:bn@postgres:5432/bestiary
ALEMBIC_DATABASE_URL=postgresql+asyncpg://bn:bn@localhost:5432/bestiary

# Clash of Clans API
CLASH_API_BASE_URL=https://api.clashofclans.com/v1
CLASH_API_TOKEN=replace-with-clash-api-token
CLASH_API_TIMEOUT_SECONDS=10

# Telegram
TELEGRAM_BOT_TOKEN=replace-with-telegram-bot-token
TELEGRAM_ADMIN_ID=123456789

# Web session
WEB_SESSION_SECRET=replace-with-random-session-secret-at-least-32-chars
WEB_ADMIN_COOKIE_NAME=bn_admin_session

# Worker
SYNC_DEFAULT_INTERVAL_SECONDS=900
ROLE_SNAPSHOT_MAX_AGE_MINUTES=30
NOTIFICATION_EVENING_HOUR_UTC=18
NOTIFICATION_DAILY_REPORT_HOUR_UTC=9

# Docker Compose PostgreSQL service
POSTGRES_DB=bestiary
POSTGRES_USER=bn
POSTGRES_PASSWORD=bn
POSTGRES_PORT=5432

# Docker Compose ports
BACKEND_PORT=8000
```
