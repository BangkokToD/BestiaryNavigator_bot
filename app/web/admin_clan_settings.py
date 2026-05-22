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
