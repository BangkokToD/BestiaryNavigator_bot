"""Worker job истечения warn при новом сезоне ЛВК."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CwlSeason
from app.services import WarningExpirationResult, WarningLifecycleService
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

EXPIRE_WARNINGS_BY_CWL_SEASON_JOB_NAME = "expire_warnings_by_cwl_season"


class WarningExpirationProcessor(Protocol):
    """Contract сервиса истечения warn по сезону ЛВК."""

    async def expire_warnings_by_cwl_season(
        self,
        *,
        current_cwl_season: CwlSeason,
        expired_at: datetime | None = None,
    ) -> WarningExpirationResult:
        """Переводит старые active warn в expired.

        Args:
            current_cwl_season: Последний обнаруженный сезон ЛВК клана.
            expired_at: Время истечения для детерминированных тестов.

        Returns:
            Результат истечения warn.
        """


class ExpireWarningsByCwlSeasonRepository(Protocol):
    """Repository contract для expire warnings by cwl season job."""

    async def list_latest_cwl_seasons(self) -> tuple[CwlSeason, ...]:
        """Возвращает последние известные CWL seasons по каждому клану.

        Returns:
            Tuple последних CWL seasons.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyExpireWarningsByCwlSeasonRepository:
    """SQLAlchemy repository для expire warnings by cwl season job."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_latest_cwl_seasons(self) -> tuple[CwlSeason, ...]:
        """Возвращает последний CWL season для каждого клана.

        Returns:
            Tuple последних сезонов в стабильном порядке по clan_id.
        """
        result = await self._session.execute(
            select(CwlSeason).order_by(
                CwlSeason.clan_id,
                CwlSeason.started_at.desc(),
                CwlSeason.id.desc(),
            )
        )

        latest_by_clan_id: dict[int, CwlSeason] = {}
        for cwl_season in result.scalars().all():
            if cwl_season.clan_id not in latest_by_clan_id:
                latest_by_clan_id[cwl_season.clan_id] = cwl_season

        return tuple(latest_by_clan_id.values())

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class ExpireWarningsByCwlSeasonJobResult:
    """Результат одного запуска expire warnings by cwl season job."""

    discovered_season_count: int
    processed_season_count: int
    expired_count: int


class ExpireWarningsByCwlSeasonJob:
    """Job истечения старых system impactful warn при новом CWL season."""

    def __init__(
        self,
        *,
        repository: ExpireWarningsByCwlSeasonRepository,
        warning_expiration_processor: WarningExpirationProcessor,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository последних CWL seasons.
            warning_expiration_processor: Сервис жизненного цикла warn.
            clock: Источник текущего времени для тестов.
        """
        self._repository = repository
        self._warning_expiration_processor = warning_expiration_processor
        self._clock = clock or _utc_now

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "ExpireWarningsByCwlSeasonJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemyExpireWarningsByCwlSeasonRepository(session),
            warning_expiration_processor=WarningLifecycleService.from_session(session=session),
        )

    async def run(self, context: WorkerJobContext) -> ExpireWarningsByCwlSeasonJobResult:
        """Истекает старые warn по последним CWL seasons.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        latest_cwl_seasons = await self._repository.list_latest_cwl_seasons()
        expired_at = self._clock()

        processed_season_count = 0
        expired_count = 0

        for cwl_season in latest_cwl_seasons:
            if context.should_stop:
                break

            result = await self._warning_expiration_processor.expire_warnings_by_cwl_season(
                current_cwl_season=cwl_season,
                expired_at=expired_at,
            )
            processed_season_count += 1
            expired_count += result.expired_count

        await self._repository.flush()

        return ExpireWarningsByCwlSeasonJobResult(
            discovered_season_count=len(latest_cwl_seasons),
            processed_season_count=processed_season_count,
            expired_count=expired_count,
        )


def register_expire_warnings_by_cwl_season_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует expire warnings by cwl season job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=EXPIRE_WARNINGS_BY_CWL_SEASON_JOB_NAME)
    async def expire_warnings_by_cwl_season(context: WorkerJobContext) -> None:
        """Запускает истечение warn внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("expire_warnings_by_cwl_season требует DB session.")

        job = ExpireWarningsByCwlSeasonJob.from_session(session=context.session)
        await job.run(context)


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "EXPIRE_WARNINGS_BY_CWL_SEASON_JOB_NAME",
    "ExpireWarningsByCwlSeasonJob",
    "ExpireWarningsByCwlSeasonJobResult",
    "ExpireWarningsByCwlSeasonRepository",
    "SqlAlchemyExpireWarningsByCwlSeasonRepository",
    "WarningExpirationProcessor",
    "register_expire_warnings_by_cwl_season_job",
]
