"""Worker job синхронизации рейдов столицы клана."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiError, Clan, RaidMember, RaidSeason
from app.domain.enums import ClanType, RaidMemberStatus
from app.integrations.clash import (
    ClashApiClient,
    ClashApiError,
    ClashCapitalRaidSeason,
    map_clash_api_error_to_context,
)
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

SYNC_RAIDS_JOB_NAME = "sync_raids"

_RAID_ENTITY_TYPE = "capital_raid_seasons"
_RAID_EXPECTED_ATTACKS = 6
_RAID_SEASONS_LIMIT = 1
_RAID_SYNC_CLAN_TYPES = frozenset({ClanType.MAIN.value, ClanType.ACADEMY.value})


class ClashRaidSeasonsProvider(Protocol):
    """Contract Clash API provider для синхронизации рейдов."""

    async def get_capital_raid_seasons(
        self,
        clan_tag: str,
        *,
        limit: int | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> list[ClashCapitalRaidSeason]:
        """Получает рейдовые сезоны столицы клана.

        Args:
            clan_tag: Нормализованный тег клана.
            limit: Ограничение количества сезонов.
            after: Pagination marker after.
            before: Pagination marker before.

        Returns:
            Список рейдовых сезонов из Clash API.
        """


class SyncRaidsRepository(Protocol):
    """Repository contract для sync raids job."""

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает active-кланы.

        Returns:
            Tuple active-кланов.
        """

    async def get_raid_season_by_start_time(
        self,
        *,
        clan_id: int,
        start_time: datetime,
    ) -> RaidSeason | None:
        """Возвращает raid season по клану и времени старта.

        Args:
            clan_id: DB ID клана.
            start_time: Время начала рейдового сезона.

        Returns:
            Модель рейдового сезона или `None`.
        """

    def add_raid_season(self, raid_season: RaidSeason) -> None:
        """Добавляет raid season в unit of work.

        Args:
            raid_season: Новая модель рейдового сезона.
        """

    async def replace_raid_members(
        self,
        *,
        raid_season: RaidSeason,
        members: tuple[RaidMember, ...],
    ) -> None:
        """Заменяет участников рейдового сезона.

        Args:
            raid_season: Модель рейдового сезона.
            members: Актуальные участники сезона.
        """

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в unit of work.

        Args:
            api_error: Модель ошибки внешнего API.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemySyncRaidsRepository:
    """SQLAlchemy repository для sync raids job."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает active-кланы в стабильном порядке.

        Returns:
            Tuple active-кланов.
        """
        result = await self._session.execute(
            select(Clan)
            .where(
                Clan.is_active.is_(True),
                Clan.type.in_(sorted(_RAID_SYNC_CLAN_TYPES)),
            )
            .order_by(Clan.id)
        )
        return tuple(result.scalars().all())

    async def get_raid_season_by_start_time(
        self,
        *,
        clan_id: int,
        start_time: datetime,
    ) -> RaidSeason | None:
        """Возвращает raid season по клану и start_time.

        Args:
            clan_id: DB ID клана.
            start_time: Время начала рейдового сезона.

        Returns:
            Модель сезона или `None`.
        """
        result = await self._session.execute(
            select(RaidSeason).where(
                RaidSeason.clan_id == clan_id,
                RaidSeason.start_time == start_time,
            )
        )
        return result.scalar_one_or_none()

    def add_raid_season(self, raid_season: RaidSeason) -> None:
        """Добавляет raid season в текущую session.

        Args:
            raid_season: Новая модель рейдового сезона.
        """
        self._session.add(raid_season)

    async def replace_raid_members(
        self,
        *,
        raid_season: RaidSeason,
        members: tuple[RaidMember, ...],
    ) -> None:
        """Заменяет участников рейдового сезона без удаления season.

        Args:
            raid_season: Модель рейдового сезона.
            members: Актуальные участники сезона.
        """
        await self._session.flush()
        raid_season_id = _required_model_id(raid_season, model_name="RaidSeason")

        await self._session.execute(
            delete(RaidMember).where(RaidMember.raid_season_id == raid_season_id)
        )

        for member in members:
            member.raid_season_id = raid_season_id
            member.raid_season = raid_season
            self._session.add(member)

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
class RaidSeasonData:
    """Нормализованные данные рейдового сезона."""

    clan_id: int
    state: str
    start_time: datetime
    end_time: datetime
    capital_total_loot: int
    raids_completed: int
    total_attacks: int
    enemy_districts_destroyed: int
    offensive_reward: int
    defensive_reward: int
    snapshot_at: datetime


@dataclass(frozen=True, slots=True)
class SyncRaidsJobResult:
    """Результат одного запуска sync raids job."""

    discovered_count: int
    synced_count: int
    no_raid_count: int
    failed_count: int
    skipped_inactive_count: int
    skipped_unsupported_clan_count: int
    created_count: int
    updated_count: int
    member_count: int


class SyncRaidsJob:
    """Job синхронизации рейдов столицы active-кланов."""

    def __init__(
        self,
        *,
        repository: SyncRaidsRepository,
        clash_client: ClashRaidSeasonsProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository для рейдов и ошибок API.
            clash_client: Clash API client или совместимый provider.
            clock: Источник текущего времени для тестов.
        """
        self._repository = repository
        self._clash_client = clash_client
        self._clock = clock or _utc_now

    @classmethod
    def from_session(
        cls,
        *,
        session: AsyncSession,
        clash_client: ClashRaidSeasonsProvider,
    ) -> "SyncRaidsJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            clash_client: Clash API client или совместимый provider.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemySyncRaidsRepository(session),
            clash_client=clash_client,
        )

    async def run(self, context: WorkerJobContext) -> SyncRaidsJobResult:
        """Синхронизирует последние рейдовые сезоны.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        clans = await self._repository.list_active_clans()

        synced_count = 0
        no_raid_count = 0
        failed_count = 0
        skipped_inactive_count = 0
        skipped_unsupported_clan_count = 0
        created_count = 0
        updated_count = 0
        member_count = 0

        for clan in clans:
            if context.should_stop:
                break

            if not clan.is_active:
                skipped_inactive_count += 1
                continue

            if clan.type not in _RAID_SYNC_CLAN_TYPES:
                skipped_unsupported_clan_count += 1
                continue

            try:
                raid_seasons = await self._clash_client.get_capital_raid_seasons(
                    clan.tag,
                    limit=_RAID_SEASONS_LIMIT,
                )
            except ClashApiError as error:
                self._handle_clash_api_error(
                    clan=clan,
                    error=error,
                    worker_name=context.job_name,
                )
                failed_count += 1
                continue

            if not raid_seasons:
                no_raid_count += 1
                continue

            raid_season_payload = raid_seasons[0]
            snapshot_at = self._clock()
            season_data = _build_raid_season_data(
                clan=clan,
                raid_season=raid_season_payload,
                snapshot_at=snapshot_at,
            )
            raid_season = await self._repository.get_raid_season_by_start_time(
                clan_id=season_data.clan_id,
                start_time=season_data.start_time,
            )

            if raid_season is None:
                raid_season = _build_raid_season(season_data)
                self._repository.add_raid_season(raid_season)
                created_count += 1
            else:
                _apply_raid_season_update(raid_season, season_data)
                updated_count += 1

            members = _build_raid_members(raid_season_payload)
            await self._repository.replace_raid_members(
                raid_season=raid_season,
                members=members,
            )

            synced_count += 1
            member_count += len(members)

        await self._repository.flush()

        return SyncRaidsJobResult(
            discovered_count=len(clans),
            synced_count=synced_count,
            no_raid_count=no_raid_count,
            failed_count=failed_count,
            skipped_inactive_count=skipped_inactive_count,
            skipped_unsupported_clan_count=skipped_unsupported_clan_count,
            created_count=created_count,
            updated_count=updated_count,
            member_count=member_count,
        )

    def _handle_clash_api_error(
        self,
        *,
        clan: Clan,
        error: ClashApiError,
        worker_name: str,
    ) -> None:
        """Обрабатывает typed ошибку Clash API для рейдов клана.

        Args:
            clan: Клан, при синхронизации рейдов которого возникла ошибка.
            error: Typed Clash API exception.
            worker_name: Имя текущей worker job для debug-контекста.
        """
        error_context = map_clash_api_error_to_context(
            error,
            entity_type=_RAID_ENTITY_TYPE,
            entity_tag=clan.tag,
            worker_name=worker_name,
            retry_count=0,
        )
        self._repository.add_api_error(ApiError(**error_context.to_api_error_values()))


def register_sync_raids_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует sync raids job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=SYNC_RAIDS_JOB_NAME)
    async def sync_raids(context: WorkerJobContext) -> None:
        """Запускает синхронизацию рейдов внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("sync_raids требует DB session.")

        async with ClashApiClient.from_settings() as clash_client:
            job = SyncRaidsJob.from_session(
                session=context.session,
                clash_client=clash_client,
            )
            await job.run(context)


def _build_raid_season_data(
    *,
    clan: Clan,
    raid_season: ClashCapitalRaidSeason,
    snapshot_at: datetime,
) -> RaidSeasonData:
    """Собирает данные рейдового сезона.

    Args:
        clan: Отслеживаемый клан.
        raid_season: DTO рейдового сезона.
        snapshot_at: Время синхронизации.

    Returns:
        Нормализованные данные сезона.
    """
    return RaidSeasonData(
        clan_id=_required_model_id(clan, model_name="Clan"),
        state=raid_season.state,
        start_time=_parse_clash_datetime(raid_season.start_time, field_name="startTime"),
        end_time=_parse_clash_datetime(raid_season.end_time, field_name="endTime"),
        capital_total_loot=raid_season.capital_total_loot or 0,
        raids_completed=raid_season.raids_completed or 0,
        total_attacks=raid_season.total_attacks or 0,
        enemy_districts_destroyed=raid_season.enemy_districts_destroyed or 0,
        offensive_reward=raid_season.offensive_reward or 0,
        defensive_reward=raid_season.defensive_reward or 0,
        snapshot_at=snapshot_at,
    )


def _build_raid_season(season_data: RaidSeasonData) -> RaidSeason:
    """Создаёт модель рейдового сезона.

    Args:
        season_data: Нормализованные данные сезона.

    Returns:
        Новая модель `RaidSeason`.
    """
    return RaidSeason(
        clan_id=season_data.clan_id,
        state=season_data.state,
        start_time=season_data.start_time,
        end_time=season_data.end_time,
        capital_total_loot=season_data.capital_total_loot,
        raids_completed=season_data.raids_completed,
        total_attacks=season_data.total_attacks,
        enemy_districts_destroyed=season_data.enemy_districts_destroyed,
        offensive_reward=season_data.offensive_reward,
        defensive_reward=season_data.defensive_reward,
        snapshot_at=season_data.snapshot_at,
    )


def _apply_raid_season_update(raid_season: RaidSeason, season_data: RaidSeasonData) -> None:
    """Обновляет существующий рейдовый сезон.

    Args:
        raid_season: Существующая модель сезона.
        season_data: Актуальные данные сезона.
    """
    raid_season.state = season_data.state
    raid_season.end_time = season_data.end_time
    raid_season.capital_total_loot = season_data.capital_total_loot
    raid_season.raids_completed = season_data.raids_completed
    raid_season.total_attacks = season_data.total_attacks
    raid_season.enemy_districts_destroyed = season_data.enemy_districts_destroyed
    raid_season.offensive_reward = season_data.offensive_reward
    raid_season.defensive_reward = season_data.defensive_reward
    raid_season.snapshot_at = season_data.snapshot_at


def _build_raid_members(raid_season: ClashCapitalRaidSeason) -> tuple[RaidMember, ...]:
    """Создаёт участников рейдового сезона.

    Args:
        raid_season: DTO рейдового сезона.

    Returns:
        Tuple моделей участников рейда.
    """
    members: list[RaidMember] = []

    for member in raid_season.members:
        attacks = _normalize_attacks(member.attacks)
        members.append(
            RaidMember(
                player_tag=member.player_tag,
                name=member.name,
                attacks=attacks,
                project_expected_attacks=_RAID_EXPECTED_ATTACKS,
                capital_resources_looted=member.capital_resources_looted or 0,
                status=_build_raid_member_status(attacks).value,
            )
        )

    return tuple(members)


def _build_raid_member_status(attacks: int) -> RaidMemberStatus:
    """Вычисляет статус участника рейдов.

    Args:
        attacks: Количество атак участника.

    Returns:
        Доменный статус участника рейдов.
    """
    if attacks >= _RAID_EXPECTED_ATTACKS:
        return RaidMemberStatus.RAID_FULL

    if attacks == _RAID_EXPECTED_ATTACKS - 1:
        return RaidMemberStatus.RAID_INCOMPLETE

    return RaidMemberStatus.RAID_MISSED


def _normalize_attacks(value: int | None) -> int:
    """Нормализует количество атак из Clash API.

    Args:
        value: Количество атак из API или `None`.

    Returns:
        Неотрицательное количество атак.
    """
    if value is None:
        return 0

    return max(value, 0)


def _parse_clash_datetime(value: str | None, *, field_name: str) -> datetime:
    """Парсит datetime из формата Clash API или ISO-строки.

    Args:
        value: Строка времени из Clash API.
        field_name: Название поля для текста ошибки.

    Returns:
        Timezone-aware UTC datetime.
    """
    normalized = _required_string(value, field_name=field_name)

    for date_format in ("%Y%m%dT%H%M%S.%fZ", "%Y%m%dT%H%M%SZ"):
        try:
            return datetime.strptime(normalized, date_format).replace(tzinfo=UTC)
        except ValueError:
            pass

    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} имеет неподдерживаемый формат datetime.") from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} должен содержать timezone.")

    return parsed.astimezone(UTC)


def _required_string(value: str | None, *, field_name: str) -> str:
    """Достаёт обязательную непустую строку.

    Args:
        value: Строковое значение.
        field_name: Название поля для текста ошибки.

    Returns:
        Непустая строка.
    """
    if value is None:
        raise ValueError(f"{field_name} не может быть пустым.")

    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} не может быть пустым.")

    return normalized


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise ValueError(f"{model_name} должен быть сохранён в БД.")


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "SYNC_RAIDS_JOB_NAME",
    "ClashRaidSeasonsProvider",
    "RaidSeasonData",
    "SqlAlchemySyncRaidsRepository",
    "SyncRaidsJob",
    "SyncRaidsJobResult",
    "SyncRaidsRepository",
    "register_sync_raids_job",
]
