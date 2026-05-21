"""Тесты worker job истечения warn по сезонам ЛВК."""

import asyncio
from datetime import UTC, datetime

from app.db.models import CwlSeason
from app.services import WarningExpirationResult
from app.worker.jobs import (
    EXPIRE_WARNINGS_BY_CWL_SEASON_JOB_NAME,
    ExpireWarningsByCwlSeasonJob,
)
from app.worker.scheduler import WorkerJobContext, create_default_worker_registry


class InMemoryExpireWarningsByCwlSeasonRepository:
    """In-memory repository для unit-тестов expire warnings job."""

    def __init__(self, cwl_seasons: list[CwlSeason]) -> None:
        """Инициализирует repository.

        Args:
            cwl_seasons: Последние сезоны ЛВК для обработки.
        """
        self.cwl_seasons = cwl_seasons
        self.flush_count = 0

    async def list_latest_cwl_seasons(self) -> tuple[CwlSeason, ...]:
        """Возвращает сезоны ЛВК для обработки.

        Returns:
            Tuple сезонов ЛВК.
        """
        return tuple(self.cwl_seasons)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


class FakeWarningExpirationProcessor:
    """Fake processor истечения warn по сезону ЛВК."""

    def __init__(self, expired_counts_by_season_id: dict[int, int]) -> None:
        """Инициализирует processor.

        Args:
            expired_counts_by_season_id: Mapping `season_id -> expired_count`.
        """
        self.expired_counts_by_season_id = expired_counts_by_season_id
        self.calls: list[tuple[int, datetime | None]] = []

    async def expire_warnings_by_cwl_season(
        self,
        *,
        current_cwl_season: CwlSeason,
        expired_at: datetime | None = None,
    ) -> WarningExpirationResult:
        """Возвращает настроенный результат истечения warn.

        Args:
            current_cwl_season: Сезон ЛВК.
            expired_at: Время истечения.

        Returns:
            Результат истечения warn.
        """
        self.calls.append((current_cwl_season.id, expired_at))
        expired_count = self.expired_counts_by_season_id.get(current_cwl_season.id, 0)
        self.expired_counts_by_season_id[current_cwl_season.id] = 0

        return WarningExpirationResult(
            expired_warnings=[],
            expired_count=expired_count,
        )


async def test_expire_warnings_by_cwl_season_job_processes_latest_seasons() -> None:
    """Проверяет обработку последних CWL seasons по кланам."""
    expired_at = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    first_season = _make_cwl_season(season_id=1, clan_id=10, season="2026-05")
    second_season = _make_cwl_season(season_id=2, clan_id=20, season="2026-06")
    repository = InMemoryExpireWarningsByCwlSeasonRepository([first_season, second_season])
    processor = FakeWarningExpirationProcessor({1: 2, 2: 3})
    job = ExpireWarningsByCwlSeasonJob(
        repository=repository,
        warning_expiration_processor=processor,
        clock=lambda: expired_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_season_count == 2
    assert result.processed_season_count == 2
    assert result.expired_count == 5
    assert processor.calls == [(1, expired_at), (2, expired_at)]
    assert repository.flush_count == 1


async def test_expire_warnings_by_cwl_season_job_is_idempotent() -> None:
    """Проверяет повторный worker-run без повторного истечения warn."""
    expired_at = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    cwl_season = _make_cwl_season(season_id=1, clan_id=10, season="2026-05")
    repository = InMemoryExpireWarningsByCwlSeasonRepository([cwl_season])
    processor = FakeWarningExpirationProcessor({1: 2})
    job = ExpireWarningsByCwlSeasonJob(
        repository=repository,
        warning_expiration_processor=processor,
        clock=lambda: expired_at,
    )

    first_result = await job.run(_build_context())
    second_result = await job.run(_build_context())

    assert first_result.expired_count == 2
    assert second_result.expired_count == 0
    assert processor.calls == [(1, expired_at), (1, expired_at)]
    assert repository.flush_count == 2


def test_default_worker_registry_registers_expire_warnings_by_cwl_season_job() -> None:
    """Проверяет, что default registry подключает expire warnings job."""
    registry = create_default_worker_registry(default_interval_seconds=900)

    job = registry.get(EXPIRE_WARNINGS_BY_CWL_SEASON_JOB_NAME)

    assert job.name == EXPIRE_WARNINGS_BY_CWL_SEASON_JOB_NAME
    assert job.run_in_transaction is True
    assert job.run_on_start is True
    assert registry.resolve_interval_seconds(job) == 900


def _make_cwl_season(*, season_id: int, clan_id: int, season: str) -> CwlSeason:
    """Создаёт CWL season для unit-тестов.

    Args:
        season_id: DB ID сезона.
        clan_id: DB ID клана.
        season: Ключ сезона.

    Returns:
        Модель CWL season.
    """
    return CwlSeason(
        id=season_id,
        clan_id=clan_id,
        season=season,
        state="inWar",
        started_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
    )


def _build_context() -> WorkerJobContext:
    """Создаёт runtime-контекст для unit-тестов job.

    Returns:
        Контекст worker job без DB session.
    """
    return WorkerJobContext(
        job_name=EXPIRE_WARNINGS_BY_CWL_SEASON_JOB_NAME,
        started_at=datetime(2026, 6, 1, tzinfo=UTC),
        stop_event=asyncio.Event(),
        session=None,
    )
