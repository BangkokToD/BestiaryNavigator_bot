"""Worker job синхронизации Лиги войн кланов."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiError, Clan, CwlSeason, CwlWar
from app.domain import normalize_clan_tag
from app.integrations.clash import (
    ClashApiClient,
    ClashApiError,
    ClashCwlLeagueGroup,
    ClashCwlWar,
    ClashNotFoundError,
    ClashWarSideSummary,
    map_clash_api_error_to_context,
)
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

SYNC_CWL_JOB_NAME = "sync_cwl"

_CWL_GROUP_ENTITY_TYPE = "cwl_league_group"
_CWL_WAR_ENTITY_TYPE = "cwl_war"
_PLACEHOLDER_WAR_TAGS = {"#0", "0"}
_ENDED_STATES = {"ended", "warended"}


class ClashCwlProvider(Protocol):
    """Contract Clash API provider для синхронизации ЛВК."""

    async def get_cwl_league_group(self, clan_tag: str) -> ClashCwlLeagueGroup:
        """Получает текущую группу ЛВК клана.

        Args:
            clan_tag: Нормализованный тег клана.

        Returns:
            DTO группы ЛВК.
        """

    async def get_cwl_war(self, war_tag: str) -> ClashCwlWar:
        """Получает конкретную войну ЛВК.

        Args:
            war_tag: War tag из League Group.

        Returns:
            DTO конкретной войны ЛВК.
        """


class SyncCwlRepository(Protocol):
    """Repository contract для sync cwl job."""

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает кланы, для которых включён мониторинг.

        Returns:
            Tuple active-кланов.
        """

    async def get_cwl_season(self, *, clan_id: int, season: str) -> CwlSeason | None:
        """Возвращает CWL season для клана.

        Args:
            clan_id: DB ID клана.
            season: Ключ сезона из Clash API.

        Returns:
            Сезон ЛВК или `None`.
        """

    def add_cwl_season(self, cwl_season: CwlSeason) -> None:
        """Добавляет CWL season в unit of work.

        Args:
            cwl_season: Новая модель сезона.
        """

    async def get_cwl_war_by_tag(self, war_tag: str) -> CwlWar | None:
        """Возвращает CWL war по уникальному war tag.

        Args:
            war_tag: War tag из Clash API.

        Returns:
            Модель CWL war или `None`.
        """

    def add_cwl_war(self, cwl_war: CwlWar) -> None:
        """Добавляет CWL war в unit of work.

        Args:
            cwl_war: Новая модель войны ЛВК.
        """

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в unit of work.

        Args:
            api_error: Модель ошибки внешнего API.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemySyncCwlRepository:
    """SQLAlchemy repository для sync cwl job."""

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
            select(Clan).where(Clan.is_active.is_(True)).order_by(Clan.id)
        )
        return tuple(result.scalars().all())

    async def get_cwl_season(self, *, clan_id: int, season: str) -> CwlSeason | None:
        """Возвращает CWL season клана.

        Args:
            clan_id: DB ID клана.
            season: Ключ сезона.

        Returns:
            Модель сезона или `None`.
        """
        result = await self._session.execute(
            select(CwlSeason).where(
                CwlSeason.clan_id == clan_id,
                CwlSeason.season == season,
            )
        )
        return result.scalar_one_or_none()

    def add_cwl_season(self, cwl_season: CwlSeason) -> None:
        """Добавляет CWL season в текущую session.

        Args:
            cwl_season: Новая модель сезона.
        """
        self._session.add(cwl_season)

    async def get_cwl_war_by_tag(self, war_tag: str) -> CwlWar | None:
        """Возвращает CWL war по unique war tag.

        Args:
            war_tag: War tag из Clash API.

        Returns:
            Модель войны или `None`.
        """
        result = await self._session.execute(select(CwlWar).where(CwlWar.war_tag == war_tag))
        return result.scalar_one_or_none()

    def add_cwl_war(self, cwl_war: CwlWar) -> None:
        """Добавляет CWL war в текущую session.

        Args:
            cwl_war: Новая модель войны ЛВК.
        """
        self._session.add(cwl_war)

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
class CwlWarData:
    """Нормализованные данные конкретной войны ЛВК."""

    cwl_season_id: int
    round_number: int
    war_tag: str
    state: str
    our_clan_tag: str
    opponent_clan_tag: str
    start_time: datetime
    end_time: datetime
    our_stars: int
    opponent_stars: int
    our_destruction: Decimal
    opponent_destruction: Decimal


@dataclass(frozen=True, slots=True)
class SyncCwlJobResult:
    """Результат одного запуска sync cwl job."""

    discovered_count: int
    synced_group_count: int
    no_cwl_count: int
    failed_group_count: int
    failed_war_count: int
    skipped_inactive_count: int
    skipped_placeholder_war_count: int
    skipped_unrelated_war_count: int
    created_season_count: int
    updated_season_count: int
    created_war_count: int
    updated_war_count: int


class SyncCwlJob:
    """Job синхронизации ЛВК active-кланов из Clash API."""

    def __init__(
        self,
        *,
        repository: SyncCwlRepository,
        clash_client: ClashCwlProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository для ЛВК и ошибок API.
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
        clash_client: ClashCwlProvider,
    ) -> "SyncCwlJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            clash_client: Clash API client или совместимый provider.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemySyncCwlRepository(session),
            clash_client=clash_client,
        )

    async def run(self, context: WorkerJobContext) -> SyncCwlJobResult:
        """Синхронизирует League Group и войны ЛВК.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        clans = await self._repository.list_active_clans()

        synced_group_count = 0
        no_cwl_count = 0
        failed_group_count = 0
        failed_war_count = 0
        skipped_inactive_count = 0
        skipped_placeholder_war_count = 0
        skipped_unrelated_war_count = 0
        created_season_count = 0
        updated_season_count = 0
        created_war_count = 0
        updated_war_count = 0

        for clan in clans:
            if context.should_stop:
                break

            if not clan.is_active:
                skipped_inactive_count += 1
                continue

            try:
                league_group = await self._clash_client.get_cwl_league_group(clan.tag)
            except ClashNotFoundError:
                no_cwl_count += 1
                continue
            except ClashApiError as error:
                self._handle_clash_api_error(
                    entity_type=_CWL_GROUP_ENTITY_TYPE,
                    entity_tag=clan.tag,
                    error=error,
                    worker_name=context.job_name,
                )
                failed_group_count += 1
                continue

            observed_at = self._clock()
            cwl_season, is_created = await self._get_or_create_cwl_season(
                clan=clan,
                league_group=league_group,
                observed_at=observed_at,
            )
            if is_created:
                created_season_count += 1
                await self._repository.flush()
            else:
                updated_season_count += 1

            for round_number, war_tag in _iter_round_war_tags(league_group):
                if context.should_stop:
                    break

                if not _is_real_war_tag(war_tag):
                    skipped_placeholder_war_count += 1
                    continue

                try:
                    cwl_war = await self._clash_client.get_cwl_war(war_tag)
                except ClashApiError as error:
                    self._handle_clash_api_error(
                        entity_type=_CWL_WAR_ENTITY_TYPE,
                        entity_tag=war_tag,
                        error=error,
                        worker_name=context.job_name,
                    )
                    failed_war_count += 1
                    continue

                cwl_war_data = _build_cwl_war_data(
                    cwl_season=cwl_season,
                    tracked_clan_tag=clan.tag,
                    round_number=round_number,
                    requested_war_tag=war_tag,
                    cwl_war=cwl_war,
                )
                if cwl_war_data is None:
                    skipped_unrelated_war_count += 1
                    continue

                existing_war = await self._repository.get_cwl_war_by_tag(cwl_war_data.war_tag)
                if existing_war is None:
                    self._repository.add_cwl_war(_build_cwl_war(cwl_war_data))
                    created_war_count += 1
                else:
                    _apply_cwl_war_update(existing_war, cwl_war_data)
                    updated_war_count += 1

            synced_group_count += 1

        await self._repository.flush()

        return SyncCwlJobResult(
            discovered_count=len(clans),
            synced_group_count=synced_group_count,
            no_cwl_count=no_cwl_count,
            failed_group_count=failed_group_count,
            failed_war_count=failed_war_count,
            skipped_inactive_count=skipped_inactive_count,
            skipped_placeholder_war_count=skipped_placeholder_war_count,
            skipped_unrelated_war_count=skipped_unrelated_war_count,
            created_season_count=created_season_count,
            updated_season_count=updated_season_count,
            created_war_count=created_war_count,
            updated_war_count=updated_war_count,
        )

    async def _get_or_create_cwl_season(
        self,
        *,
        clan: Clan,
        league_group: ClashCwlLeagueGroup,
        observed_at: datetime,
    ) -> tuple[CwlSeason, bool]:
        """Возвращает существующий сезон ЛВК или создаёт новый.

        Args:
            clan: Отслеживаемый клан.
            league_group: DTO League Group.
            observed_at: Время обнаружения.

        Returns:
            Tuple `(season, is_created)`.
        """
        clan_id = _required_model_id(clan, model_name="Clan")
        cwl_season = await self._repository.get_cwl_season(
            clan_id=clan_id,
            season=league_group.season,
        )

        if cwl_season is None:
            cwl_season = CwlSeason(
                clan_id=clan_id,
                season=league_group.season,
                state=league_group.state,
                started_at=observed_at,
                ended_at=observed_at if _is_ended_state(league_group.state) else None,
            )
            self._repository.add_cwl_season(cwl_season)
            return cwl_season, True

        cwl_season.state = league_group.state
        if _is_ended_state(league_group.state) and cwl_season.ended_at is None:
            cwl_season.ended_at = observed_at

        return cwl_season, False

    def _handle_clash_api_error(
        self,
        *,
        entity_type: str,
        entity_tag: str,
        error: ClashApiError,
        worker_name: str,
    ) -> None:
        """Обрабатывает typed ошибку Clash API.

        Args:
            entity_type: Тип сущности для `api_errors`.
            entity_tag: Тег сущности.
            error: Typed Clash API exception.
            worker_name: Имя текущей worker job для debug-контекста.
        """
        error_context = map_clash_api_error_to_context(
            error,
            entity_type=entity_type,
            entity_tag=entity_tag,
            worker_name=worker_name,
            retry_count=0,
        )
        self._repository.add_api_error(ApiError(**error_context.to_api_error_values()))


def register_sync_cwl_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует sync cwl job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=SYNC_CWL_JOB_NAME)
    async def sync_cwl(context: WorkerJobContext) -> None:
        """Запускает синхронизацию ЛВК внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("sync_cwl требует DB session.")

        async with ClashApiClient.from_settings() as clash_client:
            job = SyncCwlJob.from_session(
                session=context.session,
                clash_client=clash_client,
            )
            await job.run(context)


def _iter_round_war_tags(league_group: ClashCwlLeagueGroup) -> tuple[tuple[int, str], ...]:
    """Возвращает пары round_number/war_tag из League Group.

    Args:
        league_group: DTO League Group.

    Returns:
        Tuple пар `(round_number, war_tag)`.
    """
    result: list[tuple[int, str]] = []

    for round_index, war_tags in enumerate(league_group.rounds, start=1):
        for war_tag in war_tags:
            result.append((round_index, war_tag))

    return tuple(result)


def _is_real_war_tag(war_tag: str) -> bool:
    """Проверяет, является ли war tag реальной войной.

    Args:
        war_tag: War tag из League Group.

    Returns:
        `True`, если это не placeholder.
    """
    normalized = war_tag.strip().upper()
    return bool(normalized) and normalized not in _PLACEHOLDER_WAR_TAGS


def _build_cwl_war_data(
    *,
    cwl_season: CwlSeason,
    tracked_clan_tag: str,
    round_number: int,
    requested_war_tag: str,
    cwl_war: ClashCwlWar,
) -> CwlWarData | None:
    """Собирает данные конкретной CWL-war для tracked clan.

    Args:
        cwl_season: Сезон ЛВК tracked clan.
        tracked_clan_tag: Тег tracked clan.
        round_number: Номер раунда из League Group.
        requested_war_tag: War tag, по которому был сделан запрос.
        cwl_war: DTO конкретной войны ЛВК.

    Returns:
        Данные войны или `None`, если tracked clan в этой войне не участвует.
    """
    tracked_tag = normalize_clan_tag(tracked_clan_tag)
    sides = _select_tracked_sides(cwl_war, tracked_clan_tag=tracked_tag)
    if sides is None:
        return None

    our_side, opponent_side = sides
    opponent_tag = _required_string(opponent_side.tag, field_name="opponent.tag")
    war_tag = normalize_clan_tag(cwl_war.tag or requested_war_tag)

    return CwlWarData(
        cwl_season_id=_required_model_id(cwl_season, model_name="CwlSeason"),
        round_number=_required_positive_int(round_number, field_name="round_number"),
        war_tag=war_tag,
        state=cwl_war.state,
        our_clan_tag=tracked_tag,
        opponent_clan_tag=opponent_tag,
        start_time=_parse_clash_datetime(cwl_war.start_time, field_name="startTime"),
        end_time=_parse_clash_datetime(cwl_war.end_time, field_name="endTime"),
        our_stars=our_side.stars or 0,
        opponent_stars=opponent_side.stars or 0,
        our_destruction=_decimal_from_optional(our_side.destruction_percentage),
        opponent_destruction=_decimal_from_optional(opponent_side.destruction_percentage),
    )


def _select_tracked_sides(
    cwl_war: ClashCwlWar,
    *,
    tracked_clan_tag: str,
) -> tuple[ClashWarSideSummary, ClashWarSideSummary] | None:
    """Выбирает нашу и вражескую стороны CWL-war.

    Args:
        cwl_war: DTO войны ЛВК.
        tracked_clan_tag: Нормализованный тег tracked clan.

    Returns:
        Tuple `(our_side, opponent_side)` или `None`, если tracked clan не участвует.

    Raises:
        ValueError: Если tracked clan найден, но opponent side отсутствует.
    """
    if cwl_war.clan is not None and cwl_war.clan.tag == tracked_clan_tag:
        if cwl_war.opponent is None:
            raise ValueError("CWL war должен содержать opponent side.")
        return cwl_war.clan, cwl_war.opponent

    if cwl_war.opponent is not None and cwl_war.opponent.tag == tracked_clan_tag:
        if cwl_war.clan is None:
            raise ValueError("CWL war должен содержать clan side.")
        return cwl_war.opponent, cwl_war.clan

    return None


def _build_cwl_war(cwl_war_data: CwlWarData) -> CwlWar:
    """Создаёт модель CWL war.

    Args:
        cwl_war_data: Нормализованные данные войны.

    Returns:
        Новая модель `CwlWar`.
    """
    return CwlWar(
        cwl_season_id=cwl_war_data.cwl_season_id,
        round_number=cwl_war_data.round_number,
        war_tag=cwl_war_data.war_tag,
        state=cwl_war_data.state,
        our_clan_tag=cwl_war_data.our_clan_tag,
        opponent_clan_tag=cwl_war_data.opponent_clan_tag,
        start_time=cwl_war_data.start_time,
        end_time=cwl_war_data.end_time,
        our_stars=cwl_war_data.our_stars,
        opponent_stars=cwl_war_data.opponent_stars,
        our_destruction=cwl_war_data.our_destruction,
        opponent_destruction=cwl_war_data.opponent_destruction,
    )


def _apply_cwl_war_update(cwl_war: CwlWar, cwl_war_data: CwlWarData) -> None:
    """Обновляет существующую CWL war.

    Args:
        cwl_war: Существующая модель войны ЛВК.
        cwl_war_data: Актуальные данные войны.
    """
    cwl_war.cwl_season_id = cwl_war_data.cwl_season_id
    cwl_war.round_number = cwl_war_data.round_number
    cwl_war.state = cwl_war_data.state
    cwl_war.our_clan_tag = cwl_war_data.our_clan_tag
    cwl_war.opponent_clan_tag = cwl_war_data.opponent_clan_tag
    cwl_war.start_time = cwl_war_data.start_time
    cwl_war.end_time = cwl_war_data.end_time
    cwl_war.our_stars = cwl_war_data.our_stars
    cwl_war.opponent_stars = cwl_war_data.opponent_stars
    cwl_war.our_destruction = cwl_war_data.our_destruction
    cwl_war.opponent_destruction = cwl_war_data.opponent_destruction


def _is_ended_state(value: str) -> bool:
    """Проверяет ended-state League Group.

    Args:
        value: Состояние из Clash API.

    Returns:
        `True`, если состояние означает завершение.
    """
    return value.replace("_", "").replace("-", "").strip().lower() in _ENDED_STATES


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


def _required_positive_int(value: int | None, *, field_name: str) -> int:
    """Достаёт обязательное положительное целое число.

    Args:
        value: Значение.
        field_name: Название поля для текста ошибки.

    Returns:
        Положительное число.
    """
    if value is None:
        raise ValueError(f"{field_name} не может быть пустым.")

    if value <= 0:
        raise ValueError(f"{field_name} должен быть больше 0.")

    return value


def _decimal_from_optional(value: float | int | None) -> Decimal:
    """Преобразует число API в Decimal.

    Args:
        value: Число из Clash API или `None`.

    Returns:
        Decimal-значение для SQLAlchemy Numeric.
    """
    if value is None:
        return Decimal("0")

    return Decimal(str(value))


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
    "SYNC_CWL_JOB_NAME",
    "ClashCwlProvider",
    "CwlWarData",
    "SqlAlchemySyncCwlRepository",
    "SyncCwlJob",
    "SyncCwlJobResult",
    "SyncCwlRepository",
    "register_sync_cwl_job",
]
