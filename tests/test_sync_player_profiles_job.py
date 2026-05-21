"""Тесты worker job синхронизации snapshot-профилей игроков."""

import asyncio
from datetime import UTC, datetime

from app.db.models import ApiError, PlayerProfileSnapshot
from app.integrations.clash import ClashForbiddenError
from app.worker.jobs import SYNC_PLAYER_PROFILES_JOB_NAME, SyncPlayerProfilesJob
from app.worker.scheduler import WorkerJobContext, create_default_worker_registry


class InMemorySyncPlayerProfilesRepository:
    """In-memory repository для unit-тестов sync player profiles job."""

    def __init__(
        self,
        *,
        player_tags: list[str],
        snapshots: list[PlayerProfileSnapshot] | None = None,
    ) -> None:
        """Инициализирует repository.

        Args:
            player_tags: Теги игроков из linked accounts и current members.
            snapshots: Уже сохранённые profile snapshots.
        """
        self.player_tags = player_tags
        self.snapshots = snapshots or []
        self.added_snapshots: list[PlayerProfileSnapshot] = []
        self.api_errors: list[ApiError] = []
        self.flush_count = 0

    async def list_profile_player_tags(self) -> tuple[str, ...]:
        """Возвращает уникальные теги игроков.

        Returns:
            Tuple тегов игроков.
        """
        return tuple(self.player_tags)

    async def get_latest_profile_snapshot(
        self,
        player_tag: str,
    ) -> PlayerProfileSnapshot | None:
        """Возвращает последний snapshot профиля игрока.

        Args:
            player_tag: Тег игрока.

        Returns:
            Последний snapshot или `None`.
        """
        snapshots = [snapshot for snapshot in self.snapshots if snapshot.player_tag == player_tag]
        if not snapshots:
            return None

        return max(snapshots, key=lambda snapshot: snapshot.snapshot_at)

    def add_profile_snapshot(self, snapshot: PlayerProfileSnapshot) -> None:
        """Добавляет snapshot в in-memory storage.

        Args:
            snapshot: Новый snapshot профиля.
        """
        self.added_snapshots.append(snapshot)
        self.snapshots.append(snapshot)

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в in-memory storage.

        Args:
            api_error: Модель ошибки внешнего API.
        """
        self.api_errors.append(api_error)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


class FakeClashPlayerProfileProvider:
    """Fake Clash API provider для sync player profiles job."""

    def __init__(self, results: dict[str, dict[str, object] | BaseException]) -> None:
        """Инициализирует provider.

        Args:
            results: Mapping `player_tag -> profile payload или exception`.
        """
        self.results = results
        self.calls: list[str] = []

    async def get_player(self, player_tag: str) -> dict[str, object]:
        """Возвращает профиль игрока или выбрасывает настроенную ошибку.

        Args:
            player_tag: Тег игрока.

        Returns:
            JSON object профиля игрока.

        Raises:
            BaseException: Если fake настроен на ошибку.
        """
        self.calls.append(player_tag)
        result = self.results[player_tag]

        if isinstance(result, BaseException):
            raise result

        return result


async def test_sync_player_profiles_job_creates_snapshots_for_linked_and_current_players() -> None:
    """Проверяет создание snapshots по linked accounts и current members."""
    snapshot_at = datetime(2026, 1, 2, tzinfo=UTC)
    repository = InMemorySyncPlayerProfilesRepository(
        player_tags=["#LINKED", "#CURRENT"],
    )
    clash_provider = FakeClashPlayerProfileProvider(
        {
            "#LINKED": _make_profile_payload(player_tag="#LINKED", name="Linked"),
            "#CURRENT": _make_profile_payload(player_tag="#CURRENT", name="Current"),
        }
    )
    job = SyncPlayerProfilesJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: snapshot_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 2
    assert result.synced_count == 2
    assert result.failed_count == 0
    assert result.created_snapshot_count == 2
    assert result.skipped_unchanged_count == 0
    assert clash_provider.calls == ["#LINKED", "#CURRENT"]
    assert repository.flush_count == 1
    assert len(repository.added_snapshots) == 2

    linked_snapshot = repository.added_snapshots[0]
    assert linked_snapshot.player_tag == "#LINKED"
    assert linked_snapshot.name == "Linked"
    assert linked_snapshot.town_hall_level == 16
    assert linked_snapshot.town_hall_weapon_level == 5
    assert linked_snapshot.exp_level == 233
    assert linked_snapshot.trophies == 5200
    assert linked_snapshot.best_trophies == 5600
    assert linked_snapshot.war_stars == 1500
    assert linked_snapshot.donations == 1000
    assert linked_snapshot.donations_received == 700
    assert linked_snapshot.clan_capital_contributions == 12345
    assert linked_snapshot.snapshot_at == snapshot_at

    assert linked_snapshot.heroes_json[0]["village"] == "HOME_VILLAGE"
    assert linked_snapshot.heroes_json[1]["village"] == "BUILDER_BASE"
    assert linked_snapshot.troops_json[0]["village"] == "HOME_VILLAGE"
    assert linked_snapshot.spells_json[0]["village"] == "HOME_VILLAGE"
    assert linked_snapshot.achievements_json[0]["village"] == "HOME_VILLAGE"


async def test_sync_player_profiles_job_records_api_error_and_continues_next_profile() -> None:
    """Проверяет error path: ApiError пишется, следующий профиль синхронизируется."""
    snapshot_at = datetime(2026, 1, 2, tzinfo=UTC)
    clash_error = ClashForbiddenError(
        "Clash API returned HTTP 403 for GET players/%23ERR.",
        endpoint="players/%23ERR",
        method="GET",
        status_code=403,
        response_snippet='{"reason":"accessDenied"}',
    )
    repository = InMemorySyncPlayerProfilesRepository(
        player_tags=["#ERR", "#OK"],
    )
    clash_provider = FakeClashPlayerProfileProvider(
        {
            "#ERR": clash_error,
            "#OK": _make_profile_payload(player_tag="#OK", name="Healthy"),
        }
    )
    job = SyncPlayerProfilesJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: snapshot_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 2
    assert result.synced_count == 1
    assert result.failed_count == 1
    assert result.created_snapshot_count == 1
    assert result.skipped_unchanged_count == 0
    assert clash_provider.calls == ["#ERR", "#OK"]
    assert len(repository.added_snapshots) == 1
    assert repository.added_snapshots[0].player_tag == "#OK"

    assert len(repository.api_errors) == 1
    api_error = repository.api_errors[0]
    assert api_error.endpoint == "players/%23ERR"
    assert api_error.method == "GET"
    assert api_error.entity_type == "player_profile"
    assert api_error.entity_tag == "#ERR"
    assert api_error.status_code == 403
    assert api_error.response_snippet == '{"reason":"accessDenied"}'
    assert api_error.exception_class == "ClashForbiddenError"
    assert api_error.worker_name == SYNC_PLAYER_PROFILES_JOB_NAME
    assert api_error.retry_count == 0
    assert api_error.status == "unresolved"
    assert repository.flush_count == 1


async def test_sync_player_profiles_job_skips_unchanged_profile_on_repeated_run() -> None:
    """Проверяет, что неизменённый профиль не создаёт повторный snapshot."""
    first_snapshot_at = datetime(2026, 1, 1, tzinfo=UTC)
    second_snapshot_at = datetime(2026, 1, 2, tzinfo=UTC)
    payload = _make_profile_payload(player_tag="#2ABC", name="Bangkok")
    repository = InMemorySyncPlayerProfilesRepository(player_tags=["#2ABC"])
    clash_provider = FakeClashPlayerProfileProvider({"#2ABC": payload})
    job = SyncPlayerProfilesJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: first_snapshot_at,
    )

    first_result = await job.run(_build_context())

    second_job = SyncPlayerProfilesJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: second_snapshot_at,
    )
    second_result = await second_job.run(_build_context())

    assert first_result.created_snapshot_count == 1
    assert first_result.skipped_unchanged_count == 0
    assert second_result.created_snapshot_count == 0
    assert second_result.skipped_unchanged_count == 1
    assert len(repository.snapshots) == 1
    assert repository.snapshots[0].snapshot_at == first_snapshot_at
    assert repository.flush_count == 2


async def test_sync_player_profiles_job_creates_snapshot_when_profile_changed() -> None:
    """Проверяет создание нового snapshot при изменении профиля."""
    first_snapshot_at = datetime(2026, 1, 1, tzinfo=UTC)
    second_snapshot_at = datetime(2026, 1, 2, tzinfo=UTC)
    repository = InMemorySyncPlayerProfilesRepository(player_tags=["#2ABC"])
    clash_provider = FakeClashPlayerProfileProvider(
        {"#2ABC": _make_profile_payload(player_tag="#2ABC", name="Bangkok")}
    )
    job = SyncPlayerProfilesJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: first_snapshot_at,
    )

    await job.run(_build_context())

    clash_provider.results["#2ABC"] = _make_profile_payload(
        player_tag="#2ABC",
        name="Bangkok",
        trophies=5300,
    )
    second_job = SyncPlayerProfilesJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: second_snapshot_at,
    )
    result = await second_job.run(_build_context())

    assert result.created_snapshot_count == 1
    assert result.skipped_unchanged_count == 0
    assert len(repository.snapshots) == 2
    assert repository.snapshots[0].trophies == 5200
    assert repository.snapshots[1].trophies == 5300
    assert repository.snapshots[1].snapshot_at == second_snapshot_at


def test_default_worker_registry_registers_sync_player_profiles_job() -> None:
    """Проверяет, что default worker registry подключает sync player profiles job."""
    registry = create_default_worker_registry(default_interval_seconds=900)

    job = registry.get(SYNC_PLAYER_PROFILES_JOB_NAME)

    assert job.name == SYNC_PLAYER_PROFILES_JOB_NAME
    assert job.run_in_transaction is True
    assert job.run_on_start is True
    assert registry.resolve_interval_seconds(job) == 900


def _make_profile_payload(
    *,
    player_tag: str,
    name: str,
    trophies: int = 5200,
) -> dict[str, object]:
    """Создаёт payload профиля игрока для unit-тестов.

    Args:
        player_tag: Тег игрока.
        name: Ник игрока.
        trophies: Текущее количество трофеев.

    Returns:
        JSON object профиля игрока.
    """
    return {
        "tag": player_tag,
        "name": name,
        "townHallLevel": 16,
        "townHallWeaponLevel": 5,
        "expLevel": 233,
        "trophies": trophies,
        "bestTrophies": 5600,
        "warStars": 1500,
        "donations": 1000,
        "donationsReceived": 700,
        "clanCapitalContributions": 12345,
        "heroes": [
            {
                "name": "Archer Queen",
                "level": 95,
                "maxLevel": 95,
                "village": "HOME_VILLAGE",
            },
            {
                "name": "Battle Machine",
                "level": 35,
                "maxLevel": 35,
                "village": "BUILDER_BASE",
            },
        ],
        "troops": [
            {
                "name": "Electro Dragon",
                "level": 7,
                "maxLevel": 7,
                "village": "HOME_VILLAGE",
            }
        ],
        "spells": [
            {
                "name": "Lightning Spell",
                "level": 11,
                "maxLevel": 11,
                "village": "HOME_VILLAGE",
            }
        ],
        "achievements": [
            {
                "name": "War Hero",
                "stars": 3,
                "value": 1500,
                "target": 1000,
                "village": "HOME_VILLAGE",
            }
        ],
        "labels": [{"id": 1, "name": "Clan Wars"}],
    }


def _build_context() -> WorkerJobContext:
    """Создаёт runtime-контекст для unit-тестов job.

    Returns:
        Контекст worker job без DB session.
    """
    return WorkerJobContext(
        job_name=SYNC_PLAYER_PROFILES_JOB_NAME,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        stop_event=asyncio.Event(),
        session=None,
    )
