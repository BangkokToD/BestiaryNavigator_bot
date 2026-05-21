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
