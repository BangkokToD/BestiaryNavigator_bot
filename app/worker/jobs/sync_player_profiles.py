"""Worker job синхронизации snapshot-профилей игроков."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiError, ClanMemberSnapshot, PlayerAccount, PlayerProfileSnapshot
from app.domain import normalize_player_tag
from app.integrations.clash import ClashApiClient, ClashApiError, map_clash_api_error_to_context
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

SYNC_PLAYER_PROFILES_JOB_NAME = "sync_player_profiles"

_PLAYER_PROFILE_ENTITY_TYPE = "player_profile"
type JsonObject = dict[str, object]


class ClashPlayerProfileProvider(Protocol):
    """Contract Clash API provider для синхронизации профилей игроков."""

    async def get_player(self, player_tag: str) -> Mapping[str, object]:
        """Получает профиль игрока.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            JSON object профиля игрока.
        """


class SyncPlayerProfilesRepository(Protocol):
    """Repository contract для sync player profiles job."""

    async def list_profile_player_tags(self) -> tuple[str, ...]:
        """Возвращает уникальные player tags для синхронизации профилей.

        Returns:
            Tuple тегов из active linked accounts и current clan members.
        """

    async def get_latest_profile_snapshot(
        self,
        player_tag: str,
    ) -> PlayerProfileSnapshot | None:
        """Возвращает последний snapshot профиля игрока.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            Последний snapshot или `None`.
        """

    def add_profile_snapshot(self, snapshot: PlayerProfileSnapshot) -> None:
        """Добавляет snapshot профиля в unit of work.

        Args:
            snapshot: Новый snapshot профиля игрока.
        """

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в unit of work.

        Args:
            api_error: Модель ошибки внешнего API.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemySyncPlayerProfilesRepository:
    """SQLAlchemy repository для sync player profiles job."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_profile_player_tags(self) -> tuple[str, ...]:
        """Возвращает уникальные player tags из linked accounts и current members.

        Returns:
            Tuple нормализованных тегов в стабильном порядке.
        """
        linked_accounts_result = await self._session.execute(
            select(PlayerAccount.player_tag).where(PlayerAccount.is_active.is_(True))
        )
        current_members_result = await self._session.execute(
            select(ClanMemberSnapshot.player_tag).where(ClanMemberSnapshot.is_current.is_(True))
        )

        player_tags = {
            normalize_player_tag(player_tag)
            for player_tag in [
                *linked_accounts_result.scalars().all(),
                *current_members_result.scalars().all(),
            ]
        }
        return tuple(sorted(player_tags))

    async def get_latest_profile_snapshot(
        self,
        player_tag: str,
    ) -> PlayerProfileSnapshot | None:
        """Возвращает последний snapshot профиля игрока.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            Последний snapshot или `None`.
        """
        result = await self._session.execute(
            select(PlayerProfileSnapshot)
            .where(PlayerProfileSnapshot.player_tag == player_tag)
            .order_by(
                PlayerProfileSnapshot.snapshot_at.desc(),
                PlayerProfileSnapshot.id.desc(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    def add_profile_snapshot(self, snapshot: PlayerProfileSnapshot) -> None:
        """Добавляет snapshot профиля в текущую session.

        Args:
            snapshot: Новый snapshot профиля игрока.
        """
        self._session.add(snapshot)

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в текущую session.

        Args:
            api_error: Модель ошибки внешнего API.
        """
        self._session.add(api_error)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class PlayerProfileData:
    """Нормализованные данные профиля игрока для snapshot."""

    player_tag: str
    name: str
    town_hall_level: int | None
    town_hall_weapon_level: int | None
    exp_level: int | None
    trophies: int | None
    best_trophies: int | None
    war_stars: int | None
    donations: int | None
    donations_received: int | None
    clan_capital_contributions: int | None
    heroes_json: list[JsonObject]
    troops_json: list[JsonObject]
    spells_json: list[JsonObject]
    achievements_json: list[JsonObject]


@dataclass(frozen=True, slots=True)
class SyncPlayerProfilesJobResult:
    """Результат одного запуска sync player profiles job."""

    discovered_count: int
    synced_count: int
    failed_count: int
    created_snapshot_count: int
    skipped_unchanged_count: int


class SyncPlayerProfilesJob:
    """Job синхронизации snapshot-профилей игроков из Clash API."""

    def __init__(
        self,
        *,
        repository: SyncPlayerProfilesRepository,
        clash_client: ClashPlayerProfileProvider,
        clock: object | None = None,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository для профилей и ошибок API.
            clash_client: Clash API client или совместимый provider.
            clock: Источник времени для тестов. Если объект callable, он должен
                возвращать timezone-aware `datetime`.
        """
        self._repository = repository
        self._clash_client = clash_client
        self._clock = clock

    @classmethod
    def from_session(
        cls,
        *,
        session: AsyncSession,
        clash_client: ClashPlayerProfileProvider,
    ) -> "SyncPlayerProfilesJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            clash_client: Clash API client или совместимый provider.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemySyncPlayerProfilesRepository(session),
            clash_client=clash_client,
        )

    async def run(self, context: WorkerJobContext) -> SyncPlayerProfilesJobResult:
        """Синхронизирует snapshot-профили игроков.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        player_tags = await self._repository.list_profile_player_tags()

        synced_count = 0
        failed_count = 0
        created_snapshot_count = 0
        skipped_unchanged_count = 0

        for player_tag in player_tags:
            if context.should_stop:
                break

            try:
                payload = await self._clash_client.get_player(player_tag)
            except ClashApiError as error:
                self._handle_clash_api_error(
                    player_tag=player_tag,
                    error=error,
                    worker_name=context.job_name,
                )
                failed_count += 1
                continue

            profile_data = _extract_profile_data(payload)
            latest_snapshot = await self._repository.get_latest_profile_snapshot(
                profile_data.player_tag
            )

            synced_count += 1
            if latest_snapshot is not None and _snapshot_matches_profile_data(
                latest_snapshot,
                profile_data,
            ):
                skipped_unchanged_count += 1
                continue

            self._repository.add_profile_snapshot(
                _build_profile_snapshot(
                    profile_data=profile_data,
                    snapshot_at=self._now(),
                )
            )
            created_snapshot_count += 1

        await self._repository.flush()

        return SyncPlayerProfilesJobResult(
            discovered_count=len(player_tags),
            synced_count=synced_count,
            failed_count=failed_count,
            created_snapshot_count=created_snapshot_count,
            skipped_unchanged_count=skipped_unchanged_count,
        )

    def _handle_clash_api_error(
        self,
        *,
        player_tag: str,
        error: ClashApiError,
        worker_name: str,
    ) -> None:
        """Обрабатывает typed ошибку Clash API для профиля игрока.

        Args:
            player_tag: Тег игрока.
            error: Typed Clash API exception.
            worker_name: Имя текущей worker job для debug-контекста.
        """
        error_context = map_clash_api_error_to_context(
            error,
            entity_type=_PLAYER_PROFILE_ENTITY_TYPE,
            entity_tag=player_tag,
            worker_name=worker_name,
            retry_count=0,
        )
        self._repository.add_api_error(ApiError(**error_context.to_api_error_values()))

    def _now(self) -> datetime:
        """Возвращает текущее время для snapshot.

        Returns:
            Timezone-aware UTC datetime.
        """
        if callable(self._clock):
            value = self._clock()
            if not isinstance(value, datetime):
                raise TypeError("clock должен возвращать datetime.")
            return value

        return datetime.now(UTC)


def register_sync_player_profiles_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует sync player profiles job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=SYNC_PLAYER_PROFILES_JOB_NAME)
    async def sync_player_profiles(context: WorkerJobContext) -> None:
        """Запускает синхронизацию профилей внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("sync_player_profiles требует DB session.")

        async with ClashApiClient.from_settings() as clash_client:
            job = SyncPlayerProfilesJob.from_session(
                session=context.session,
                clash_client=clash_client,
            )
            await job.run(context)


def _extract_profile_data(payload: Mapping[str, object]) -> PlayerProfileData:
    """Извлекает нормализованные данные профиля из payload Clash API.

    Args:
        payload: JSON object ответа `GET /players/{playerTag}`.

    Returns:
        Нормализованные данные профиля для snapshot.
    """
    return PlayerProfileData(
        player_tag=normalize_player_tag(_required_str_field(payload, "tag")),
        name=_required_str_field(payload, "name"),
        town_hall_level=_optional_int_field(payload, "townHallLevel"),
        town_hall_weapon_level=_optional_int_field(payload, "townHallWeaponLevel"),
        exp_level=_optional_int_field(payload, "expLevel"),
        trophies=_optional_int_field(payload, "trophies"),
        best_trophies=_optional_int_field(payload, "bestTrophies"),
        war_stars=_optional_int_field(payload, "warStars"),
        donations=_optional_int_field(payload, "donations"),
        donations_received=_optional_int_field(payload, "donationsReceived"),
        clan_capital_contributions=_optional_int_field(
            payload,
            "clanCapitalContributions",
        ),
        heroes_json=_optional_json_object_list(payload, "heroes"),
        troops_json=_optional_json_object_list(payload, "troops"),
        spells_json=_optional_json_object_list(payload, "spells"),
        achievements_json=_optional_json_object_list(payload, "achievements"),
    )


def _build_profile_snapshot(
    *,
    profile_data: PlayerProfileData,
    snapshot_at: datetime,
) -> PlayerProfileSnapshot:
    """Создаёт snapshot профиля игрока.

    Args:
        profile_data: Нормализованные данные профиля.
        snapshot_at: Время snapshot.

    Returns:
        Модель snapshot профиля.
    """
    return PlayerProfileSnapshot(
        player_tag=profile_data.player_tag,
        name=profile_data.name,
        town_hall_level=profile_data.town_hall_level,
        town_hall_weapon_level=profile_data.town_hall_weapon_level,
        exp_level=profile_data.exp_level,
        trophies=profile_data.trophies,
        best_trophies=profile_data.best_trophies,
        war_stars=profile_data.war_stars,
        donations=profile_data.donations,
        donations_received=profile_data.donations_received,
        clan_capital_contributions=profile_data.clan_capital_contributions,
        heroes_json=profile_data.heroes_json,
        troops_json=profile_data.troops_json,
        spells_json=profile_data.spells_json,
        achievements_json=profile_data.achievements_json,
        snapshot_at=snapshot_at,
    )


def _snapshot_matches_profile_data(
    snapshot: PlayerProfileSnapshot,
    profile_data: PlayerProfileData,
) -> bool:
    """Сравнивает последний snapshot с актуальным профилем.

    Args:
        snapshot: Последний сохранённый snapshot.
        profile_data: Актуальные данные профиля из Clash API.

    Returns:
        `True`, если новый snapshot не нужен.
    """
    return (
        snapshot.player_tag == profile_data.player_tag
        and snapshot.name == profile_data.name
        and snapshot.town_hall_level == profile_data.town_hall_level
        and snapshot.town_hall_weapon_level == profile_data.town_hall_weapon_level
        and snapshot.exp_level == profile_data.exp_level
        and snapshot.trophies == profile_data.trophies
        and snapshot.best_trophies == profile_data.best_trophies
        and snapshot.war_stars == profile_data.war_stars
        and snapshot.donations == profile_data.donations
        and snapshot.donations_received == profile_data.donations_received
        and snapshot.clan_capital_contributions == profile_data.clan_capital_contributions
        and snapshot.heroes_json == profile_data.heroes_json
        and snapshot.troops_json == profile_data.troops_json
        and snapshot.spells_json == profile_data.spells_json
        and snapshot.achievements_json == profile_data.achievements_json
    )


def _required_str_field(payload: Mapping[str, object], field_name: str) -> str:
    """Достаёт обязательное строковое поле.

    Args:
        payload: JSON object.
        field_name: Название поля.

    Returns:
        Непустая строка без пробелов по краям.

    Raises:
        ValueError: Если поле отсутствует, пустое или не строковое.
    """
    value = payload.get(field_name)
    if not isinstance(value, str):
        raise ValueError(f"Clash API response должен содержать строковое поле {field_name}.")

    normalized = value.strip()
    if not normalized:
        raise ValueError(f"Clash API response содержит пустое поле {field_name}.")

    return normalized


def _optional_int_field(payload: Mapping[str, object], field_name: str) -> int | None:
    """Достаёт опциональное целочисленное поле.

    Args:
        payload: JSON object.
        field_name: Название поля.

    Returns:
        Целочисленное значение или `None`.

    Raises:
        ValueError: Если поле есть, но не является int.
    """
    value = payload.get(field_name)
    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Clash API response поле {field_name} должно быть целым числом.")

    return value


def _optional_json_object_list(
    payload: Mapping[str, object],
    field_name: str,
) -> list[JsonObject]:
    """Достаёт JSON list object-полей профиля без сохранения raw profile.

    Args:
        payload: JSON object.
        field_name: Название поля.

    Returns:
        Глубокая копия списка JSON-объектов.

    Raises:
        ValueError: Если поле есть, но не является списком объектов.
    """
    value = payload.get(field_name)
    if value is None:
        return []

    if not isinstance(value, list):
        raise ValueError(f"Clash API response поле {field_name} должно быть списком.")

    items: list[JsonObject] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(
                f"Clash API response поле {field_name} должно содержать только объекты."
            )

        items.append(cast(JsonObject, deepcopy(dict(item))))

    return items


__all__ = [
    "SYNC_PLAYER_PROFILES_JOB_NAME",
    "ClashPlayerProfileProvider",
    "PlayerProfileData",
    "SqlAlchemySyncPlayerProfilesRepository",
    "SyncPlayerProfilesJob",
    "SyncPlayerProfilesJobResult",
    "SyncPlayerProfilesRepository",
    "register_sync_player_profiles_job",
]
