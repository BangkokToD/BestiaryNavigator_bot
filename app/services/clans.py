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
