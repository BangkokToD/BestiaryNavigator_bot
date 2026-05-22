"""Тесты сервиса проверки прав `/warn`."""

from datetime import UTC, datetime

import pytest

from app.db.models import Clan, ClanMemberSnapshot, PlayerAccount, TelegramUser
from app.domain import ClanType
from app.integrations.clash import ClashClanMember
from app.services import (
    WarningPermissionService,
    WarnPermissionStatus,
)

_NOW = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
_OLD = datetime(2026, 5, 22, 10, 0, tzinfo=UTC)


class InMemoryWarningPermissionRepository:
    """In-memory repository проверки прав `/warn`."""

    def __init__(
        self,
        *,
        accounts: list[PlayerAccount] | None = None,
        snapshots: list[ClanMemberSnapshot] | None = None,
        clans: list[Clan] | None = None,
    ) -> None:
        """Инициализирует repository.

        Args:
            accounts: Игровые аккаунты.
            snapshots: Snapshot-записи состава.
            clans: Кланы.
        """
        self.accounts = accounts or []
        self.snapshots = snapshots or []
        self.clans = {clan.id: clan for clan in clans or []}

    async def list_active_accounts_by_telegram_user_id(
        self,
        telegram_user_id: int,
    ) -> tuple[PlayerAccount, ...]:
        """Возвращает активные аккаунты TelegramUser."""
        return tuple(
            account
            for account in self.accounts
            if account.telegram_user_id == telegram_user_id
            and account.is_active
            and account.unlinked_at is None
        )

    async def list_current_main_snapshots_by_player_tags(
        self,
        player_tags: tuple[str, ...],
    ) -> tuple[ClanMemberSnapshot, ...]:
        """Возвращает current snapshots в active main-кланах."""
        tags = set(player_tags)
        result: list[ClanMemberSnapshot] = []

        for snapshot in self.snapshots:
            clan = snapshot.clan or self.clans.get(snapshot.clan_id)
            if clan is not None:
                snapshot.clan = clan

            if (
                snapshot.player_tag in tags
                and snapshot.is_current
                and clan is not None
                and clan.type == ClanType.MAIN.value
                and clan.is_active
            ):
                result.append(snapshot)

        return tuple(result)


class FakeClashMembersProvider:
    """Fake Clash provider для проверки refresh состава."""

    def __init__(
        self,
        *,
        members_by_clan_tag: dict[str, list[ClashClanMember]] | None = None,
        error: Exception | None = None,
    ) -> None:
        """Инициализирует fake provider.

        Args:
            members_by_clan_tag: Участники по тегу клана.
            error: Ошибка refresh.
        """
        self.members_by_clan_tag = members_by_clan_tag or {}
        self.error = error
        self.calls: list[str] = []

    async def get_clan_members(self, clan_tag: str) -> list[ClashClanMember]:
        """Возвращает участников клана."""
        self.calls.append(clan_tag)
        if self.error is not None:
            raise self.error

        return self.members_by_clan_tag[clan_tag]


class FakeMemberLifecycleService:
    """Fake member lifecycle service для обновления snapshots в памяти."""

    def __init__(
        self,
        *,
        repository: InMemoryWarningPermissionRepository,
        observed_at: datetime = _NOW,
    ) -> None:
        """Инициализирует fake service.

        Args:
            repository: In-memory repository.
            observed_at: Время обновления snapshot.
        """
        self.repository = repository
        self.observed_at = observed_at
        self.calls: list[str] = []

    async def process_clan_members(
        self,
        *,
        clan: Clan,
        members: list[ClashClanMember],
    ) -> object:
        """Обновляет snapshots по входящему составу."""
        self.calls.append(clan.tag)
        incoming_by_tag = {member.player_tag: member for member in members}

        for snapshot in self.repository.snapshots:
            if snapshot.clan_id != clan.id:
                continue

            member = incoming_by_tag.get(snapshot.player_tag)
            if member is None:
                snapshot.is_current = False
                snapshot.snapshot_at = self.observed_at
                snapshot.last_seen_at = self.observed_at
                continue

            snapshot.name = member.name
            snapshot.role = member.role
            snapshot.snapshot_at = self.observed_at
            snapshot.last_seen_at = self.observed_at
            snapshot.is_current = True

        existing_tags = {
            snapshot.player_tag
            for snapshot in self.repository.snapshots
            if snapshot.clan_id == clan.id
        }
        for member in members:
            if member.player_tag in existing_tags:
                continue

            snapshot = _make_snapshot(
                clan=clan,
                player_tag=member.player_tag,
                name=member.name,
                role=member.role,
                snapshot_at=self.observed_at,
            )
            self.repository.snapshots.append(snapshot)

        return object()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["leader", "coLeader", "elder"])
async def test_warn_permission_allows_officer_roles_in_main(role: str) -> None:
    """Проверяет роли, которым доступен `/warn`."""
    service, _, _ = _make_service(
        accounts=[_make_account()],
        snapshots=[_make_snapshot(role=role)],
        clans=[_make_clan()],
    )

    result = await service.check_initiator(telegram_user=_make_user())

    assert result.allowed is True
    assert result.status == WarnPermissionStatus.ALLOWED
    assert result.account is not None
    assert result.account.role == role


@pytest.mark.asyncio
async def test_warn_permission_rejects_member_role() -> None:
    """Проверяет отказ обычному member."""
    service, _, _ = _make_service(
        accounts=[_make_account()],
        snapshots=[_make_snapshot(role="member")],
        clans=[_make_clan()],
    )

    result = await service.check_initiator(telegram_user=_make_user())

    assert result.allowed is False
    assert result.status == WarnPermissionStatus.ROLE_NOT_ALLOWED


@pytest.mark.asyncio
@pytest.mark.parametrize("clan_type", [ClanType.ACADEMY, ClanType.FREEZER])
async def test_warn_permission_rejects_academy_and_freezer(clan_type: ClanType) -> None:
    """Проверяет, что academy/freezer не дают право на `/warn`."""
    clan = _make_clan(clan_type=clan_type)
    service, _, _ = _make_service(
        accounts=[_make_account()],
        snapshots=[_make_snapshot(clan=clan, role="leader")],
        clans=[clan],
    )

    result = await service.check_initiator(telegram_user=_make_user())

    assert result.allowed is False
    assert result.status == WarnPermissionStatus.NO_LINKED_MAIN_ACCOUNT


@pytest.mark.asyncio
async def test_warn_permission_refreshes_stale_snapshot_before_allowing_role() -> None:
    """Проверяет refresh stale snapshot перед проверкой роли."""
    clan = _make_clan()
    repository = InMemoryWarningPermissionRepository(
        accounts=[_make_account()],
        snapshots=[_make_snapshot(clan=clan, role="member", snapshot_at=_OLD)],
        clans=[clan],
    )
    clash_provider = FakeClashMembersProvider(
        members_by_clan_tag={
            "#MAIN": [
                _make_member(player_tag="#2ABC", name="Bangkok", role="elder"),
            ]
        }
    )
    lifecycle = FakeMemberLifecycleService(repository=repository)
    service = WarningPermissionService(
        repository=repository,
        clash_client=clash_provider,
        member_lifecycle_service=lifecycle,
        role_snapshot_max_age_minutes=30,
        now_provider=lambda: _NOW,
    )

    result = await service.check_initiator(telegram_user=_make_user())

    assert result.allowed is True
    assert result.status == WarnPermissionStatus.ALLOWED
    assert result.refreshed_clan_tags == ("#MAIN",)
    assert clash_provider.calls == ["#MAIN"]
    assert lifecycle.calls == ["#MAIN"]


@pytest.mark.asyncio
async def test_warn_permission_rejects_if_stale_role_cannot_be_refreshed() -> None:
    """Проверяет отказ, если роль нельзя подтвердить после stale refresh."""
    service, clash_provider, _ = _make_service(
        accounts=[_make_account()],
        snapshots=[_make_snapshot(role="leader", snapshot_at=_OLD)],
        clans=[_make_clan()],
        clash_error=RuntimeError("Clash unavailable"),
    )

    result = await service.check_initiator(telegram_user=_make_user())

    assert result.allowed is False
    assert result.status == WarnPermissionStatus.ROLE_UNCONFIRMED
    assert clash_provider.calls == ["#MAIN"]


@pytest.mark.asyncio
async def test_warn_permission_allows_target_in_main() -> None:
    """Проверяет, что цель проходит при наличии linked account в main."""
    service, _, _ = _make_service(
        snapshots=[_make_snapshot(role="member")],
        clans=[_make_clan()],
    )

    result = await service.check_target(candidate=_make_candidate())

    assert result.allowed is True
    assert result.status == WarnPermissionStatus.ALLOWED


@pytest.mark.asyncio
async def test_warn_permission_rejects_target_outside_main() -> None:
    """Проверяет отказ цели вне main-клана."""
    academy = _make_clan(clan_type=ClanType.ACADEMY)
    service, _, _ = _make_service(
        snapshots=[_make_snapshot(clan=academy, role="leader")],
        clans=[academy],
    )

    result = await service.check_target(candidate=_make_candidate())

    assert result.allowed is False
    assert result.status == WarnPermissionStatus.TARGET_NOT_MAIN


def _make_service(
    *,
    accounts: list[PlayerAccount] | None = None,
    snapshots: list[ClanMemberSnapshot] | None = None,
    clans: list[Clan] | None = None,
    clash_error: Exception | None = None,
) -> tuple[WarningPermissionService, FakeClashMembersProvider, FakeMemberLifecycleService]:
    """Создаёт service и fake dependencies для тестов."""
    repository = InMemoryWarningPermissionRepository(
        accounts=accounts,
        snapshots=snapshots,
        clans=clans,
    )
    clash_provider = FakeClashMembersProvider(error=clash_error)
    lifecycle = FakeMemberLifecycleService(repository=repository)
    service = WarningPermissionService(
        repository=repository,
        clash_client=clash_provider,
        member_lifecycle_service=lifecycle,
        role_snapshot_max_age_minutes=30,
        now_provider=lambda: _NOW,
    )
    return service, clash_provider, lifecycle


def _make_user() -> TelegramUser:
    """Создаёт TelegramUser для тестов."""
    return TelegramUser(
        id=101,
        telegram_id=42,
        username="bangkok",
        display_name="Bangkok",
    )


def _make_account(
    *,
    telegram_user_id: int = 101,
    player_tag: str = "#2ABC",
    name: str = "Bangkok",
) -> PlayerAccount:
    """Создаёт PlayerAccount для тестов."""
    return PlayerAccount(
        telegram_user_id=telegram_user_id,
        player_tag=player_tag,
        name=name,
        is_active=True,
    )


def _make_clan(
    *,
    clan_id: int = 7,
    clan_type: ClanType = ClanType.MAIN,
) -> Clan:
    """Создаёт Clan для тестов."""
    return Clan(
        id=clan_id,
        tag="#MAIN",
        name="Bestiary",
        type=clan_type.value,
        is_active=True,
    )


def _make_snapshot(
    *,
    clan: Clan | None = None,
    player_tag: str = "#2ABC",
    name: str = "Bangkok",
    role: str | None = "leader",
    snapshot_at: datetime = _NOW,
) -> ClanMemberSnapshot:
    """Создаёт ClanMemberSnapshot для тестов."""
    resolved_clan = clan or _make_clan()
    return ClanMemberSnapshot(
        clan_id=resolved_clan.id,
        clan=resolved_clan,
        player_tag=player_tag,
        name=name,
        role=role,
        snapshot_at=snapshot_at,
        last_seen_at=snapshot_at,
        first_seen_at=snapshot_at,
        is_current=True,
    )


def _make_member(
    *,
    player_tag: str,
    name: str,
    role: str | None,
) -> ClashClanMember:
    """Создаёт ClashClanMember для refresh-тестов."""
    return ClashClanMember(
        player_tag=player_tag,
        name=name,
        role=role,
        town_hall_level=16,
        exp_level=233,
        trophies=5200,
        donations=1000,
        donations_received=700,
    )


def _make_candidate() -> object:
    """Создаёт candidate-подобный объект цели warn."""
    account = type(
        "CandidateAccount",
        (),
        {
            "player_tag": "#2ABC",
            "player_name": "Bangkok",
        },
    )()
    return type(
        "Candidate",
        (),
        {
            "accounts": (account,),
        },
    )()
