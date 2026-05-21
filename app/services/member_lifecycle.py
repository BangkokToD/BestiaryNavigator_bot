"""Сервис жизненного цикла участников клана."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Clan, ClanMemberSnapshot, PlayerAccount, PlayerEvent
from app.domain import normalize_player_tag
from app.integrations.clash import ClashClanMember

_MEMBER_JOINED_EVENT_TYPE = "clan_member_joined"
_MEMBER_LEFT_EVENT_TYPE = "clan_member_left"
_MEMBER_MOVED_EVENT_TYPE = "clan_member_moved"
_MEMBER_RENAMED_EVENT_TYPE = "clan_member_renamed"


class MemberLifecycleError(RuntimeError):
    """Базовая ошибка сервиса жизненного цикла участников."""


@dataclass(frozen=True, slots=True)
class MemberLifecycleResult:
    """Результат обработки состава клана."""

    clan: Clan
    created_count: int
    updated_count: int
    left_count: int
    moved_count: int
    current_count: int


class MemberLifecycleRepository(Protocol):
    """Repository contract для обработки состава клана."""

    async def list_current_by_clan(self, clan_id: int) -> list[ClanMemberSnapshot]:
        """Возвращает текущие snapshot-записи состава клана.

        Args:
            clan_id: DB ID клана.

        Returns:
            Список текущих snapshot-записей.
        """

    async def get_current_by_clan_and_player_tag(
        self,
        *,
        clan_id: int,
        player_tag: str,
    ) -> ClanMemberSnapshot | None:
        """Возвращает текущий snapshot игрока в конкретном клане.

        Args:
            clan_id: DB ID клана.
            player_tag: Нормализованный тег игрока.

        Returns:
            Snapshot или `None`.
        """

    async def close_current_snapshots_in_other_clans(
        self,
        *,
        player_tag: str,
        clan_id: int,
        observed_at: datetime,
    ) -> int:
        """Закрывает current snapshots игрока в других кланах.

        Args:
            player_tag: Нормализованный тег игрока.
            clan_id: DB ID актуального клана.
            observed_at: Время наблюдения состава.

        Returns:
            Количество закрытых snapshot-записей.
        """

    async def get_player_account_by_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает подтверждённый аккаунт по тегу.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            `PlayerAccount` или `None`.
        """

    def add_player_event(self, player_event: PlayerEvent) -> None:
        """Добавляет событие игрока в unit of work.

        Args:
            player_event: Новая модель события.
        """

    def add_member_snapshot(self, snapshot: ClanMemberSnapshot) -> None:
        """Добавляет snapshot участника в unit of work.

        Args:
            snapshot: Новый snapshot участника.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyMemberLifecycleRepository:
    """SQLAlchemy-реализация repository для жизненного цикла участников."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_current_by_clan(self, clan_id: int) -> list[ClanMemberSnapshot]:
        """Возвращает текущие snapshot-записи состава клана.

        Args:
            clan_id: DB ID клана.

        Returns:
            Список текущих snapshot-записей.
        """
        result = await self._session.execute(
            select(ClanMemberSnapshot).where(
                ClanMemberSnapshot.clan_id == clan_id,
                ClanMemberSnapshot.is_current.is_(True),
            )
        )
        return list(result.scalars().all())

    async def get_current_by_clan_and_player_tag(
        self,
        *,
        clan_id: int,
        player_tag: str,
    ) -> ClanMemberSnapshot | None:
        """Возвращает текущий snapshot игрока в клане.

        Args:
            clan_id: DB ID клана.
            player_tag: Нормализованный тег игрока.

        Returns:
            Snapshot или `None`.
        """
        result = await self._session.execute(
            select(ClanMemberSnapshot).where(
                ClanMemberSnapshot.clan_id == clan_id,
                ClanMemberSnapshot.player_tag == player_tag,
                ClanMemberSnapshot.is_current.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def close_current_snapshots_in_other_clans(
        self,
        *,
        player_tag: str,
        clan_id: int,
        observed_at: datetime,
    ) -> int:
        """Закрывает current snapshots игрока в других кланах.

        Args:
            player_tag: Нормализованный тег игрока.
            clan_id: DB ID актуального клана.
            observed_at: Время наблюдения состава.

        Returns:
            Количество закрытых snapshot-записей.
        """
        result = await self._session.execute(
            select(ClanMemberSnapshot).where(
                ClanMemberSnapshot.player_tag == player_tag,
                ClanMemberSnapshot.clan_id != clan_id,
                ClanMemberSnapshot.is_current.is_(True),
            )
        )
        snapshots = list(result.scalars().all())

        for snapshot in snapshots:
            _mark_snapshot_not_current(snapshot, observed_at=observed_at)

        return len(snapshots)

    async def get_player_account_by_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает подтверждённый аккаунт по тегу.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            `PlayerAccount` или `None`.
        """
        result = await self._session.execute(
            select(PlayerAccount).where(PlayerAccount.player_tag == player_tag)
        )
        return result.scalar_one_or_none()

    def add_player_event(self, player_event: PlayerEvent) -> None:
        """Добавляет событие игрока в текущую session.

        Args:
            player_event: Новая модель события.
        """
        self._session.add(player_event)

    def add_member_snapshot(self, snapshot: ClanMemberSnapshot) -> None:
        """Добавляет snapshot участника в текущую session.

        Args:
            snapshot: Новый snapshot участника.
        """
        self._session.add(snapshot)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class MemberLifecycleService:
    """Сервис обработки состава клана.

    Сервис поддерживает current-state состава: актуальный участник обновляется
    in-place, пропавший участник закрывается через `is_current=False`, а игрок,
    появившийся в другом клане, закрывается в старых current snapshots.
    """

    def __init__(self, *, repository: MemberLifecycleRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository для состава и связанных аккаунтов.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "MemberLifecycleService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyMemberLifecycleRepository(session))

    async def process_clan_members(
        self,
        *,
        clan: Clan,
        members: list[ClashClanMember],
    ) -> MemberLifecycleResult:
        """Обрабатывает актуальный API-состав клана.

        Args:
            clan: Отслеживаемый клан.
            members: Список участников из Clash API.

        Returns:
            Счётчики изменений состава.

        Raises:
            MemberLifecycleError: Если клан ещё не сохранён в БД.
        """
        clan_id = _required_model_id(clan, model_name="Clan")
        observed_at = _utc_now()
        incoming_members = _normalize_members(members)

        created_count = 0
        updated_count = 0
        left_count = 0
        moved_count = 0

        current_snapshots = await self._repository.list_current_by_clan(clan_id)
        incoming_tags = set(incoming_members)

        for snapshot in current_snapshots:
            if snapshot.player_tag not in incoming_tags:
                _mark_snapshot_not_current(snapshot, observed_at=observed_at)
                account = await self._repository.get_player_account_by_tag(snapshot.player_tag)
                self._repository.add_player_event(
                    _build_member_left_event(
                        clan=clan,
                        snapshot=snapshot,
                        account=account,
                        observed_at=observed_at,
                    )
                )
                left_count += 1

        for player_tag, member in incoming_members.items():
            account = await self._repository.get_player_account_by_tag(player_tag)
            moved_from_other_clans_count = (
                await self._repository.close_current_snapshots_in_other_clans(
                    player_tag=player_tag,
                    clan_id=clan_id,
                    observed_at=observed_at,
                )
            )
            moved_count += moved_from_other_clans_count
            if moved_from_other_clans_count:
                self._repository.add_player_event(
                    _build_member_moved_event(
                        clan=clan,
                        member=member,
                        account=account,
                        observed_at=observed_at,
                    )
                )

            snapshot = await self._repository.get_current_by_clan_and_player_tag(
                player_tag=player_tag,
                clan_id=clan_id,
            )
            if snapshot is None:
                snapshot = _build_member_snapshot(
                    clan=clan,
                    member=member,
                    observed_at=observed_at,
                )
                self._repository.add_member_snapshot(snapshot)
                self._repository.add_player_event(
                    _build_member_joined_event(
                        clan=clan,
                        member=member,
                        account=account,
                        observed_at=observed_at,
                    )
                )
                created_count += 1
            else:
                previous_name = snapshot.name
                _apply_member_snapshot_update(snapshot, member=member, observed_at=observed_at)
                if previous_name != member.name:
                    self._repository.add_player_event(
                        _build_member_renamed_event(
                            clan=clan,
                            member=member,
                            previous_name=previous_name,
                            account=account,
                            observed_at=observed_at,
                        )
                    )
                updated_count += 1

            if account is not None:
                account.last_seen_clan_id = clan_id
                account.last_seen_clan = clan

        await self._repository.flush()

        return MemberLifecycleResult(
            clan=clan,
            created_count=created_count,
            updated_count=updated_count,
            left_count=left_count,
            moved_count=moved_count,
            current_count=len(incoming_members),
        )


def _normalize_members(members: list[ClashClanMember]) -> dict[str, ClashClanMember]:
    """Нормализует список участников в словарь по player tag.

    Args:
        members: Участники клана из Clash API.

    Returns:
        Словарь `player_tag -> member`.

    Raises:
        MemberLifecycleError: Если вход содержит дубликаты player tag.
    """
    normalized_members: dict[str, ClashClanMember] = {}

    for member in members:
        player_tag = normalize_player_tag(member.player_tag)
        if player_tag in normalized_members:
            raise MemberLifecycleError(f"Дубликат player_tag в составе клана: {player_tag}.")
        normalized_members[player_tag] = member

    return normalized_members


def _build_member_snapshot(
    *,
    clan: Clan,
    member: ClashClanMember,
    observed_at: datetime,
) -> ClanMemberSnapshot:
    """Создаёт current snapshot участника клана.

    Args:
        clan: Отслеживаемый клан.
        member: Участник из Clash API.
        observed_at: Время наблюдения состава.

    Returns:
        Новый current snapshot.
    """
    return ClanMemberSnapshot(
        clan_id=_required_model_id(clan, model_name="Clan"),
        clan=clan,
        player_tag=normalize_player_tag(member.player_tag),
        name=member.name,
        role=member.role,
        town_hall_level=member.town_hall_level,
        exp_level=member.exp_level,
        trophies=member.trophies,
        donations=member.donations,
        donations_received=member.donations_received,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        snapshot_at=observed_at,
        is_current=True,
    )


def _apply_member_snapshot_update(
    snapshot: ClanMemberSnapshot,
    *,
    member: ClashClanMember,
    observed_at: datetime,
) -> None:
    """Обновляет current snapshot участника без потери first_seen_at.

    Args:
        snapshot: Текущий snapshot участника.
        member: Участник из Clash API.
        observed_at: Время наблюдения состава.
    """
    snapshot.name = member.name
    snapshot.role = member.role
    snapshot.town_hall_level = member.town_hall_level
    snapshot.exp_level = member.exp_level
    snapshot.trophies = member.trophies
    snapshot.donations = member.donations
    snapshot.donations_received = member.donations_received
    snapshot.last_seen_at = observed_at
    snapshot.snapshot_at = observed_at
    snapshot.is_current = True


def _mark_snapshot_not_current(snapshot: ClanMemberSnapshot, *, observed_at: datetime) -> None:
    """Закрывает current snapshot участника.

    Args:
        snapshot: Snapshot участника.
        observed_at: Время наблюдения ухода/перехода.
    """
    snapshot.is_current = False
    snapshot.last_seen_at = observed_at
    snapshot.snapshot_at = observed_at


def _build_member_joined_event(
    *,
    clan: Clan,
    member: ClashClanMember,
    account: PlayerAccount | None,
    observed_at: datetime,
) -> PlayerEvent:
    """Создаёт событие появления игрока в клане.

    Args:
        clan: Клан, в котором замечен игрок.
        member: Участник из Clash API.
        account: Подтверждённый аккаунт игрока, если он есть.
        observed_at: Время наблюдения состава.

    Returns:
        Модель события игрока.
    """
    player_tag = normalize_player_tag(member.player_tag)
    return PlayerEvent(
        telegram_user_id=_account_telegram_user_id(account),
        player_tag=player_tag,
        event_type=_MEMBER_JOINED_EVENT_TYPE,
        title="Игрок появился в клане",
        description=f"Игрок {member.name} появился в клане {clan.name}.",
        metadata_json=_build_member_event_metadata(
            clan=clan,
            player_tag=player_tag,
            player_name=member.name,
        ),
        created_at=observed_at,
    )


def _build_member_left_event(
    *,
    clan: Clan,
    snapshot: ClanMemberSnapshot,
    account: PlayerAccount | None,
    observed_at: datetime,
) -> PlayerEvent:
    """Создаёт событие ухода игрока из клана.

    Args:
        clan: Клан, из которого пропал игрок.
        snapshot: Последний current snapshot игрока.
        account: Подтверждённый аккаунт игрока, если он есть.
        observed_at: Время наблюдения ухода.

    Returns:
        Модель события игрока.
    """
    player_tag = normalize_player_tag(snapshot.player_tag)
    return PlayerEvent(
        telegram_user_id=_account_telegram_user_id(account),
        player_tag=player_tag,
        event_type=_MEMBER_LEFT_EVENT_TYPE,
        title="Игрок вышел из клана",
        description=f"Игрок {snapshot.name} больше не найден в составе клана {clan.name}.",
        metadata_json=_build_member_event_metadata(
            clan=clan,
            player_tag=player_tag,
            player_name=snapshot.name,
        ),
        created_at=observed_at,
    )


def _build_member_moved_event(
    *,
    clan: Clan,
    member: ClashClanMember,
    account: PlayerAccount | None,
    observed_at: datetime,
) -> PlayerEvent:
    """Создаёт событие перехода игрока в другой отслеживаемый клан.

    Args:
        clan: Новый актуальный клан игрока.
        member: Участник из Clash API.
        account: Подтверждённый аккаунт игрока, если он есть.
        observed_at: Время наблюдения перехода.

    Returns:
        Модель события игрока.
    """
    player_tag = normalize_player_tag(member.player_tag)
    return PlayerEvent(
        telegram_user_id=_account_telegram_user_id(account),
        player_tag=player_tag,
        event_type=_MEMBER_MOVED_EVENT_TYPE,
        title="Игрок перешёл в другой клан",
        description=f"Игрок {member.name} теперь найден в клане {clan.name}.",
        metadata_json=_build_member_event_metadata(
            clan=clan,
            player_tag=player_tag,
            player_name=member.name,
        ),
        created_at=observed_at,
    )


def _build_member_renamed_event(
    *,
    clan: Clan,
    member: ClashClanMember,
    previous_name: str,
    account: PlayerAccount | None,
    observed_at: datetime,
) -> PlayerEvent:
    """Создаёт событие смены ника игрока.

    Args:
        clan: Клан, где замечена смена ника.
        member: Участник из Clash API с новым ником.
        previous_name: Предыдущее имя из current snapshot.
        account: Подтверждённый аккаунт игрока, если он есть.
        observed_at: Время наблюдения смены ника.

    Returns:
        Модель события игрока.
    """
    player_tag = normalize_player_tag(member.player_tag)
    return PlayerEvent(
        telegram_user_id=_account_telegram_user_id(account),
        player_tag=player_tag,
        event_type=_MEMBER_RENAMED_EVENT_TYPE,
        title="Игрок сменил ник",
        description=f"Игрок {previous_name} сменил ник на {member.name}.",
        metadata_json=_build_member_event_metadata(
            clan=clan,
            player_tag=player_tag,
            player_name=member.name,
            previous_name=previous_name,
        ),
        created_at=observed_at,
    )


def _build_member_event_metadata(
    *,
    clan: Clan,
    player_tag: str,
    player_name: str,
    previous_name: str | None = None,
) -> dict[str, object]:
    """Собирает metadata для события жизненного цикла участника.

    Args:
        clan: Клан события.
        player_tag: Тег игрока.
        player_name: Актуальное имя игрока.
        previous_name: Предыдущее имя игрока, если событие связано со сменой ника.

    Returns:
        JSON-совместимый словарь metadata.
    """
    metadata: dict[str, object] = {
        "clan_id": _required_model_id(clan, model_name="Clan"),
        "clan_tag": clan.tag,
        "clan_name": clan.name,
        "player_tag": normalize_player_tag(player_tag),
        "player_name": player_name,
    }
    if previous_name is not None:
        metadata["previous_name"] = previous_name

    return metadata


def _account_telegram_user_id(account: PlayerAccount | None) -> int | None:
    """Возвращает TelegramUser ID подтверждённого аккаунта.

    Args:
        account: Подтверждённый аккаунт игрока или `None`.

    Returns:
        DB ID TelegramUser или `None`.
    """
    if account is None:
        return None

    return account.telegram_user_id


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.

    Raises:
        MemberLifecycleError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise MemberLifecycleError(f"{model_name} должен быть сохранён в БД.")


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "MemberLifecycleError",
    "MemberLifecycleRepository",
    "MemberLifecycleResult",
    "MemberLifecycleService",
    "SqlAlchemyMemberLifecycleRepository",
]
