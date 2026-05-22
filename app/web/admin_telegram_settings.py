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
