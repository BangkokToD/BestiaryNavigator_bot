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
