"""Тесты сервиса жизненного цикла участников клана."""

from datetime import UTC, datetime

import pytest

from app.db.models import Clan, ClanMemberSnapshot, PlayerAccount, PlayerEvent
from app.domain import ClanType
from app.integrations.clash import ClashClanMember
from app.services import MemberLifecycleError, MemberLifecycleService


class InMemoryMemberLifecycleRepository:
    """In-memory repository для unit-тестов MemberLifecycleService."""

    def __init__(
        self,
        *,
        snapshots: list[ClanMemberSnapshot] | None = None,
        accounts: list[PlayerAccount] | None = None,
    ) -> None:
        """Инициализирует repository.

        Args:
            snapshots: Начальный набор snapshot-записей состава.
            accounts: Начальный набор игровых аккаунтов.
        """
        self.snapshots = snapshots or []
        self.accounts = {account.player_tag: account for account in accounts or []}
        self.added_snapshots: list[ClanMemberSnapshot] = []
        self.events: list[PlayerEvent] = []
        self.flush_count = 0

    async def list_current_by_clan(self, clan_id: int) -> list[ClanMemberSnapshot]:
        """Возвращает текущие snapshot-записи клана."""
        return [
            snapshot
            for snapshot in self.snapshots
            if snapshot.clan_id == clan_id and snapshot.is_current
        ]

    async def get_current_by_clan_and_player_tag(
        self,
        *,
        clan_id: int,
        player_tag: str,
    ) -> ClanMemberSnapshot | None:
        """Возвращает текущий snapshot игрока в клане."""
        for snapshot in self.snapshots:
            if (
                snapshot.clan_id == clan_id
                and snapshot.player_tag == player_tag
                and snapshot.is_current
            ):
                return snapshot

        return None

    async def close_current_snapshots_in_other_clans(
        self,
        *,
        player_tag: str,
        clan_id: int,
        observed_at: datetime,
    ) -> int:
        """Закрывает текущие snapshot-записи игрока в других кланах."""
        closed_count = 0

        for snapshot in self.snapshots:
            if (
                snapshot.player_tag == player_tag
                and snapshot.clan_id != clan_id
                and snapshot.is_current
            ):
                snapshot.is_current = False
                snapshot.last_seen_at = observed_at
                snapshot.snapshot_at = observed_at
                closed_count += 1

        return closed_count

    async def get_player_account_by_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает игровой аккаунт по тегу."""
        return self.accounts.get(player_tag)

    def add_player_event(self, player_event: PlayerEvent) -> None:
        """Добавляет событие игрока в in-memory storage."""
        self.events.append(player_event)

    def add_member_snapshot(self, snapshot: ClanMemberSnapshot) -> None:
        """Добавляет snapshot в in-memory storage."""
        self.added_snapshots.append(snapshot)
        self.snapshots.append(snapshot)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


def make_clan(
    *,
    clan_id: int = 1,
    clan_tag: str = "#MAIN",
    clan_type: ClanType = ClanType.MAIN,
) -> Clan:
    """Создаёт клан для unit-тестов."""
    return Clan(
        id=clan_id,
        tag=clan_tag,
        name="Bestiary",
        type=clan_type.value,
        is_active=True,
    )


def make_member(
    *,
    player_tag: str = "#2ABC",
    name: str = "Bangkok",
    role: str | None = "leader",
    town_hall_level: int | None = 16,
) -> ClashClanMember:
    """Создаёт DTO участника клана для unit-тестов."""
    return ClashClanMember(
        player_tag=player_tag,
        name=name,
        role=role,
        town_hall_level=town_hall_level,
        exp_level=233,
        trophies=5200,
        donations=1000,
        donations_received=700,
    )


def make_snapshot(
    *,
    clan_id: int = 1,
    player_tag: str = "#2ABC",
    name: str = "Bangkok",
    is_current: bool = True,
    first_seen_at: datetime | None = None,
) -> ClanMemberSnapshot:
    """Создаёт snapshot участника для unit-тестов."""
    observed_at = first_seen_at or datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    return ClanMemberSnapshot(
        clan_id=clan_id,
        player_tag=player_tag,
        name=name,
        role="member",
        town_hall_level=15,
        exp_level=200,
        trophies=4800,
        donations=500,
        donations_received=400,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        snapshot_at=observed_at,
        is_current=is_current,
    )


def make_account(
    *,
    player_tag: str = "#2ABC",
    last_seen_clan_id: int | None = None,
) -> PlayerAccount:
    """Создаёт PlayerAccount для unit-тестов."""
    return PlayerAccount(
        telegram_user_id=101,
        player_tag=player_tag,
        name="Bangkok",
        is_active=True,
        last_seen_clan_id=last_seen_clan_id,
    )


@pytest.mark.asyncio
async def test_member_lifecycle_service_creates_snapshot_for_new_player() -> None:
    """Проверяет создание snapshot для нового игрока."""
    clan = make_clan()
    repository = InMemoryMemberLifecycleRepository()
    service = MemberLifecycleService(repository=repository)

    result = await service.process_clan_members(clan=clan, members=[make_member()])

    assert result.created_count == 1
    assert result.updated_count == 0
    assert result.left_count == 0
    assert result.moved_count == 0
    assert result.current_count == 1
    assert repository.flush_count == 1
    assert len(repository.added_snapshots) == 1

    snapshot = repository.added_snapshots[0]
    assert snapshot.clan_id == 1
    assert snapshot.clan is clan
    assert snapshot.player_tag == "#2ABC"
    assert snapshot.name == "Bangkok"
    assert snapshot.role == "leader"
    assert snapshot.town_hall_level == 16
    assert snapshot.exp_level == 233
    assert snapshot.trophies == 5200
    assert snapshot.donations == 1000
    assert snapshot.donations_received == 700
    assert snapshot.first_seen_at is not None
    assert snapshot.last_seen_at is not None
    assert snapshot.snapshot_at is not None
    assert snapshot.is_current is True

    assert len(repository.events) == 1
    event = repository.events[0]
    assert event.event_type == "clan_member_joined"
    assert event.player_tag == "#2ABC"
    assert event.title == "Игрок появился в клане"
    assert event.metadata_json["clan_tag"] == "#MAIN"


@pytest.mark.asyncio
async def test_member_lifecycle_service_marks_missing_player_as_not_current() -> None:
    """Проверяет закрытие snapshot игрока, пропавшего из состава."""
    clan = make_clan()
    snapshot = make_snapshot()
    repository = InMemoryMemberLifecycleRepository(snapshots=[snapshot])
    service = MemberLifecycleService(repository=repository)

    result = await service.process_clan_members(clan=clan, members=[])

    assert result.created_count == 0
    assert result.updated_count == 0
    assert result.left_count == 1
    assert result.moved_count == 0
    assert result.current_count == 0
    assert snapshot.is_current is False
    assert repository.flush_count == 1
    assert len(repository.events) == 1

    event = repository.events[0]
    assert event.event_type == "clan_member_left"
    assert event.player_tag == "#2ABC"
    assert event.title == "Игрок вышел из клана"
    assert event.metadata_json["clan_tag"] == "#MAIN"


@pytest.mark.asyncio
async def test_member_lifecycle_service_updates_current_snapshot_in_place_on_rename() -> None:
    """Проверяет обновление ника без потери first_seen_at."""
    clan = make_clan()
    first_seen_at = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    snapshot = make_snapshot(name="Old nickname", first_seen_at=first_seen_at)
    repository = InMemoryMemberLifecycleRepository(snapshots=[snapshot])
    service = MemberLifecycleService(repository=repository)

    result = await service.process_clan_members(
        clan=clan,
        members=[make_member(name="New nickname")],
    )

    assert result.created_count == 0
    assert result.updated_count == 1
    assert result.left_count == 0
    assert snapshot.name == "New nickname"
    assert snapshot.first_seen_at == first_seen_at
    assert snapshot.last_seen_at != first_seen_at
    assert snapshot.snapshot_at != first_seen_at
    assert snapshot.is_current is True
    assert repository.added_snapshots == []
    assert len(repository.events) == 1

    event = repository.events[0]
    assert event.event_type == "clan_member_renamed"
    assert event.player_tag == "#2ABC"
    assert event.title == "Игрок сменил ник"
    assert event.metadata_json["previous_name"] == "Old nickname"
    assert event.metadata_json["player_name"] == "New nickname"


@pytest.mark.asyncio
@pytest.mark.parametrize("target_clan_type", [ClanType.ACADEMY, ClanType.FREEZER])
async def test_member_lifecycle_service_closes_old_clan_snapshot_on_transition(
    target_clan_type: ClanType,
) -> None:
    """Проверяет переход main -> academy/freezer и обновление last_seen_clan_id."""
    old_snapshot = make_snapshot(clan_id=1, player_tag="#2ABC")
    account = make_account(player_tag="#2ABC", last_seen_clan_id=1)
    target_clan = make_clan(clan_id=2, clan_tag="#TARGET", clan_type=target_clan_type)
    repository = InMemoryMemberLifecycleRepository(
        snapshots=[old_snapshot],
        accounts=[account],
    )
    service = MemberLifecycleService(repository=repository)

    result = await service.process_clan_members(
        clan=target_clan,
        members=[make_member(player_tag="2abc")],
    )

    assert result.created_count == 1
    assert result.updated_count == 0
    assert result.left_count == 0
    assert result.moved_count == 1
    assert old_snapshot.is_current is False

    new_snapshot = repository.added_snapshots[0]
    assert new_snapshot.clan_id == 2
    assert new_snapshot.clan is target_clan
    assert new_snapshot.is_current is True
    assert account.last_seen_clan_id == 2
    assert account.last_seen_clan is target_clan
    assert [event.event_type for event in repository.events] == [
        "clan_member_moved",
        "clan_member_joined",
    ]


@pytest.mark.asyncio
async def test_member_lifecycle_service_updates_player_account_last_seen_clan() -> None:
    """Проверяет обновление last_seen_clan_id у существующего PlayerAccount."""
    clan = make_clan(clan_id=7)
    account = make_account(player_tag="#2ABC", last_seen_clan_id=None)
    repository = InMemoryMemberLifecycleRepository(accounts=[account])
    service = MemberLifecycleService(repository=repository)

    result = await service.process_clan_members(clan=clan, members=[make_member(player_tag="2abc")])

    assert result.created_count == 1
    assert account.last_seen_clan_id == 7
    assert account.last_seen_clan is clan


@pytest.mark.asyncio
async def test_member_lifecycle_service_repeated_run_updates_without_duplicate() -> None:
    """Проверяет повторный запуск без дубля current snapshot и событий входа."""
    clan = make_clan()
    repository = InMemoryMemberLifecycleRepository()
    service = MemberLifecycleService(repository=repository)

    first_result = await service.process_clan_members(clan=clan, members=[make_member()])
    second_result = await service.process_clan_members(clan=clan, members=[make_member()])

    assert first_result.created_count == 1
    assert first_result.updated_count == 0
    assert second_result.created_count == 0
    assert second_result.updated_count == 1
    assert len(repository.added_snapshots) == 1
    assert repository.added_snapshots[0].is_current is True
    assert [event.event_type for event in repository.events] == ["clan_member_joined"]
    assert repository.flush_count == 2


@pytest.mark.asyncio
async def test_member_lifecycle_service_rejects_unsaved_clan() -> None:
    """Проверяет запрет обработки состава для несохранённого клана."""
    clan = Clan(
        tag="#MAIN",
        name="Bestiary",
        type=ClanType.MAIN.value,
        is_active=True,
    )
    repository = InMemoryMemberLifecycleRepository()
    service = MemberLifecycleService(repository=repository)

    with pytest.raises(MemberLifecycleError):
        await service.process_clan_members(clan=clan, members=[make_member()])

    assert repository.flush_count == 0
