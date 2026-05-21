"""Worker job обнаружения нарушений по рейдам столицы."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import (
    Clan,
    ClanMemberSnapshot,
    CwlSeason,
    PlayerAccount,
    RaidMember,
    RaidSeason,
    TelegramUser,
)
from app.domain import ClanType, RaidMemberStatus, WarningReasonCode, build_raid_warning_event_key
from app.services import WarningAffectedAccount, WarningCreationResult, WarningCreationService
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

DETECT_RAID_VIOLATIONS_JOB_NAME = "detect_raid_violations"

_MAIN_CLAN_TYPE = ClanType.MAIN.value
_NON_SANCTION_CLAN_TYPES = frozenset({ClanType.ACADEMY.value, ClanType.FREEZER.value})
_RAID_VIOLATION_STATUSES = frozenset(
    {
        RaidMemberStatus.RAID_MISSED.value,
        RaidMemberStatus.RAID_INCOMPLETE.value,
    }
)


@dataclass(frozen=True, slots=True)
class RaidViolationMember:
    """Участник рейда, потенциально нарушивший правило 6 атак."""

    clan: Clan
    raid_season: RaidSeason
    telegram_user: TelegramUser
    player_tag: str
    player_name: str
    status: str
    created_cwl_season_key: str | None


@dataclass(slots=True)
class _RaidViolationGroupState:
    """Внутреннее состояние группировки raid-нарушений."""

    clan: Clan
    raid_season: RaidSeason
    telegram_user: TelegramUser
    reason_code: WarningReasonCode
    affected_accounts: list[WarningAffectedAccount]
    created_cwl_season_key: str | None


class RaidViolationWarningCreator(Protocol):
    """Contract сервиса создания warn за рейдовые нарушения."""

    async def create_system_warning(
        self,
        *,
        telegram_user: TelegramUser,
        reason_code: WarningReasonCode | str,
        event_key: str,
        affected_accounts: list[WarningAffectedAccount],
        clan: Clan | None = None,
        comment: str | None = None,
        created_cwl_season_key: str | None = None,
    ) -> WarningCreationResult:
        """Создаёт system warn.

        Args:
            telegram_user: TelegramUser, которому создаётся warn.
            reason_code: Причина system warn.
            event_key: Идемпотентный ключ события.
            affected_accounts: Затронутые игровые аккаунты.
            clan: Клан контекста warn.
            comment: Опциональный комментарий.
            created_cwl_season_key: CWL season key для будущего expiration.

        Returns:
            Результат создания warn.
        """


class DetectRaidViolationsRepository(Protocol):
    """Repository contract для detect raid violations job."""

    async def list_raid_violation_members(
        self,
        *,
        observed_at: datetime,
    ) -> tuple[RaidViolationMember, ...]:
        """Возвращает участников рейдов с возможными нарушениями.

        Args:
            observed_at: Время текущей проверки.

        Returns:
            Tuple участников рейдов.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyDetectRaidViolationsRepository:
    """SQLAlchemy repository для detect raid violations job."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_raid_violation_members(
        self,
        *,
        observed_at: datetime,
    ) -> tuple[RaidViolationMember, ...]:
        """Возвращает linked members с raid_missed/raid_incomplete в main-кланах.

        Args:
            observed_at: Время текущей проверки.

        Returns:
            Tuple участников с потенциальными нарушениями.
        """
        current_snapshot = aliased(ClanMemberSnapshot)
        current_clan = aliased(Clan)
        non_sanction_presence_exists = (
            select(current_snapshot.id)
            .join(current_clan, current_clan.id == current_snapshot.clan_id)
            .where(
                current_snapshot.player_tag == RaidMember.player_tag,
                current_snapshot.is_current.is_(True),
                current_clan.is_active.is_(True),
                current_clan.type.in_(sorted(_NON_SANCTION_CLAN_TYPES)),
            )
            .exists()
        )
        latest_cwl_season_key = (
            select(CwlSeason.season)
            .where(CwlSeason.clan_id == Clan.id)
            .order_by(CwlSeason.started_at.desc(), CwlSeason.id.desc())
            .limit(1)
            .scalar_subquery()
        )

        result = await self._session.execute(
            select(
                RaidSeason,
                Clan,
                RaidMember,
                PlayerAccount,
                TelegramUser,
                latest_cwl_season_key.label("created_cwl_season_key"),
            )
            .join(Clan, Clan.id == RaidSeason.clan_id)
            .join(RaidMember, RaidMember.raid_season_id == RaidSeason.id)
            .join(PlayerAccount, PlayerAccount.player_tag == RaidMember.player_tag)
            .join(TelegramUser, TelegramUser.id == PlayerAccount.telegram_user_id)
            .where(
                Clan.is_active.is_(True),
                Clan.type == _MAIN_CLAN_TYPE,
                RaidSeason.end_time <= observed_at,
                RaidMember.status.in_(sorted(_RAID_VIOLATION_STATUSES)),
                PlayerAccount.is_active.is_(True),
                PlayerAccount.telegram_user_id.is_not(None),
                ~non_sanction_presence_exists,
            )
            .order_by(RaidSeason.id, TelegramUser.id, RaidMember.player_tag)
        )

        return tuple(
            RaidViolationMember(
                clan=clan,
                raid_season=raid_season,
                telegram_user=telegram_user,
                player_tag=raid_member.player_tag,
                player_name=raid_member.name,
                status=raid_member.status,
                created_cwl_season_key=created_cwl_season_key,
            )
            for (
                raid_season,
                clan,
                raid_member,
                _player_account,
                telegram_user,
                created_cwl_season_key,
            ) in result.all()
        )

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class DetectRaidViolationsJobResult:
    """Результат одного запуска detect raid violations job."""

    discovered_count: int
    grouped_count: int
    created_count: int
    existing_count: int
    skipped_not_ready_count: int
    skipped_non_main_count: int
    skipped_without_violation_count: int
    skipped_without_affected_accounts_count: int


class DetectRaidViolationsJob:
    """Job создания system warn за нарушения рейдов столицы."""

    def __init__(
        self,
        *,
        repository: DetectRaidViolationsRepository,
        warning_creator: RaidViolationWarningCreator,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository участников рейдов с нарушениями.
            warning_creator: Сервис создания system warn.
        """
        self._repository = repository
        self._warning_creator = warning_creator

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "DetectRaidViolationsJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemyDetectRaidViolationsRepository(session),
            warning_creator=WarningCreationService.from_session(session=session),
        )

    async def run(self, context: WorkerJobContext) -> DetectRaidViolationsJobResult:
        """Создаёт raid_missed/raid_incomplete warn после окончания raid season.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        observed_at = _utc_now()
        violation_members = await self._repository.list_raid_violation_members(
            observed_at=observed_at
        )

        skipped_not_ready_count = 0
        skipped_non_main_count = 0
        skipped_without_violation_count = 0
        skipped_without_affected_accounts_count = 0
        created_count = 0
        existing_count = 0
        grouped: dict[tuple[int, int], _RaidViolationGroupState] = {}

        for member in violation_members:
            if context.should_stop:
                break

            if member.clan.type != _MAIN_CLAN_TYPE:
                skipped_non_main_count += 1
                continue

            if member.raid_season.end_time > observed_at:
                skipped_not_ready_count += 1
                continue

            reason_code = _reason_code_from_raid_status(member.status)
            if reason_code is None:
                skipped_without_violation_count += 1
                continue

            group_key = (
                _required_model_id(member.raid_season, model_name="RaidSeason"),
                _required_model_id(member.telegram_user, model_name="TelegramUser"),
            )
            affected_account = WarningAffectedAccount(
                player_tag=member.player_tag,
                player_name=member.player_name,
            )

            if group_key not in grouped:
                grouped[group_key] = _RaidViolationGroupState(
                    clan=member.clan,
                    raid_season=member.raid_season,
                    telegram_user=member.telegram_user,
                    reason_code=reason_code,
                    affected_accounts=[affected_account],
                    created_cwl_season_key=member.created_cwl_season_key,
                )
                continue

            state = grouped[group_key]
            state.reason_code = _worst_raid_reason(
                current_reason=state.reason_code,
                new_reason=reason_code,
            )
            state.affected_accounts.append(affected_account)

        for state in grouped.values():
            if context.should_stop:
                break

            if not state.affected_accounts:
                skipped_without_affected_accounts_count += 1
                continue

            event_key = build_raid_warning_event_key(
                reason_code=state.reason_code,
                clan_tag=state.clan.tag,
                raid_start_time=state.raid_season.start_time,
                telegram_user_id=_required_model_id(
                    state.telegram_user,
                    model_name="TelegramUser",
                ),
            )
            result = await self._warning_creator.create_system_warning(
                telegram_user=state.telegram_user,
                reason_code=state.reason_code,
                event_key=event_key,
                affected_accounts=state.affected_accounts,
                clan=state.clan,
                created_cwl_season_key=state.created_cwl_season_key,
            )

            if result.created:
                created_count += 1
            else:
                existing_count += 1

        await self._repository.flush()

        return DetectRaidViolationsJobResult(
            discovered_count=len(violation_members),
            grouped_count=len(grouped),
            created_count=created_count,
            existing_count=existing_count,
            skipped_not_ready_count=skipped_not_ready_count,
            skipped_non_main_count=skipped_non_main_count,
            skipped_without_violation_count=skipped_without_violation_count,
            skipped_without_affected_accounts_count=skipped_without_affected_accounts_count,
        )


def register_detect_raid_violations_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует detect raid violations job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=DETECT_RAID_VIOLATIONS_JOB_NAME)
    async def detect_raid_violations(context: WorkerJobContext) -> None:
        """Запускает обнаружение raid-нарушений внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("detect_raid_violations требует DB session.")

        job = DetectRaidViolationsJob.from_session(session=context.session)
        await job.run(context)


def _reason_code_from_raid_status(status: str) -> WarningReasonCode | None:
    """Возвращает warn reason по статусу участника рейдов.

    Args:
        status: Статус `RaidMember.status`.

    Returns:
        Reason code или `None`, если warn не нужен.
    """
    if status == RaidMemberStatus.RAID_MISSED.value:
        return WarningReasonCode.RAID_MISSED

    if status == RaidMemberStatus.RAID_INCOMPLETE.value:
        return WarningReasonCode.RAID_INCOMPLETE

    return None


def _worst_raid_reason(
    *,
    current_reason: WarningReasonCode,
    new_reason: WarningReasonCode,
) -> WarningReasonCode:
    """Выбирает худшую raid-причину для одного TelegramUser.

    Args:
        current_reason: Текущая причина группы.
        new_reason: Новая причина из другого аккаунта.

    Returns:
        Худшая причина: raid_missed приоритетнее raid_incomplete.
    """
    if WarningReasonCode.RAID_MISSED in {current_reason, new_reason}:
        return WarningReasonCode.RAID_MISSED

    return WarningReasonCode.RAID_INCOMPLETE


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
    "DETECT_RAID_VIOLATIONS_JOB_NAME",
    "DetectRaidViolationsJob",
    "DetectRaidViolationsJobResult",
    "DetectRaidViolationsRepository",
    "RaidViolationMember",
    "RaidViolationWarningCreator",
    "SqlAlchemyDetectRaidViolationsRepository",
    "register_detect_raid_violations_job",
]
