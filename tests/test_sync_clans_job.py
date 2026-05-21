"""Тесты worker job синхронизации кланов."""

import asyncio
from datetime import UTC, datetime

from app.db.models import ApiError, Clan
from app.domain import ClanType
from app.integrations.clash import ClashClan, ClashForbiddenError
from app.worker.jobs import SYNC_CLANS_JOB_NAME, SyncClansJob
from app.worker.scheduler import WorkerJobContext, create_default_worker_registry


class InMemorySyncClansRepository:
    """In-memory repository для unit-тестов sync clans job."""

    def __init__(self, clans: list[Clan]) -> None:
        """Инициализирует repository.

        Args:
            clans: Набор кланов для тестового запуска.
        """
        self.clans = clans
        self.api_errors: list[ApiError] = []
        self.flush_count = 0

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает кланы для тестового запуска.

        Repository намеренно возвращает все кланы, чтобы тест зафиксировал
        дополнительный guard job против случайной синхронизации inactive-клана.

        Returns:
            Tuple кланов.
        """
        return tuple(self.clans)

    def add_api_error(self, api_error: ApiError) -> None:
        """Сохраняет ошибку API в памяти.

        Args:
            api_error: Модель ошибки внешнего API.
        """
        self.api_errors.append(api_error)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


class FakeClashClanSyncProvider:
    """Fake Clash API provider для sync clans job."""

    def __init__(self, results: dict[str, ClashClan | BaseException]) -> None:
        """Инициализирует provider.

        Args:
            results: Mapping `clan_tag -> DTO или exception`.
        """
        self.results = results
        self.calls: list[str] = []

    async def get_clan(self, clan_tag: str) -> ClashClan:
        """Возвращает DTO клана или выбрасывает настроенную ошибку.

        Args:
            clan_tag: Тег клана.

        Returns:
            DTO клана.

        Raises:
            BaseException: Если fake настроен на ошибку.
        """
        self.calls.append(clan_tag)
        result = self.results[clan_tag]

        if isinstance(result, BaseException):
            raise result

        return result


async def test_sync_clans_job_updates_only_active_clans_on_happy_path() -> None:
    """Проверяет успешную синхронизацию active-клана и пропуск inactive-клана."""
    old_sync_at = datetime(2026, 1, 1, tzinfo=UTC)
    new_sync_at = datetime(2026, 1, 2, tzinfo=UTC)

    active_clan = Clan(
        tag="#2ABC",
        name="Old name",
        type=ClanType.MAIN.value,
        level=1,
        badge_url=None,
        is_active=True,
        last_sync_at=old_sync_at,
        sync_status=None,
    )
    inactive_clan = Clan(
        tag="#BAD",
        name="Inactive",
        type=ClanType.ACADEMY.value,
        level=5,
        badge_url=None,
        is_active=False,
        last_sync_at=None,
        sync_status=None,
    )

    repository = InMemorySyncClansRepository([active_clan, inactive_clan])
    clash_provider = FakeClashClanSyncProvider(
        {
            "#2ABC": ClashClan(
                tag="#2ABC",
                name="Fresh name",
                level=20,
                badge_url="https://example.test/fresh-badge.png",
                members_count=49,
            )
        }
    )
    job = SyncClansJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: new_sync_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 2
    assert result.synced_count == 1
    assert result.failed_count == 0
    assert result.skipped_inactive_count == 1
    assert clash_provider.calls == ["#2ABC"]

    assert active_clan.name == "Fresh name"
    assert active_clan.level == 20
    assert active_clan.badge_url == "https://example.test/fresh-badge.png"
    assert active_clan.sync_status == "ok"
    assert active_clan.last_sync_at == new_sync_at

    assert inactive_clan.name == "Inactive"
    assert inactive_clan.level == 5
    assert inactive_clan.last_sync_at is None
    assert inactive_clan.sync_status is None

    assert repository.api_errors == []
    assert repository.flush_count == 1


async def test_sync_clans_job_records_api_error_and_continues_next_clan() -> None:
    """Проверяет error path: ApiError пишется, следующий клан синхронизируется."""
    old_sync_at = datetime(2026, 1, 1, tzinfo=UTC)
    new_sync_at = datetime(2026, 1, 2, tzinfo=UTC)

    broken_clan = Clan(
        tag="#ERR",
        name="Broken",
        type=ClanType.MAIN.value,
        level=10,
        badge_url="https://example.test/old-badge.png",
        is_active=True,
        last_sync_at=old_sync_at,
        sync_status="ok",
    )
    healthy_clan = Clan(
        tag="#2ABC",
        name="Old name",
        type=ClanType.MAIN.value,
        level=1,
        badge_url=None,
        is_active=True,
        last_sync_at=None,
        sync_status=None,
    )
    clash_error = ClashForbiddenError(
        "Clash API returned HTTP 403 for GET clans/%23ERR.",
        endpoint="clans/%23ERR",
        method="GET",
        status_code=403,
        response_snippet='{"reason":"accessDenied"}',
    )

    repository = InMemorySyncClansRepository([broken_clan, healthy_clan])
    clash_provider = FakeClashClanSyncProvider(
        {
            "#ERR": clash_error,
            "#2ABC": ClashClan(
                tag="#2ABC",
                name="Fresh name",
                level=18,
                badge_url="https://example.test/fresh-badge.png",
                members_count=44,
            ),
        }
    )
    job = SyncClansJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: new_sync_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 2
    assert result.synced_count == 1
    assert result.failed_count == 1
    assert result.skipped_inactive_count == 0
    assert clash_provider.calls == ["#ERR", "#2ABC"]

    assert broken_clan.name == "Broken"
    assert broken_clan.level == 10
    assert broken_clan.badge_url == "https://example.test/old-badge.png"
    assert broken_clan.last_sync_at == old_sync_at
    assert broken_clan.sync_status == "error"

    assert healthy_clan.name == "Fresh name"
    assert healthy_clan.level == 18
    assert healthy_clan.badge_url == "https://example.test/fresh-badge.png"
    assert healthy_clan.last_sync_at == new_sync_at
    assert healthy_clan.sync_status == "ok"

    assert len(repository.api_errors) == 1
    api_error = repository.api_errors[0]
    assert api_error.endpoint == "clans/%23ERR"
    assert api_error.method == "GET"
    assert api_error.entity_type == "clan"
    assert api_error.entity_tag == "#ERR"
    assert api_error.status_code == 403
    assert api_error.response_snippet == '{"reason":"accessDenied"}'
    assert api_error.exception_class == "ClashForbiddenError"
    assert api_error.worker_name == SYNC_CLANS_JOB_NAME
    assert api_error.retry_count == 0
    assert api_error.status == "unresolved"

    assert repository.flush_count == 1


def test_default_worker_registry_registers_sync_clans_job() -> None:
    """Проверяет, что default worker registry подключает sync clans job."""
    registry = create_default_worker_registry(default_interval_seconds=900)

    job = registry.get(SYNC_CLANS_JOB_NAME)

    assert job.name == SYNC_CLANS_JOB_NAME
    assert job.run_in_transaction is True
    assert job.run_on_start is True
    assert registry.resolve_interval_seconds(job) == 900


def _build_context() -> WorkerJobContext:
    """Создаёт runtime-контекст для unit-тестов job.

    Returns:
        Контекст worker job без DB session.
    """
    return WorkerJobContext(
        job_name=SYNC_CLANS_JOB_NAME,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        stop_event=asyncio.Event(),
        session=None,
    )
