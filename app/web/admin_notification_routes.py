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
