"""Worker job синхронизации текущих обычных войн."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiError, Clan, WarAttack, WarMember, WarSnapshot
from app.domain import build_war_event_key
from app.integrations.clash import (
    ClashApiClient,
    ClashApiError,
    ClashCurrentWar,
    ClashWarSideSummary,
    map_clash_api_error_to_context,
)
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

SYNC_CURRENT_WARS_JOB_NAME = "sync_current_wars"

_WAR_ENTITY_TYPE = "current_war"
_OUR_SIDE = "our"
_OPPONENT_SIDE = "opponent"
_DEFAULT_ATTACKS_PER_MEMBER = 2


class ClashCurrentWarProvider(Protocol):
    """Contract Clash API provider для синхронизации текущих войн."""

    async def get_current_war(self, clan_tag: str) -> ClashCurrentWar | None:
        """Получает текущую войну клана.

        Args:
            clan_tag: Нормализованный тег клана.

        Returns:
            DTO текущей войны или `None`, если войны нет.
        """


class SyncCurrentWarsRepository(Protocol):
    """Repository contract для sync current wars job."""

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает кланы, для которых включён мониторинг.

        Returns:
            Tuple active-кланов.
        """

    async def get_war_snapshot_by_event_key(self, war_event_key: str) -> WarSnapshot | None:
        """Возвращает snapshot войны по стабильному event key.

        Args:
            war_event_key: Стабильный hash-key войны.

        Returns:
            Snapshot войны или `None`.
        """

    def add_war_snapshot(self, war_snapshot: WarSnapshot) -> None:
        """Добавляет snapshot войны в unit of work.

        Args:
            war_snapshot: Новый snapshot войны.
        """

    async def replace_war_children(
        self,
        *,
        war_snapshot: WarSnapshot,
        members: tuple[WarMember, ...],
        attacks: tuple[WarAttack, ...],
    ) -> None:
        """Заменяет дочерние записи состава и атак войны.

        Args:
            war_snapshot: Snapshot войны.
            members: Актуальные участники войны.
            attacks: Актуальные атаки войны.
        """

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в unit of work.

        Args:
            api_error: Модель ошибки внешнего API.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemySyncCurrentWarsRepository:
    """SQLAlchemy repository для sync current wars job."""

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

    async def get_war_snapshot_by_event_key(self, war_event_key: str) -> WarSnapshot | None:
        """Возвращает snapshot войны по event key.

        Args:
            war_event_key: Стабильный hash-key войны.

        Returns:
            Snapshot войны или `None`.
        """
        result = await self._session.execute(
            select(WarSnapshot).where(WarSnapshot.war_event_key == war_event_key)
        )
        return result.scalar_one_or_none()

    def add_war_snapshot(self, war_snapshot: WarSnapshot) -> None:
        """Добавляет snapshot войны в текущую session.

        Args:
            war_snapshot: Новый snapshot войны.
        """
        self._session.add(war_snapshot)

    async def replace_war_children(
        self,
        *,
        war_snapshot: WarSnapshot,
        members: tuple[WarMember, ...],
        attacks: tuple[WarAttack, ...],
    ) -> None:
        """Заменяет дочерние записи войны без каскадного удаления snapshot.

        Args:
            war_snapshot: Snapshot войны.
            members: Актуальные участники войны.
            attacks: Актуальные атаки войны.
        """
        await self._session.flush()
        war_snapshot_id = _required_model_id(war_snapshot, model_name="WarSnapshot")

        await self._session.execute(
            delete(WarAttack).where(WarAttack.war_snapshot_id == war_snapshot_id)
        )
        await self._session.execute(
            delete(WarMember).where(WarMember.war_snapshot_id == war_snapshot_id)
        )

        for member in members:
            member.war_snapshot_id = war_snapshot_id
            member.war_snapshot = war_snapshot
            self._session.add(member)

        for attack in attacks:
            attack.war_snapshot_id = war_snapshot_id
            attack.war_snapshot = war_snapshot
            self._session.add(attack)

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
class WarSnapshotData:
    """Нормализованные данные текущей войны для сохранения."""

    clan_id: int
    war_event_key: str
    state: str
    team_size: int
    attacks_per_member: int
    preparation_start_time: datetime
    start_time: datetime
    end_time: datetime
    opponent_tag: str
    opponent_name: str | None
    our_stars: int
    opponent_stars: int
    our_destruction: Decimal
    opponent_destruction: Decimal
    our_attacks: int
    opponent_attacks: int
    snapshot_at: datetime


@dataclass(frozen=True, slots=True)
class SyncCurrentWarsJobResult:
    """Результат одного запуска sync current wars job."""

    discovered_count: int
    synced_count: int
    no_war_count: int
    failed_count: int
    skipped_inactive_count: int
    created_count: int
    updated_count: int
    member_count: int
    attack_count: int


class SyncCurrentWarsJob:
    """Job синхронизации текущих обычных войн active-кланов."""

    def __init__(
        self,
        *,
        repository: SyncCurrentWarsRepository,
        clash_client: ClashCurrentWarProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository для войн и ошибок API.
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
        clash_client: ClashCurrentWarProvider,
    ) -> "SyncCurrentWarsJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            clash_client: Clash API client или совместимый provider.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemySyncCurrentWarsRepository(session),
            clash_client=clash_client,
        )

    async def run(self, context: WorkerJobContext) -> SyncCurrentWarsJobResult:
        """Синхронизирует текущие войны active-кланов.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        clans = await self._repository.list_active_clans()

        synced_count = 0
        no_war_count = 0
        failed_count = 0
        skipped_inactive_count = 0
        created_count = 0
        updated_count = 0
        member_count = 0
        attack_count = 0

        for clan in clans:
            if context.should_stop:
                break

            if not clan.is_active:
                skipped_inactive_count += 1
                continue

            try:
                current_war = await self._clash_client.get_current_war(clan.tag)
            except ClashApiError as error:
                self._handle_clash_api_error(
                    clan=clan,
                    error=error,
                    worker_name=context.job_name,
                )
                failed_count += 1
                continue

            if current_war is None or _is_no_current_war(current_war):
                no_war_count += 1
                continue

            snapshot_at = self._clock()
            snapshot_data = _build_war_snapshot_data(
                clan=clan,
                current_war=current_war,
                snapshot_at=snapshot_at,
            )
            war_snapshot = await self._repository.get_war_snapshot_by_event_key(
                snapshot_data.war_event_key
            )

            if war_snapshot is None:
                war_snapshot = _build_war_snapshot(snapshot_data)
                self._repository.add_war_snapshot(war_snapshot)
                created_count += 1
            else:
                _apply_war_snapshot_update(war_snapshot, snapshot_data)
                updated_count += 1

            members = _build_war_members(
                current_war,
                attacks_per_member=snapshot_data.attacks_per_member,
            )
            attacks = _build_war_attacks(current_war)
            await self._repository.replace_war_children(
                war_snapshot=war_snapshot,
                members=members,
                attacks=attacks,
            )

            synced_count += 1
            member_count += len(members)
            attack_count += len(attacks)

        await self._repository.flush()

        return SyncCurrentWarsJobResult(
            discovered_count=len(clans),
            synced_count=synced_count,
            no_war_count=no_war_count,
            failed_count=failed_count,
            skipped_inactive_count=skipped_inactive_count,
            created_count=created_count,
            updated_count=updated_count,
            member_count=member_count,
            attack_count=attack_count,
        )

    def _handle_clash_api_error(
        self,
        *,
        clan: Clan,
        error: ClashApiError,
        worker_name: str,
    ) -> None:
        """Обрабатывает typed ошибку Clash API для текущей войны клана.

        Args:
            clan: Клан, при синхронизации войны которого возникла ошибка.
            error: Typed Clash API exception.
            worker_name: Имя текущей worker job для debug-контекста.
        """
        error_context = map_clash_api_error_to_context(
            error,
            entity_type=_WAR_ENTITY_TYPE,
            entity_tag=clan.tag,
            worker_name=worker_name,
            retry_count=0,
        )
        self._repository.add_api_error(ApiError(**error_context.to_api_error_values()))


def register_sync_current_wars_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует sync current wars job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=SYNC_CURRENT_WARS_JOB_NAME)
    async def sync_current_wars(context: WorkerJobContext) -> None:
        """Запускает синхронизацию текущих войн внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("sync_current_wars требует DB session.")

        async with ClashApiClient.from_settings() as clash_client:
            job = SyncCurrentWarsJob.from_session(
                session=context.session,
                clash_client=clash_client,
            )
            await job.run(context)


def _is_no_current_war(current_war: ClashCurrentWar) -> bool:
    """Проверяет состояние отсутствия текущей войны.

    Args:
        current_war: DTO текущей войны.

    Returns:
        `True`, если API явно вернул состояние без войны.
    """
    return current_war.state.replace("_", "").replace("-", "").lower() == "notinwar"


def _build_war_snapshot_data(
    *,
    clan: Clan,
    current_war: ClashCurrentWar,
    snapshot_at: datetime,
) -> WarSnapshotData:
    """Собирает данные snapshot текущей войны.

    Args:
        clan: Отслеживаемый клан.
        current_war: DTO текущей войны.
        snapshot_at: Время синхронизации.

    Returns:
        Нормализованные данные snapshot войны.
    """
    clan_id = _required_model_id(clan, model_name="Clan")
    our_side = _required_side(current_war.clan, field_name="clan")
    opponent_side = _required_side(current_war.opponent, field_name="opponent")
    opponent_tag = _required_string(opponent_side.tag, field_name="opponent.tag")

    preparation_start_time = _parse_clash_datetime(
        current_war.preparation_start_time,
        field_name="preparationStartTime",
    )
    start_time = _parse_clash_datetime(current_war.start_time, field_name="startTime")
    end_time = _parse_clash_datetime(current_war.end_time, field_name="endTime")
    team_size = _required_int(current_war.team_size, field_name="teamSize")
    attacks_per_member = current_war.attacks_per_member or _DEFAULT_ATTACKS_PER_MEMBER

    war_event_key = build_war_event_key(
        clan_tag=clan.tag,
        opponent_tag=opponent_tag,
        preparation_start_time=preparation_start_time,
        start_time=start_time,
        end_time=end_time,
        team_size=team_size,
    )

    return WarSnapshotData(
        clan_id=clan_id,
        war_event_key=war_event_key,
        state=current_war.state,
        team_size=team_size,
        attacks_per_member=attacks_per_member,
        preparation_start_time=preparation_start_time,
        start_time=start_time,
        end_time=end_time,
        opponent_tag=opponent_tag,
        opponent_name=opponent_side.name,
        our_stars=our_side.stars or 0,
        opponent_stars=opponent_side.stars or 0,
        our_destruction=_decimal_from_optional(our_side.destruction_percentage),
        opponent_destruction=_decimal_from_optional(opponent_side.destruction_percentage),
        our_attacks=_side_attack_count(our_side),
        opponent_attacks=_side_attack_count(opponent_side),
        snapshot_at=snapshot_at,
    )


def _build_war_snapshot(snapshot_data: WarSnapshotData) -> WarSnapshot:
    """Создаёт модель snapshot войны.

    Args:
        snapshot_data: Нормализованные данные snapshot.

    Returns:
        Новая модель `WarSnapshot`.
    """
    return WarSnapshot(
        clan_id=snapshot_data.clan_id,
        war_tag=None,
        war_event_key=snapshot_data.war_event_key,
        state=snapshot_data.state,
        team_size=snapshot_data.team_size,
        attacks_per_member=snapshot_data.attacks_per_member,
        preparation_start_time=snapshot_data.preparation_start_time,
        start_time=snapshot_data.start_time,
        end_time=snapshot_data.end_time,
        opponent_tag=snapshot_data.opponent_tag,
        opponent_name=snapshot_data.opponent_name,
        our_stars=snapshot_data.our_stars,
        opponent_stars=snapshot_data.opponent_stars,
        our_destruction=snapshot_data.our_destruction,
        opponent_destruction=snapshot_data.opponent_destruction,
        our_attacks=snapshot_data.our_attacks,
        opponent_attacks=snapshot_data.opponent_attacks,
        snapshot_at=snapshot_data.snapshot_at,
    )


def _apply_war_snapshot_update(
    war_snapshot: WarSnapshot,
    snapshot_data: WarSnapshotData,
) -> None:
    """Обновляет существующий snapshot войны актуальными значениями.

    Args:
        war_snapshot: Существующий snapshot войны.
        snapshot_data: Нормализованные данные snapshot.
    """
    war_snapshot.state = snapshot_data.state
    war_snapshot.team_size = snapshot_data.team_size
    war_snapshot.attacks_per_member = snapshot_data.attacks_per_member
    war_snapshot.preparation_start_time = snapshot_data.preparation_start_time
    war_snapshot.start_time = snapshot_data.start_time
    war_snapshot.end_time = snapshot_data.end_time
    war_snapshot.opponent_tag = snapshot_data.opponent_tag
    war_snapshot.opponent_name = snapshot_data.opponent_name
    war_snapshot.our_stars = snapshot_data.our_stars
    war_snapshot.opponent_stars = snapshot_data.opponent_stars
    war_snapshot.our_destruction = snapshot_data.our_destruction
    war_snapshot.opponent_destruction = snapshot_data.opponent_destruction
    war_snapshot.our_attacks = snapshot_data.our_attacks
    war_snapshot.opponent_attacks = snapshot_data.opponent_attacks
    war_snapshot.snapshot_at = snapshot_data.snapshot_at


def _build_war_members(
    current_war: ClashCurrentWar,
    *,
    attacks_per_member: int,
) -> tuple[WarMember, ...]:
    """Создаёт участников войны для обеих сторон.

    Args:
        current_war: DTO текущей войны.
        attacks_per_member: Количество атак на участника.

    Returns:
        Tuple моделей `WarMember`.
    """
    members: list[WarMember] = []
    for side, side_summary in (
        (_OUR_SIDE, current_war.clan),
        (_OPPONENT_SIDE, current_war.opponent),
    ):
        if side_summary is None:
            continue

        for member in side_summary.members:
            attacks_done = len(member.attacks)
            members.append(
                WarMember(
                    side=side,
                    player_tag=member.player_tag,
                    name=member.name,
                    town_hall_level=member.town_hall_level,
                    map_position=member.map_position,
                    attacks_done=attacks_done,
                    attacks_left=max(attacks_per_member - attacks_done, 0),
                )
            )

    return tuple(members)


def _build_war_attacks(current_war: ClashCurrentWar) -> tuple[WarAttack, ...]:
    """Создаёт атаки войны для обеих сторон.

    Args:
        current_war: DTO текущей войны.

    Returns:
        Tuple моделей `WarAttack`.
    """
    attacks: list[WarAttack] = []
    for side_summary in (current_war.clan, current_war.opponent):
        if side_summary is None:
            continue

        for member in side_summary.members:
            for attack in member.attacks:
                attacks.append(
                    WarAttack(
                        attacker_tag=attack.attacker_tag,
                        defender_tag=attack.defender_tag,
                        stars=attack.stars or 0,
                        destruction_percentage=_decimal_from_optional(
                            attack.destruction_percentage
                        ),
                        duration=attack.duration,
                        order=_required_int(attack.order, field_name="attack.order"),
                    )
                )

    return tuple(sorted(attacks, key=lambda attack: attack.order))


def _side_attack_count(side_summary: ClashWarSideSummary) -> int:
    """Возвращает количество атак стороны войны.

    Args:
        side_summary: Сторона войны из Clash API.

    Returns:
        Количество использованных атак.
    """
    if side_summary.attacks is not None:
        return side_summary.attacks

    return sum(len(member.attacks) for member in side_summary.members)


def _parse_clash_datetime(value: str | None, *, field_name: str) -> datetime:
    """Парсит datetime из формата Clash API или ISO-строки.

    Args:
        value: Строка времени из Clash API.
        field_name: Название поля для текста ошибки.

    Returns:
        Timezone-aware UTC datetime.

    Raises:
        ValueError: Если значение отсутствует или формат не распознан.
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


def _required_side(
    value: ClashWarSideSummary | None,
    *,
    field_name: str,
) -> ClashWarSideSummary:
    """Достаёт обязательную сторону войны.

    Args:
        value: DTO стороны войны.
        field_name: Название поля для текста ошибки.

    Returns:
        DTO стороны войны.

    Raises:
        ValueError: Если сторона войны отсутствует.
    """
    if value is None:
        raise ValueError(f"Current war должен содержать сторону {field_name}.")

    return value


def _required_string(value: str | None, *, field_name: str) -> str:
    """Достаёт обязательную непустую строку.

    Args:
        value: Строковое значение.
        field_name: Название поля для текста ошибки.

    Returns:
        Непустая строка.

    Raises:
        ValueError: Если значение пустое.
    """
    if value is None:
        raise ValueError(f"{field_name} не может быть пустым.")

    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} не может быть пустым.")

    return normalized


def _required_int(value: int | None, *, field_name: str) -> int:
    """Достаёт обязательное целочисленное значение.

    Args:
        value: Значение.
        field_name: Название поля для текста ошибки.

    Returns:
        Целое число.

    Raises:
        ValueError: Если значение отсутствует или не положительное.
    """
    if value is None:
        raise ValueError(f"{field_name} не может быть пустым.")

    if value <= 0:
        raise ValueError(f"{field_name} должен быть больше 0.")

    return value


def _decimal_from_optional(value: float | int | None) -> Decimal:
    """Преобразует числовое значение API в Decimal.

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

    Raises:
        ValueError: Если id отсутствует.
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
    "SYNC_CURRENT_WARS_JOB_NAME",
    "ClashCurrentWarProvider",
    "SqlAlchemySyncCurrentWarsRepository",
    "SyncCurrentWarsJob",
    "SyncCurrentWarsJobResult",
    "SyncCurrentWarsRepository",
    "WarSnapshotData",
    "register_sync_current_wars_job",
]
