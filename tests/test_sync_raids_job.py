"""Тесты worker job синхронизации рейдов столицы клана."""

import asyncio
from datetime import UTC, datetime

from app.db.models import ApiError, Clan, RaidMember, RaidSeason
from app.domain import ClanType, RaidMemberStatus
from app.integrations.clash import ClashCapitalRaidSeason, ClashForbiddenError, ClashRaidMember
from app.worker.jobs import SYNC_RAIDS_JOB_NAME, SyncRaidsJob
from app.worker.scheduler import WorkerJobContext, create_default_worker_registry


class InMemorySyncRaidsRepository:
    """In-memory repository для unit-тестов sync raids job."""

    def __init__(self, clans: list[Clan]) -> None:
        """Инициализирует repository.

        Args:
            clans: Набор кланов для тестового запуска.
        """
        self.clans = clans
        self.raid_seasons: list[RaidSeason] = []
        self.raid_members: list[RaidMember] = []
        self.api_errors: list[ApiError] = []
        self.flush_count = 0
        self._next_raid_season_id = 1

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает кланы для тестового запуска.

        Returns:
            Tuple кланов.
        """
        return tuple(self.clans)

    async def get_raid_season_by_start_time(
        self,
        *,
        clan_id: int,
        start_time: datetime,
    ) -> RaidSeason | None:
        """Возвращает рейдовый сезон по клану и времени старта.

        Args:
            clan_id: DB ID клана.
            start_time: Время начала сезона.

        Returns:
            Модель сезона или `None`.
        """
        for raid_season in self.raid_seasons:
            if raid_season.clan_id == clan_id and raid_season.start_time == start_time:
                return raid_season

        return None

    def add_raid_season(self, raid_season: RaidSeason) -> None:
        """Добавляет рейдовый сезон в память.

        Args:
            raid_season: Новая модель сезона.
        """
        raid_season.id = self._next_raid_season_id
        self._next_raid_season_id += 1
        self.raid_seasons.append(raid_season)

    async def replace_raid_members(
        self,
        *,
        raid_season: RaidSeason,
        members: tuple[RaidMember, ...],
    ) -> None:
        """Заменяет участников рейдового сезона в памяти.

        Args:
            raid_season: Модель сезона.
            members: Актуальные участники.
        """
        raid_season_id = raid_season.id
        self.raid_members = [
            member for member in self.raid_members if member.raid_season_id != raid_season_id
        ]

        for member in members:
            member.raid_season_id = raid_season_id
            member.raid_season = raid_season
            self.raid_members.append(member)

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в память.

        Args:
            api_error: Модель ошибки внешнего API.
        """
        self.api_errors.append(api_error)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


class FakeClashRaidSeasonsProvider:
    """Fake Clash API provider для sync raids job."""

    def __init__(
        self,
        results: dict[str, list[ClashCapitalRaidSeason] | BaseException],
    ) -> None:
        """Инициализирует provider.

        Args:
            results: Mapping `clan_tag -> raid seasons или exception`.
        """
        self.results = results
        self.calls: list[tuple[str, int | None]] = []

    async def get_capital_raid_seasons(
        self,
        clan_tag: str,
        *,
        limit: int | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> list[ClashCapitalRaidSeason]:
        """Возвращает рейдовые сезоны или выбрасывает настроенную ошибку.

        Args:
            clan_tag: Тег клана.
            limit: Ограничение количества сезонов.
            after: Pagination marker after.
            before: Pagination marker before.

        Returns:
            Список рейдовых сезонов.

        Raises:
            BaseException: Если fake настроен на ошибку.
        """
        _ = after, before
        self.calls.append((clan_tag, limit))
        result = self.results[clan_tag]

        if isinstance(result, BaseException):
            raise result

        return result


async def test_sync_raids_job_creates_raid_season_and_members_for_main_and_academy() -> None:
    """Проверяет создание рейдового сезона для main и academy."""
    snapshot_at = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    main_clan = _make_clan(clan_id=1, tag="#MAIN", clan_type=ClanType.MAIN)
    academy_clan = _make_clan(clan_id=2, tag="#ACA", clan_type=ClanType.ACADEMY)
    repository = InMemorySyncRaidsRepository([main_clan, academy_clan])
    clash_provider = FakeClashRaidSeasonsProvider(
        {
            "#MAIN": [
                _make_raid_season(
                    members=[
                        _make_raid_member(player_tag="#P1", attacks=6),
                    ]
                )
            ],
            "#ACA": [
                _make_raid_season(
                    members=[
                        _make_raid_member(player_tag="#P2", attacks=5),
                    ]
                )
            ],
        }
    )
    job = SyncRaidsJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: snapshot_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 2
    assert result.synced_count == 2
    assert result.created_count == 2
    assert result.updated_count == 0
    assert result.member_count == 2
    assert clash_provider.calls == [("#MAIN", 1), ("#ACA", 1)]
    assert repository.flush_count == 1

    assert len(repository.raid_seasons) == 2
    assert repository.raid_seasons[0].clan_id == 1
    assert repository.raid_seasons[0].state == "ended"
    assert repository.raid_seasons[0].start_time == datetime(2026, 5, 17, 7, 0, tzinfo=UTC)
    assert repository.raid_seasons[0].end_time == datetime(2026, 5, 20, 7, 0, tzinfo=UTC)
    assert repository.raid_seasons[0].snapshot_at == snapshot_at

    assert len(repository.raid_members) == 2
    assert repository.raid_members[0].player_tag == "#P1"
    assert repository.raid_members[0].attacks == 6
    assert repository.raid_members[0].project_expected_attacks == 6
    assert repository.raid_members[0].status == RaidMemberStatus.RAID_FULL.value

    assert repository.raid_members[1].player_tag == "#P2"
    assert repository.raid_members[1].attacks == 5
    assert repository.raid_members[1].project_expected_attacks == 6
    assert repository.raid_members[1].status == RaidMemberStatus.RAID_INCOMPLETE.value


async def test_sync_raids_job_skips_freezer_and_inactive_clans() -> None:
    """Проверяет, что freezer и inactive clans не синхронизируются."""
    main_clan = _make_clan(clan_id=1, tag="#MAIN", clan_type=ClanType.MAIN)
    freezer_clan = _make_clan(clan_id=2, tag="#FREEZE", clan_type=ClanType.FREEZER)
    inactive_clan = _make_clan(
        clan_id=3,
        tag="#OFF",
        clan_type=ClanType.MAIN,
        is_active=False,
    )
    repository = InMemorySyncRaidsRepository([main_clan, freezer_clan, inactive_clan])
    clash_provider = FakeClashRaidSeasonsProvider(
        {
            "#MAIN": [_make_raid_season(members=[])],
        }
    )
    job = SyncRaidsJob(repository=repository, clash_client=clash_provider)

    result = await job.run(_build_context())

    assert result.discovered_count == 3
    assert result.synced_count == 1
    assert result.skipped_unsupported_clan_count == 1
    assert result.skipped_inactive_count == 1
    assert clash_provider.calls == [("#MAIN", 1)]
    assert len(repository.raid_seasons) == 1


async def test_sync_raids_job_calculates_statuses_for_zero_to_six_and_more_attacks() -> None:
    """Проверяет статусы 0–4, 5, 6 и больше 6 атак."""
    clan = _make_clan(clan_id=1, tag="#MAIN", clan_type=ClanType.MAIN)
    repository = InMemorySyncRaidsRepository([clan])
    clash_provider = FakeClashRaidSeasonsProvider(
        {
            "#MAIN": [
                _make_raid_season(
                    members=[
                        _make_raid_member(player_tag="#P0", attacks=None),
                        _make_raid_member(player_tag="#P4", attacks=4),
                        _make_raid_member(player_tag="#P5", attacks=5),
                        _make_raid_member(player_tag="#P6", attacks=6),
                        _make_raid_member(player_tag="#P7", attacks=7),
                    ]
                )
            ],
        }
    )
    job = SyncRaidsJob(repository=repository, clash_client=clash_provider)

    result = await job.run(_build_context())

    assert result.synced_count == 1
    assert result.member_count == 5
    statuses = {member.player_tag: member.status for member in repository.raid_members}
    attacks = {member.player_tag: member.attacks for member in repository.raid_members}

    assert attacks["#P0"] == 0
    assert attacks["#P4"] == 4
    assert attacks["#P5"] == 5
    assert attacks["#P6"] == 6
    assert attacks["#P7"] == 7

    assert statuses["#P0"] == RaidMemberStatus.RAID_MISSED.value
    assert statuses["#P4"] == RaidMemberStatus.RAID_MISSED.value
    assert statuses["#P5"] == RaidMemberStatus.RAID_INCOMPLETE.value
    assert statuses["#P6"] == RaidMemberStatus.RAID_FULL.value
    assert statuses["#P7"] == RaidMemberStatus.RAID_FULL.value
    assert all(member.project_expected_attacks == 6 for member in repository.raid_members)


async def test_sync_raids_job_updates_existing_season_without_duplicate_members() -> None:
    """Проверяет повторный sync без дубля RaidSeason и RaidMember."""
    first_snapshot_at = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    second_snapshot_at = datetime(2026, 5, 20, 13, 0, tzinfo=UTC)
    clan = _make_clan(clan_id=1, tag="#MAIN", clan_type=ClanType.MAIN)
    repository = InMemorySyncRaidsRepository([clan])
    clash_provider = FakeClashRaidSeasonsProvider(
        {
            "#MAIN": [
                _make_raid_season(
                    total_attacks=5,
                    members=[
                        _make_raid_member(player_tag="#P1", attacks=5),
                    ],
                )
            ],
        }
    )
    first_job = SyncRaidsJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: first_snapshot_at,
    )

    first_result = await first_job.run(_build_context())

    clash_provider.results["#MAIN"] = [
        _make_raid_season(
            total_attacks=6,
            members=[
                _make_raid_member(player_tag="#P1", attacks=6),
                _make_raid_member(player_tag="#P2", attacks=0),
            ],
        )
    ]
    second_job = SyncRaidsJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: second_snapshot_at,
    )
    second_result = await second_job.run(_build_context())

    assert first_result.created_count == 1
    assert first_result.updated_count == 0
    assert second_result.created_count == 0
    assert second_result.updated_count == 1
    assert len(repository.raid_seasons) == 1
    assert len(repository.raid_members) == 2

    season = repository.raid_seasons[0]
    assert season.total_attacks == 6
    assert season.snapshot_at == second_snapshot_at

    statuses = {member.player_tag: member.status for member in repository.raid_members}
    assert statuses["#P1"] == RaidMemberStatus.RAID_FULL.value
    assert statuses["#P2"] == RaidMemberStatus.RAID_MISSED.value
    assert repository.flush_count == 2


async def test_sync_raids_job_records_api_error_and_continues_next_clan() -> None:
    """Проверяет error path: ApiError пишется, следующий клан синхронизируется."""
    broken_clan = _make_clan(clan_id=1, tag="#ERR", clan_type=ClanType.MAIN)
    healthy_clan = _make_clan(clan_id=2, tag="#MAIN", clan_type=ClanType.MAIN)
    clash_error = ClashForbiddenError(
        "Clash API returned HTTP 403 for GET clans/%23ERR/capitalraidseasons.",
        endpoint="clans/%23ERR/capitalraidseasons",
        method="GET",
        status_code=403,
        response_snippet='{"reason":"accessDenied"}',
    )
    repository = InMemorySyncRaidsRepository([broken_clan, healthy_clan])
    clash_provider = FakeClashRaidSeasonsProvider(
        {
            "#ERR": clash_error,
            "#MAIN": [_make_raid_season(members=[])],
        }
    )
    job = SyncRaidsJob(repository=repository, clash_client=clash_provider)

    result = await job.run(_build_context())

    assert result.discovered_count == 2
    assert result.synced_count == 1
    assert result.failed_count == 1
    assert len(repository.raid_seasons) == 1

    assert len(repository.api_errors) == 1
    api_error = repository.api_errors[0]
    assert api_error.endpoint == "clans/%23ERR/capitalraidseasons"
    assert api_error.method == "GET"
    assert api_error.entity_type == "capital_raid_seasons"
    assert api_error.entity_tag == "#ERR"
    assert api_error.status_code == 403
    assert api_error.response_snippet == '{"reason":"accessDenied"}'
    assert api_error.exception_class == "ClashForbiddenError"
    assert api_error.worker_name == SYNC_RAIDS_JOB_NAME
    assert api_error.retry_count == 0
    assert api_error.status == "unresolved"


def test_default_worker_registry_registers_sync_raids_job() -> None:
    """Проверяет, что default worker registry подключает sync raids job."""
    registry = create_default_worker_registry(default_interval_seconds=900)

    job = registry.get(SYNC_RAIDS_JOB_NAME)

    assert job.name == SYNC_RAIDS_JOB_NAME
    assert job.run_in_transaction is True
    assert job.run_on_start is True
    assert registry.resolve_interval_seconds(job) == 900


def _make_clan(
    *,
    clan_id: int,
    tag: str,
    clan_type: ClanType,
    is_active: bool = True,
) -> Clan:
    """Создаёт клан для unit-тестов.

    Args:
        clan_id: DB id клана.
        tag: Нормализованный тег клана.
        clan_type: Тип клана.
        is_active: Флаг активного мониторинга.

    Returns:
        Модель клана.
    """
    return Clan(
        id=clan_id,
        tag=tag,
        name="Bestiary",
        type=clan_type.value,
        is_active=is_active,
    )


def _make_raid_season(
    *,
    state: str = "ended",
    total_attacks: int = 0,
    members: list[ClashRaidMember],
) -> ClashCapitalRaidSeason:
    """Создаёт DTO рейдового сезона.

    Args:
        state: Состояние рейдового сезона.
        total_attacks: Общее количество атак.
        members: Участники рейда.

    Returns:
        DTO рейдового сезона.
    """
    return ClashCapitalRaidSeason(
        state=state,
        start_time="20260517T070000.000Z",
        end_time="20260520T070000.000Z",
        capital_total_loot=123456,
        raids_completed=12,
        total_attacks=total_attacks,
        enemy_districts_destroyed=44,
        offensive_reward=1200,
        defensive_reward=500,
        members=tuple(members),
    )


def _make_raid_member(
    *,
    player_tag: str,
    attacks: int | None,
    name: str = "Bangkok",
) -> ClashRaidMember:
    """Создаёт DTO участника рейда.

    Args:
        player_tag: Тег игрока.
        attacks: Количество атак или `None`.
        name: Ник игрока.

    Returns:
        DTO участника рейда.
    """
    return ClashRaidMember(
        player_tag=player_tag,
        name=name,
        attacks=attacks,
        attack_limit=5,
        bonus_attack_limit=1,
        capital_resources_looted=30000,
    )


def _build_context() -> WorkerJobContext:
    """Создаёт runtime-контекст для unit-тестов job.

    Returns:
        Контекст worker job без DB session.
    """
    return WorkerJobContext(
        job_name=SYNC_RAIDS_JOB_NAME,
        started_at=datetime(2026, 5, 20, tzinfo=UTC),
        stop_event=asyncio.Event(),
        session=None,
    )
