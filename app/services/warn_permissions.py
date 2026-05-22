"""Сервис проверки прав ручной команды `/warn`."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.settings import Settings
from app.db.models import Clan, ClanMemberSnapshot, PlayerAccount, TelegramUser
from app.domain import ClanType
from app.integrations.clash import ClashClanMember
from app.services.member_lifecycle import MemberLifecycleService

_ALLOWED_WARN_ROLES = frozenset({"leader", "coleader", "elder"})


class WarnPermissionStatus(StrEnum):
    """Статус проверки прав ручного warn."""

    ALLOWED = "allowed"
    NO_TELEGRAM_USER = "no_telegram_user"
    NO_LINKED_MAIN_ACCOUNT = "no_linked_main_account"
    ROLE_NOT_ALLOWED = "role_not_allowed"
    ROLE_UNCONFIRMED = "role_unconfirmed"
    TARGET_NOT_MAIN = "target_not_main"
    TARGET_UNCONFIRMED = "target_unconfirmed"


@dataclass(frozen=True, slots=True)
class WarnPermissionAccount:
    """Аккаунт, подтвердивший право или main-присутствие."""

    player_tag: str
    player_name: str
    clan_id: int
    clan_tag: str
    clan_name: str
    role: str | None = None


@dataclass(frozen=True, slots=True)
class WarnPermissionDecision:
    """Результат проверки прав `/warn`.

    Attributes:
        allowed: Разрешено ли продолжать сценарий.
        status: Технический статус проверки.
        account: Аккаунт, который подтвердил право, если он есть.
        refreshed_clan_tags: Кланы, которые пришлось обновить из Clash API.
    """

    allowed: bool
    status: WarnPermissionStatus
    account: WarnPermissionAccount | None = None
    refreshed_clan_tags: tuple[str, ...] = ()

    @classmethod
    def allow(
        cls,
        *,
        account: WarnPermissionAccount | None = None,
        refreshed_clan_tags: tuple[str, ...] = (),
    ) -> "WarnPermissionDecision":
        """Создаёт успешный результат.

        Args:
            account: Аккаунт, подтвердивший право.
            refreshed_clan_tags: Теги обновлённых кланов.

        Returns:
            Разрешающий результат.
        """
        return cls(
            allowed=True,
            status=WarnPermissionStatus.ALLOWED,
            account=account,
            refreshed_clan_tags=refreshed_clan_tags,
        )

    @classmethod
    def denied(
        cls,
        status: WarnPermissionStatus,
        *,
        refreshed_clan_tags: tuple[str, ...] = (),
    ) -> "WarnPermissionDecision":
        """Создаёт запрещающий результат.

        Args:
            status: Причина отказа.
            refreshed_clan_tags: Теги обновлённых кланов.

        Returns:
            Запрещающий результат.
        """
        return cls(
            allowed=False,
            status=status,
            refreshed_clan_tags=refreshed_clan_tags,
        )


@dataclass(frozen=True, slots=True)
class _RefreshResult:
    """Результат refresh попытки."""

    success: bool
    refreshed_clan_tags: tuple[str, ...]


class WarningPermissionClashProvider(Protocol):
    """Contract Clash API provider для refresh состава."""

    async def get_clan_members(self, clan_tag: str) -> list[ClashClanMember]:
        """Получает текущий состав клана.

        Args:
            clan_tag: Тег клана.

        Returns:
            Список участников клана из Clash API.
        """


class WarningPermissionMemberLifecycleService(Protocol):
    """Contract сервиса обновления состава клана."""

    async def process_clan_members(
        self,
        *,
        clan: Clan,
        members: list[ClashClanMember],
    ) -> object:
        """Обновляет current snapshots состава клана.

        Args:
            clan: Клан.
            members: Участники из Clash API.

        Returns:
            Результат обработки состава.
        """


class WarningPermissionRepository(Protocol):
    """Repository contract проверки прав `/warn`."""

    async def list_active_accounts_by_telegram_user_id(
        self,
        telegram_user_id: int,
    ) -> tuple[PlayerAccount, ...]:
        """Возвращает активные linked accounts TelegramUser.

        Args:
            telegram_user_id: DB ID TelegramUser.

        Returns:
            Активные игровые аккаунты.
        """

    async def list_current_main_snapshots_by_player_tags(
        self,
        player_tags: tuple[str, ...],
    ) -> tuple[ClanMemberSnapshot, ...]:
        """Возвращает current snapshots в active main-кланах.

        Args:
            player_tags: Теги игровых аккаунтов.

        Returns:
            Current snapshots только для active main-кланов.
        """


class SqlAlchemyWarningPermissionRepository:
    """SQLAlchemy repository проверки прав `/warn`."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_active_accounts_by_telegram_user_id(
        self,
        telegram_user_id: int,
    ) -> tuple[PlayerAccount, ...]:
        """Возвращает активные linked accounts TelegramUser."""
        result = await self._session.execute(
            select(PlayerAccount)
            .where(
                PlayerAccount.telegram_user_id == telegram_user_id,
                PlayerAccount.is_active.is_(True),
                PlayerAccount.unlinked_at.is_(None),
            )
            .order_by(PlayerAccount.player_tag.asc())
        )
        return tuple(result.scalars().all())

    async def list_current_main_snapshots_by_player_tags(
        self,
        player_tags: tuple[str, ...],
    ) -> tuple[ClanMemberSnapshot, ...]:
        """Возвращает current snapshots в active main-кланах."""
        if not player_tags:
            return ()

        result = await self._session.execute(
            select(ClanMemberSnapshot)
            .join(Clan, ClanMemberSnapshot.clan_id == Clan.id)
            .options(selectinload(ClanMemberSnapshot.clan))
            .where(
                ClanMemberSnapshot.player_tag.in_(player_tags),
                ClanMemberSnapshot.is_current.is_(True),
                Clan.type == ClanType.MAIN.value,
                Clan.is_active.is_(True),
            )
            .order_by(ClanMemberSnapshot.player_tag.asc(), ClanMemberSnapshot.snapshot_at.desc())
        )
        return tuple(result.scalars().all())


class WarningPermissionService:
    """Сервис проверки прав ручной команды `/warn`.

    Сервис не создаёт `Warning`. Он проверяет только право инициатора и то,
    что цель находится в active main-клане. При stale snapshot сервис пытается
    точечно обновить состав соответствующего main-клана.
    """

    def __init__(
        self,
        *,
        repository: WarningPermissionRepository,
        clash_client: WarningPermissionClashProvider,
        member_lifecycle_service: WarningPermissionMemberLifecycleService,
        role_snapshot_max_age_minutes: int,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        """Инициализирует service.

        Args:
            repository: Repository проверки прав.
            clash_client: Clash API provider.
            member_lifecycle_service: Сервис обновления состава.
            role_snapshot_max_age_minutes: Максимальный возраст role snapshot.
            now_provider: Явный provider времени для тестов.

        Raises:
            ValueError: Если stale-порог некорректный.
        """
        if role_snapshot_max_age_minutes <= 0:
            raise ValueError("role_snapshot_max_age_minutes должен быть больше 0.")

        self._repository = repository
        self._clash_client = clash_client
        self._member_lifecycle_service = member_lifecycle_service
        self._role_snapshot_max_age = timedelta(minutes=role_snapshot_max_age_minutes)
        self._now_provider = now_provider or _utc_now

    @classmethod
    def from_session(
        cls,
        *,
        session: AsyncSession,
        clash_client: WarningPermissionClashProvider,
        settings: Settings,
    ) -> "WarningPermissionService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            clash_client: Clash API provider.
            settings: Runtime settings.

        Returns:
            Настроенный service.
        """
        return cls(
            repository=SqlAlchemyWarningPermissionRepository(session),
            clash_client=clash_client,
            member_lifecycle_service=MemberLifecycleService.from_session(session=session),
            role_snapshot_max_age_minutes=settings.role_snapshot_max_age_minutes,
        )

    async def check_initiator(
        self,
        *,
        telegram_user: TelegramUser | None,
    ) -> WarnPermissionDecision:
        """Проверяет право инициатора использовать `/warn`.

        Args:
            telegram_user: TelegramUser инициатора или `None`.

        Returns:
            Результат проверки прав.
        """
        if telegram_user is None:
            return WarnPermissionDecision.denied(WarnPermissionStatus.NO_TELEGRAM_USER)

        telegram_user_id = _required_model_id(telegram_user, model_name="TelegramUser")
        accounts = await self._repository.list_active_accounts_by_telegram_user_id(telegram_user_id)
        if not accounts:
            return WarnPermissionDecision.denied(WarnPermissionStatus.NO_LINKED_MAIN_ACCOUNT)

        snapshots, refresh_result = await self._load_snapshots_after_refresh_if_needed(
            accounts=accounts,
        )
        if not refresh_result.success:
            return WarnPermissionDecision.denied(
                WarnPermissionStatus.ROLE_UNCONFIRMED,
                refreshed_clan_tags=refresh_result.refreshed_clan_tags,
            )

        if not snapshots:
            return WarnPermissionDecision.denied(
                WarnPermissionStatus.NO_LINKED_MAIN_ACCOUNT,
                refreshed_clan_tags=refresh_result.refreshed_clan_tags,
            )

        for snapshot in snapshots:
            if _is_allowed_warn_role(snapshot.role):
                return WarnPermissionDecision.allow(
                    account=_account_from_snapshot(snapshot),
                    refreshed_clan_tags=refresh_result.refreshed_clan_tags,
                )

        if any(_normalize_role(snapshot.role) is None for snapshot in snapshots):
            return WarnPermissionDecision.denied(
                WarnPermissionStatus.ROLE_UNCONFIRMED,
                refreshed_clan_tags=refresh_result.refreshed_clan_tags,
            )

        return WarnPermissionDecision.denied(
            WarnPermissionStatus.ROLE_NOT_ALLOWED,
            refreshed_clan_tags=refresh_result.refreshed_clan_tags,
        )

    async def check_target(
        self,
        *,
        candidate: object,
    ) -> WarnPermissionDecision:
        """Проверяет, что цель warn сейчас находится в main-клане.

        Args:
            candidate: Candidate из `WarningTargetResolverService`.

        Returns:
            Результат проверки цели.
        """
        accounts = tuple(getattr(candidate, "accounts", ()))
        if not accounts:
            return WarnPermissionDecision.denied(WarnPermissionStatus.TARGET_NOT_MAIN)

        player_tags = tuple(str(account.player_tag) for account in accounts)
        snapshots, refresh_result = await self._load_snapshots_after_refresh_if_needed(
            player_tags=player_tags,
        )
        if not refresh_result.success:
            return WarnPermissionDecision.denied(
                WarnPermissionStatus.TARGET_UNCONFIRMED,
                refreshed_clan_tags=refresh_result.refreshed_clan_tags,
            )

        if not snapshots:
            return WarnPermissionDecision.denied(
                WarnPermissionStatus.TARGET_NOT_MAIN,
                refreshed_clan_tags=refresh_result.refreshed_clan_tags,
            )

        return WarnPermissionDecision.allow(
            account=_account_from_snapshot(snapshots[0]),
            refreshed_clan_tags=refresh_result.refreshed_clan_tags,
        )

    async def _load_snapshots_after_refresh_if_needed(
        self,
        *,
        accounts: tuple[PlayerAccount, ...] = (),
        player_tags: tuple[str, ...] = (),
    ) -> tuple[tuple[ClanMemberSnapshot, ...], _RefreshResult]:
        """Загружает main snapshots и обновляет stale-кланы.

        Args:
            accounts: Аккаунты, для которых нужно проверить main snapshots.
            player_tags: Явные player tags.

        Returns:
            Пара: snapshots после возможного refresh и результат refresh.
        """
        normalized_player_tags = player_tags or tuple(account.player_tag for account in accounts)
        snapshots = await self._repository.list_current_main_snapshots_by_player_tags(
            normalized_player_tags
        )

        stale_snapshots = tuple(snapshot for snapshot in snapshots if self._is_stale(snapshot))
        if not stale_snapshots:
            return snapshots, _RefreshResult(success=True, refreshed_clan_tags=())

        refresh_result = await self._refresh_stale_snapshot_clans(stale_snapshots)
        if not refresh_result.success:
            return snapshots, refresh_result

        refreshed_snapshots = await self._repository.list_current_main_snapshots_by_player_tags(
            normalized_player_tags
        )
        return refreshed_snapshots, refresh_result

    async def _refresh_stale_snapshot_clans(
        self,
        snapshots: tuple[ClanMemberSnapshot, ...],
    ) -> _RefreshResult:
        """Обновляет main-кланы, где есть stale current snapshot.

        Args:
            snapshots: Stale snapshots.

        Returns:
            Результат refresh.
        """
        refreshed_clan_tags: list[str] = []

        for clan in _unique_clans_from_snapshots(snapshots):
            try:
                members = await self._clash_client.get_clan_members(clan.tag)
                await self._member_lifecycle_service.process_clan_members(
                    clan=clan,
                    members=members,
                )
            except Exception:
                return _RefreshResult(
                    success=False,
                    refreshed_clan_tags=tuple(refreshed_clan_tags),
                )

            refreshed_clan_tags.append(clan.tag)

        return _RefreshResult(
            success=True,
            refreshed_clan_tags=tuple(refreshed_clan_tags),
        )

    def _is_stale(self, snapshot: ClanMemberSnapshot) -> bool:
        """Проверяет, устарел ли role snapshot.

        Args:
            snapshot: Snapshot участника.

        Returns:
            `True`, если snapshot старше разрешённого порога.
        """
        snapshot_at = snapshot.snapshot_at
        if snapshot_at.tzinfo is None or snapshot_at.utcoffset() is None:
            return True

        return snapshot_at < _ensure_aware_utc(self._now_provider()) - self._role_snapshot_max_age


def _unique_clans_from_snapshots(snapshots: tuple[ClanMemberSnapshot, ...]) -> tuple[Clan, ...]:
    """Возвращает уникальные main-кланы из snapshots.

    Args:
        snapshots: Snapshots участников.

    Returns:
        Tuple уникальных кланов.
    """
    clans_by_id: dict[int, Clan] = {}

    for snapshot in snapshots:
        clan = snapshot.clan
        if clan is None:
            continue

        clan_id = _required_model_id(clan, model_name="Clan")
        if clan.type == ClanType.MAIN.value and clan.is_active:
            clans_by_id[clan_id] = clan

    return tuple(clans_by_id.values())


def _account_from_snapshot(snapshot: ClanMemberSnapshot) -> WarnPermissionAccount:
    """Создаёт account view из snapshot.

    Args:
        snapshot: Snapshot участника.

    Returns:
        Account view для permission decision.
    """
    clan = snapshot.clan
    if clan is None:
        raise ValueError("ClanMemberSnapshot.clan должен быть загружен.")

    return WarnPermissionAccount(
        player_tag=snapshot.player_tag,
        player_name=snapshot.name,
        clan_id=_required_model_id(clan, model_name="Clan"),
        clan_tag=clan.tag,
        clan_name=clan.name,
        role=snapshot.role,
    )


def _is_allowed_warn_role(role: str | None) -> bool:
    """Проверяет, входит ли роль в список officer-ролей.

    Args:
        role: Роль из Clash API.

    Returns:
        `True`, если роль может использовать `/warn`.
    """
    normalized_role = _normalize_role(role)
    return normalized_role in _ALLOWED_WARN_ROLES


def _normalize_role(role: str | None) -> str | None:
    """Нормализует роль участника клана.

    Args:
        role: Роль из Clash API.

    Returns:
        Нормализованная роль или `None`.
    """
    if role is None:
        return None

    normalized = role.strip().lower()
    return normalized or None


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели.

    Returns:
        Положительный DB id.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise ValueError(f"{model_name} должен быть сохранён в БД.")


def _ensure_aware_utc(value: datetime) -> datetime:
    """Гарантирует timezone-aware UTC datetime.

    Args:
        value: Datetime.

    Returns:
        Timezone-aware UTC datetime.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def _utc_now() -> datetime:
    """Возвращает текущее UTC-время.

    Returns:
        Timezone-aware UTC datetime.
    """
    return datetime.now(UTC)


__all__ = [
    "SqlAlchemyWarningPermissionRepository",
    "WarnPermissionAccount",
    "WarnPermissionDecision",
    "WarnPermissionStatus",
    "WarningPermissionClashProvider",
    "WarningPermissionMemberLifecycleService",
    "WarningPermissionRepository",
    "WarningPermissionService",
]
