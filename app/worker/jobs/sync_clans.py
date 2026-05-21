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
