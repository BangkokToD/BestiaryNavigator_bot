"""Тесты worker job синхронизации текущих обычных войн."""

import asyncio
from datetime import UTC, datetime

from app.db.models import ApiError, Clan, WarAttack, WarMember, WarSnapshot
from app.domain import ClanType, build_war_event_key
from app.integrations.clash import ClashCurrentWar, ClashForbiddenError
from app.worker.jobs import SYNC_CURRENT_WARS_JOB_NAME, SyncCurrentWarsJob
from app.worker.scheduler import WorkerJobContext, create_default_worker_registry


class InMemorySyncCurrentWarsRepository:
    """In-memory repository для unit-тестов sync current wars job."""

    def __init__(self, clans: list[Clan]) -> None:
        """Инициализирует repository.

        Args:
            clans: Набор кланов для тестового запуска.
        """
        self.clans = clans
        self.war_snapshots: list[WarSnapshot] = []
        self.war_members: list[WarMember] = []
        self.war_attacks: list[WarAttack] = []
        self.api_errors: list[ApiError] = []
        self.flush_count = 0
        self._next_war_snapshot_id = 1

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает кланы для тестового запуска.

        Returns:
            Tuple кланов.
        """
        return tuple(self.clans)

    async def get_war_snapshot_by_event_key(self, war_event_key: str) -> WarSnapshot | None:
        """Возвращает snapshot войны по event key.

        Args:
            war_event_key: Стабильный key войны.

        Returns:
            Snapshot войны или `None`.
        """
        for war_snapshot in self.war_snapshots:
            if war_snapshot.war_event_key == war_event_key:
                return war_snapshot

        return None

    def add_war_snapshot(self, war_snapshot: WarSnapshot) -> None:
        """Добавляет snapshot войны в память.

        Args:
            war_snapshot: Новый snapshot войны.
        """
        war_snapshot.id = self._next_war_snapshot_id
        self._next_war_snapshot_id += 1
        self.war_snapshots.append(war_snapshot)

    async def replace_war_children(
        self,
        *,
        war_snapshot: WarSnapshot,
        members: tuple[WarMember, ...],
        attacks: tuple[WarAttack, ...],
    ) -> None:
        """Заменяет дочерние записи войны в памяти.

        Args:
            war_snapshot: Snapshot войны.
            members: Актуальные участники войны.
            attacks: Актуальные атаки войны.
        """
        war_snapshot_id = war_snapshot.id

        self.war_members = [
            member for member in self.war_members if member.war_snapshot_id != war_snapshot_id
        ]
        self.war_attacks = [
            attack for attack in self.war_attacks if attack.war_snapshot_id != war_snapshot_id
        ]

        for member in members:
            member.war_snapshot_id = war_snapshot_id
            member.war_snapshot = war_snapshot
            self.war_members.append(member)

        for attack in attacks:
            attack.war_snapshot_id = war_snapshot_id
            attack.war_snapshot = war_snapshot
            self.war_attacks.append(attack)

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в память.

        Args:
            api_error: Модель ошибки внешнего API.
        """
        self.api_errors.append(api_error)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


class FakeClashCurrentWarProvider:
    """Fake Clash API provider для sync current wars job."""

    def __init__(self, results: dict[str, ClashCurrentWar | None | BaseException]) -> None:
        """Инициализирует provider.

        Args:
            results: Mapping `clan_tag -> current war, None или exception`.
        """
        self.results = results
        self.calls: list[str] = []

    async def get_current_war(self, clan_tag: str) -> ClashCurrentWar | None:
        """Возвращает current war или выбрасывает настроенную ошибку.

        Args:
            clan_tag: Тег клана.

        Returns:
            DTO текущей войны или `None`.

        Raises:
            BaseException: Если fake настроен на ошибку.
        """
        self.calls.append(clan_tag)
        result = self.results[clan_tag]

        if isinstance(result, BaseException):
            raise result

        return result


async def test_sync_current_wars_job_creates_war_snapshot_members_and_attacks() -> None:
    """Проверяет создание snapshot войны, участников и атак."""
    snapshot_at = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    clan = _make_clan(clan_id=1, tag="#MAIN", is_active=True)
    current_war = _make_current_war(
        state="inWar",
        our_attacks=1,
        opponent_attacks=0,
        with_attack=True,
    )
    repository = InMemorySyncCurrentWarsRepository([clan])
    clash_provider = FakeClashCurrentWarProvider({"#MAIN": current_war})
    job = SyncCurrentWarsJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: snapshot_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 1
    assert result.synced_count == 1
    assert result.no_war_count == 0
    assert result.failed_count == 0
    assert result.created_count == 1
    assert result.updated_count == 0
    assert result.member_count == 2
    assert result.attack_count == 1
    assert clash_provider.calls == ["#MAIN"]
    assert repository.flush_count == 1

    expected_event_key = build_war_event_key(
        clan_tag="#MAIN",
        opponent_tag="#OPP",
        preparation_start_time=datetime(2026, 5, 18, 10, 0, tzinfo=UTC),
        start_time=datetime(2026, 5, 18, 22, 0, tzinfo=UTC),
        end_time=datetime(2026, 5, 19, 22, 0, tzinfo=UTC),
        team_size=5,
    )

    assert len(repository.war_snapshots) == 1
    war_snapshot = repository.war_snapshots[0]
    assert war_snapshot.war_event_key == expected_event_key
    assert war_snapshot.state == "inWar"
    assert war_snapshot.team_size == 5
    assert war_snapshot.attacks_per_member == 2
    assert war_snapshot.opponent_tag == "#OPP"
    assert war_snapshot.opponent_name == "Enemy"
    assert war_snapshot.our_stars == 3
    assert war_snapshot.opponent_stars == 0
    assert war_snapshot.our_attacks == 1
    assert war_snapshot.opponent_attacks == 0
    assert war_snapshot.snapshot_at == snapshot_at

    assert [member.side for member in repository.war_members] == ["our", "opponent"]
    our_member = repository.war_members[0]
    assert our_member.player_tag == "#PLAYER1"
    assert our_member.name == "Bangkok"
    assert our_member.town_hall_level == 16
    assert our_member.map_position == 1
    assert our_member.attacks_done == 1
    assert our_member.attacks_left == 1

    attack = repository.war_attacks[0]
    assert attack.attacker_tag == "#PLAYER1"
    assert attack.defender_tag == "#ENEMY1"
    assert attack.stars == 3
    assert attack.order == 1


async def test_sync_current_wars_job_skips_none_current_war_without_db_changes() -> None:
    """Проверяет, что отсутствие войны не создаёт snapshot."""
    clan = _make_clan(clan_id=1, tag="#MAIN", is_active=True)
    repository = InMemorySyncCurrentWarsRepository([clan])
    clash_provider = FakeClashCurrentWarProvider({"#MAIN": None})
    job = SyncCurrentWarsJob(repository=repository, clash_client=clash_provider)

    result = await job.run(_build_context())

    assert result.discovered_count == 1
    assert result.synced_count == 0
    assert result.no_war_count == 1
    assert result.created_count == 0
    assert repository.war_snapshots == []
    assert repository.war_members == []
    assert repository.war_attacks == []
    assert repository.api_errors == []
    assert repository.flush_count == 1


async def test_sync_current_wars_job_records_api_error_and_continues_next_clan() -> None:
    """Проверяет error path: ApiError пишется, следующий клан синхронизируется."""
    broken_clan = _make_clan(clan_id=1, tag="#ERR", is_active=True)
    healthy_clan = _make_clan(clan_id=2, tag="#MAIN", is_active=True)
    clash_error = ClashForbiddenError(
        "Clash API returned HTTP 403 for GET clans/%23ERR/currentwar.",
        endpoint="clans/%23ERR/currentwar",
        method="GET",
        status_code=403,
        response_snippet='{"reason":"accessDenied"}',
    )

    repository = InMemorySyncCurrentWarsRepository([broken_clan, healthy_clan])
    clash_provider = FakeClashCurrentWarProvider(
        {
            "#ERR": clash_error,
            "#MAIN": _make_current_war(),
        }
    )
    job = SyncCurrentWarsJob(repository=repository, clash_client=clash_provider)

    result = await job.run(_build_context())

    assert result.discovered_count == 2
    assert result.synced_count == 1
    assert result.failed_count == 1
    assert clash_provider.calls == ["#ERR", "#MAIN"]
    assert len(repository.war_snapshots) == 1

    assert len(repository.api_errors) == 1
    api_error = repository.api_errors[0]
    assert api_error.endpoint == "clans/%23ERR/currentwar"
    assert api_error.method == "GET"
    assert api_error.entity_type == "current_war"
    assert api_error.entity_tag == "#ERR"
    assert api_error.status_code == 403
    assert api_error.response_snippet == '{"reason":"accessDenied"}'
    assert api_error.exception_class == "ClashForbiddenError"
    assert api_error.worker_name == SYNC_CURRENT_WARS_JOB_NAME
    assert api_error.retry_count == 0
    assert api_error.status == "unresolved"


async def test_sync_current_wars_job_updates_existing_snapshot_idempotently() -> None:
    """Проверяет, что повторный запуск обновляет войну без дубля snapshot."""
    first_snapshot_at = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    second_snapshot_at = datetime(2026, 5, 18, 13, 0, tzinfo=UTC)
    clan = _make_clan(clan_id=1, tag="#MAIN", is_active=True)
    repository = InMemorySyncCurrentWarsRepository([clan])
    clash_provider = FakeClashCurrentWarProvider(
        {
            "#MAIN": _make_current_war(
                state="inWar",
                our_attacks=0,
                opponent_attacks=0,
                with_attack=False,
            )
        }
    )
    first_job = SyncCurrentWarsJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: first_snapshot_at,
    )

    first_result = await first_job.run(_build_context())

    clash_provider.results["#MAIN"] = _make_current_war(
        state="inWar",
        our_attacks=1,
        opponent_attacks=0,
        with_attack=True,
    )
    second_job = SyncCurrentWarsJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: second_snapshot_at,
    )
    second_result = await second_job.run(_build_context())

    assert first_result.created_count == 1
    assert first_result.updated_count == 0
    assert first_result.attack_count == 0
    assert second_result.created_count == 0
    assert second_result.updated_count == 1
    assert second_result.attack_count == 1
    assert len(repository.war_snapshots) == 1
    assert len(repository.war_members) == 2
    assert len(repository.war_attacks) == 1

    war_snapshot = repository.war_snapshots[0]
    assert war_snapshot.our_attacks == 1
    assert war_snapshot.snapshot_at == second_snapshot_at

    our_member = next(member for member in repository.war_members if member.side == "our")
    assert our_member.attacks_done == 1
    assert our_member.attacks_left == 1


def test_default_worker_registry_registers_sync_current_wars_job() -> None:
    """Проверяет, что default worker registry подключает sync current wars job."""
    registry = create_default_worker_registry(default_interval_seconds=900)

    job = registry.get(SYNC_CURRENT_WARS_JOB_NAME)

    assert job.name == SYNC_CURRENT_WARS_JOB_NAME
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


def _make_current_war(
    *,
    state: str = "inWar",
    our_attacks: int = 0,
    opponent_attacks: int = 0,
    with_attack: bool = False,
) -> ClashCurrentWar:
    """Создаёт DTO текущей войны.

    Args:
        state: Состояние войны.
        our_attacks: Количество атак нашего клана.
        opponent_attacks: Количество атак соперника.
        with_attack: Добавить ли одну атаку нашего игрока.

    Returns:
        DTO текущей войны.
    """
    our_member_attacks = []
    if with_attack:
        our_member_attacks.append(
            {
                "order": 1,
                "attackerTag": "#PLAYER1",
                "defenderTag": "#ENEMY1",
                "stars": 3,
                "destructionPercentage": 100,
                "duration": 120,
            }
        )

    return ClashCurrentWar.from_payload(
        {
            "state": state,
            "teamSize": 5,
            "attacksPerMember": 2,
            "preparationStartTime": "20260518T100000.000Z",
            "startTime": "20260518T220000.000Z",
            "endTime": "20260519T220000.000Z",
            "clan": {
                "tag": "#MAIN",
                "name": "Bestiary",
                "stars": 3 if with_attack else 0,
                "destructionPercentage": 100 if with_attack else 0,
                "attacks": our_attacks,
                "members": [
                    {
                        "tag": "#PLAYER1",
                        "name": "Bangkok",
                        "townhallLevel": 16,
                        "mapPosition": 1,
                        "attacks": our_member_attacks,
                    }
                ],
            },
            "opponent": {
                "tag": "#OPP",
                "name": "Enemy",
                "stars": 0,
                "destructionPercentage": 0,
                "attacks": opponent_attacks,
                "members": [
                    {
                        "tag": "#ENEMY1",
                        "name": "Enemy One",
                        "townhallLevel": 16,
                        "mapPosition": 1,
                        "attacks": [],
                    }
                ],
            },
        }
    )


def _build_context() -> WorkerJobContext:
    """Создаёт runtime-контекст для unit-тестов job.

    Returns:
        Контекст worker job без DB session.
    """
    return WorkerJobContext(
        job_name=SYNC_CURRENT_WARS_JOB_NAME,
        started_at=datetime(2026, 5, 18, tzinfo=UTC),
        stop_event=asyncio.Event(),
        session=None,
    )
