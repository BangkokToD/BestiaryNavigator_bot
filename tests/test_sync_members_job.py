"""Тесты worker job синхронизации составов кланов."""

import asyncio
from datetime import UTC, datetime

from app.db.models import ApiError, Clan
from app.domain import ClanType
from app.integrations.clash import ClashClanMember, ClashForbiddenError
from app.services.member_lifecycle import MemberLifecycleResult
from app.worker.jobs import SYNC_MEMBERS_JOB_NAME, SyncMembersJob
from app.worker.scheduler import WorkerJobContext, create_default_worker_registry


class InMemorySyncMembersRepository:
    """In-memory repository для unit-тестов sync members job."""

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


class FakeClashClanMembersProvider:
    """Fake Clash API provider для sync members job."""

    def __init__(self, results: dict[str, list[ClashClanMember] | BaseException]) -> None:
        """Инициализирует provider.

        Args:
            results: Mapping `clan_tag -> members или exception`.
        """
        self.results = results
        self.calls: list[str] = []

    async def get_clan_members(self, clan_tag: str) -> list[ClashClanMember]:
        """Возвращает состав клана или выбрасывает настроенную ошибку.

        Args:
            clan_tag: Тег клана.

        Returns:
            Список участников клана.

        Raises:
            BaseException: Если fake настроен на ошибку.
        """
        self.calls.append(clan_tag)
        result = self.results[clan_tag]

        if isinstance(result, BaseException):
            raise result

        return result


class FakeMemberLifecycleProcessor:
    """Fake lifecycle processor для unit-тестов sync members job."""

    def __init__(self, results: dict[str, MemberLifecycleResult]) -> None:
        """Инициализирует processor.

        Args:
            results: Mapping `clan_tag -> lifecycle result`.
        """
        self.results = results
        self.calls: list[tuple[str, list[str]]] = []

    async def process_clan_members(
        self,
        *,
        clan: Clan,
        members: list[ClashClanMember],
    ) -> MemberLifecycleResult:
        """Фиксирует вызов обработки состава.

        Args:
            clan: Клан.
            members: Участники клана.

        Returns:
            Настроенный результат обработки состава.
        """
        self.calls.append((clan.tag, [member.player_tag for member in members]))
        return self.results[clan.tag]


async def test_sync_members_job_processes_only_active_clans_on_happy_path() -> None:
    """Проверяет успешную синхронизацию состава active-клана."""
    active_clan = _make_clan(clan_id=1, tag="#MAIN", is_active=True)
    inactive_clan = _make_clan(clan_id=2, tag="#OFF", is_active=False)
    members = [_make_member(player_tag="#2ABC"), _make_member(player_tag="#3DEF")]

    repository = InMemorySyncMembersRepository([active_clan, inactive_clan])
    clash_provider = FakeClashClanMembersProvider({"#MAIN": members})
    lifecycle_processor = FakeMemberLifecycleProcessor(
        {
            "#MAIN": MemberLifecycleResult(
                clan=active_clan,
                created_count=2,
                updated_count=0,
                left_count=0,
                moved_count=0,
                current_count=2,
            )
        }
    )
    job = SyncMembersJob(
        repository=repository,
        clash_client=clash_provider,
        member_lifecycle_processor=lifecycle_processor,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 2
    assert result.synced_count == 1
    assert result.failed_count == 0
    assert result.skipped_inactive_count == 1
    assert result.created_count == 2
    assert result.updated_count == 0
    assert result.left_count == 0
    assert result.moved_count == 0
    assert result.current_count == 2
    assert clash_provider.calls == ["#MAIN"]
    assert lifecycle_processor.calls == [("#MAIN", ["#2ABC", "#3DEF"])]
    assert repository.api_errors == []
    assert repository.flush_count == 1


async def test_sync_members_job_records_api_error_and_continues_next_clan() -> None:
    """Проверяет error path: ApiError пишется, следующий клан синхронизируется."""
    broken_clan = _make_clan(clan_id=1, tag="#ERR", is_active=True)
    healthy_clan = _make_clan(clan_id=2, tag="#MAIN", is_active=True)
    clash_error = ClashForbiddenError(
        "Clash API returned HTTP 403 for GET clans/%23ERR/members.",
        endpoint="clans/%23ERR/members",
        method="GET",
        status_code=403,
        response_snippet='{"reason":"accessDenied"}',
    )

    repository = InMemorySyncMembersRepository([broken_clan, healthy_clan])
    clash_provider = FakeClashClanMembersProvider(
        {
            "#ERR": clash_error,
            "#MAIN": [_make_member(player_tag="#2ABC")],
        }
    )
    lifecycle_processor = FakeMemberLifecycleProcessor(
        {
            "#MAIN": MemberLifecycleResult(
                clan=healthy_clan,
                created_count=1,
                updated_count=0,
                left_count=0,
                moved_count=0,
                current_count=1,
            )
        }
    )
    job = SyncMembersJob(
        repository=repository,
        clash_client=clash_provider,
        member_lifecycle_processor=lifecycle_processor,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 2
    assert result.synced_count == 1
    assert result.failed_count == 1
    assert result.created_count == 1
    assert clash_provider.calls == ["#ERR", "#MAIN"]
    assert lifecycle_processor.calls == [("#MAIN", ["#2ABC"])]

    assert len(repository.api_errors) == 1
    api_error = repository.api_errors[0]
    assert api_error.endpoint == "clans/%23ERR/members"
    assert api_error.method == "GET"
    assert api_error.entity_type == "clan_members"
    assert api_error.entity_tag == "#ERR"
    assert api_error.status_code == 403
    assert api_error.response_snippet == '{"reason":"accessDenied"}'
    assert api_error.exception_class == "ClashForbiddenError"
    assert api_error.worker_name == SYNC_MEMBERS_JOB_NAME
    assert api_error.retry_count == 0
    assert api_error.status == "unresolved"
    assert repository.flush_count == 1


def test_default_worker_registry_registers_sync_members_job() -> None:
    """Проверяет, что default worker registry подключает sync members job."""
    registry = create_default_worker_registry(default_interval_seconds=900)

    job = registry.get(SYNC_MEMBERS_JOB_NAME)

    assert job.name == SYNC_MEMBERS_JOB_NAME
    assert job.run_in_transaction is True
    assert job.run_on_start is True
    assert registry.resolve_interval_seconds(job) == 900


def _make_clan(*, clan_id: int, tag: str, is_active: bool) -> Clan:
    """Создаёт клан для unit-тестов.

    Args:
        clan_id: DB id клана.
        tag: Нормализованный тег клана.
        is_active: Флаг активного мониторинга.

    Returns:
        Модель клана.
    """
    return Clan(
        id=clan_id,
        tag=tag,
        name="Bestiary",
        type=ClanType.MAIN.value,
        is_active=is_active,
    )


def _make_member(*, player_tag: str, name: str = "Bangkok") -> ClashClanMember:
    """Создаёт DTO участника клана.

    Args:
        player_tag: Нормализованный тег игрока.
        name: Ник игрока.

    Returns:
        DTO участника клана.
    """
    return ClashClanMember(
        player_tag=player_tag,
        name=name,
        role="member",
        town_hall_level=16,
        exp_level=233,
        trophies=5200,
        donations=100,
        donations_received=80,
    )


def _build_context() -> WorkerJobContext:
    """Создаёт runtime-контекст для unit-тестов job.

    Returns:
        Контекст worker job без DB session.
    """
    return WorkerJobContext(
        job_name=SYNC_MEMBERS_JOB_NAME,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        stop_event=asyncio.Event(),
        session=None,
    )
