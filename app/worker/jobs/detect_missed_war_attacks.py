"""Worker job обнаружения пропущенных атак в обычной КВ."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import (
    Clan,
    ClanMemberSnapshot,
    CwlSeason,
    PlayerAccount,
    TelegramUser,
    WarMember,
    WarSnapshot,
)
from app.domain import ClanType, WarningReasonCode, build_war_warning_event_key
from app.services import WarningAffectedAccount, WarningCreationResult, WarningCreationService
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

DETECT_MISSED_WAR_ATTACKS_JOB_NAME = "detect_missed_war_attacks"

_WAR_DETECTION_DELAY = timedelta(minutes=5)
_OUR_SIDE = "our"
_MAIN_CLAN_TYPE = ClanType.MAIN.value
_NON_SANCTION_CLAN_TYPES = frozenset({ClanType.ACADEMY.value, ClanType.FREEZER.value})


@dataclass(frozen=True, slots=True)
class MissedWarAttackCandidate:
    """Кандидат на system warn за пропуск атак в обычной КВ."""

    clan: Clan
    war_snapshot: WarSnapshot
    telegram_user: TelegramUser
    affected_accounts: tuple[WarningAffectedAccount, ...]
    created_cwl_season_key: str | None


class WarAttackWarningCreator(Protocol):
    """Contract сервиса создания warn за пропущенную КВ."""

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


class DetectMissedWarAttacksRepository(Protocol):
    """Repository contract для detect missed war attacks job."""

    async def list_missed_war_attack_candidates(
        self,
        *,
        observed_at: datetime,
    ) -> tuple[MissedWarAttackCandidate, ...]:
        """Возвращает кандидатов на warn за пропущенные атаки КВ.

        Args:
            observed_at: Время текущей проверки.

        Returns:
            Tuple кандидатов на system warn.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyDetectMissedWarAttacksRepository:
    """SQLAlchemy repository для detect missed war attacks job."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_missed_war_attack_candidates(
        self,
        *,
        observed_at: datetime,
    ) -> tuple[MissedWarAttackCandidate, ...]:
        """Возвращает сгруппированные пропуски атак в main-КВ.

        Args:
            observed_at: Время текущей проверки.

        Returns:
            Tuple кандидатов на system warn.
        """
        current_snapshot = aliased(ClanMemberSnapshot)
        current_clan = aliased(Clan)
        non_sanction_presence_exists = (
            select(current_snapshot.id)
            .join(current_clan, current_clan.id == current_snapshot.clan_id)
            .where(
                current_snapshot.player_tag == WarMember.player_tag,
                current_snapshot.is_current.is_(True),
                current_clan.is_active.is_(True),
                current_clan.type.in_(sorted(_NON_SANCTION_CLAN_TYPES)),
            )
            .exists()
        )
        result = await self._session.execute(
            select(WarSnapshot, Clan, TelegramUser, WarMember)
            .join(Clan, Clan.id == WarSnapshot.clan_id)
            .join(WarMember, WarMember.war_snapshot_id == WarSnapshot.id)
            .join(PlayerAccount, PlayerAccount.player_tag == WarMember.player_tag)
            .join(TelegramUser, TelegramUser.id == PlayerAccount.telegram_user_id)
            .where(
                Clan.is_active.is_(True),
                Clan.type == _MAIN_CLAN_TYPE,
                WarSnapshot.end_time <= observed_at - _WAR_DETECTION_DELAY,
                WarMember.side == _OUR_SIDE,
                WarMember.attacks_left > 0,
                PlayerAccount.is_active.is_(True),
                PlayerAccount.telegram_user_id.is_not(None),
                ~non_sanction_presence_exists,
            )
            .order_by(WarSnapshot.id, TelegramUser.id, WarMember.player_tag)
        )

        grouped_candidates: dict[tuple[int, int], MissedWarAttackCandidate] = {}
        affected_accounts_by_key: dict[tuple[int, int], list[WarningAffectedAccount]] = {}

        for war_snapshot, clan, telegram_user, war_member in result.all():
            war_snapshot_id = _required_model_id(war_snapshot, model_name="WarSnapshot")
            telegram_user_id = _required_model_id(telegram_user, model_name="TelegramUser")
            group_key = (war_snapshot_id, telegram_user_id)

            if group_key not in grouped_candidates:
                grouped_candidates[group_key] = MissedWarAttackCandidate(
                    clan=clan,
                    war_snapshot=war_snapshot,
                    telegram_user=telegram_user,
                    affected_accounts=(),
                    created_cwl_season_key=await self._get_latest_cwl_season_key(
                        clan_id=_required_model_id(clan, model_name="Clan")
                    ),
                )
                affected_accounts_by_key[group_key] = []

            affected_accounts_by_key[group_key].append(
                WarningAffectedAccount(
                    player_tag=war_member.player_tag,
                    player_name=war_member.name,
                )
            )

        return tuple(
            MissedWarAttackCandidate(
                clan=candidate.clan,
                war_snapshot=candidate.war_snapshot,
                telegram_user=candidate.telegram_user,
                affected_accounts=tuple(affected_accounts_by_key[group_key]),
                created_cwl_season_key=candidate.created_cwl_season_key,
            )
            for group_key, candidate in grouped_candidates.items()
        )

    async def _get_latest_cwl_season_key(self, *, clan_id: int) -> str | None:
        """Возвращает последний известный CWL season key клана.

        Args:
            clan_id: DB ID клана.

        Returns:
            Последний `season` или `None`.
        """
        result = await self._session.execute(
            select(CwlSeason.season)
            .where(CwlSeason.clan_id == clan_id)
            .order_by(CwlSeason.started_at.desc(), CwlSeason.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class DetectMissedWarAttacksJobResult:
    """Результат одного запуска detect missed war attacks job."""

    discovered_count: int
    created_count: int
    existing_count: int
    skipped_not_ready_count: int
    skipped_non_main_count: int
    skipped_without_affected_accounts_count: int


class DetectMissedWarAttacksJob:
    """Job создания system warn за пропущенные атаки обычной КВ."""

    def __init__(
        self,
        *,
        repository: DetectMissedWarAttacksRepository,
        warning_creator: WarAttackWarningCreator,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository кандидатов по пропущенным атакам.
            warning_creator: Сервис создания system warn.
            clock: Источник текущего времени для тестов.
        """
        self._repository = repository
        self._warning_creator = warning_creator
        self._clock = clock or _utc_now

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "DetectMissedWarAttacksJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemyDetectMissedWarAttacksRepository(session),
            warning_creator=WarningCreationService.from_session(session=session),
        )

    async def run(self, context: WorkerJobContext) -> DetectMissedWarAttacksJobResult:
        """Создаёт warn за пропущенные атаки КВ.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        observed_at = self._clock()
        candidates = await self._repository.list_missed_war_attack_candidates(
            observed_at=observed_at
        )

        created_count = 0
        existing_count = 0
        skipped_not_ready_count = 0
        skipped_non_main_count = 0
        skipped_without_affected_accounts_count = 0

        for candidate in candidates:
            if context.should_stop:
                break

            if candidate.clan.type != _MAIN_CLAN_TYPE:
                skipped_non_main_count += 1
                continue

            if not _is_ready_for_detection(candidate.war_snapshot, observed_at=observed_at):
                skipped_not_ready_count += 1
                continue

            if not candidate.affected_accounts:
                skipped_without_affected_accounts_count += 1
                continue

            event_key = build_war_warning_event_key(
                clan_tag=candidate.clan.tag,
                war_event_key=candidate.war_snapshot.war_event_key,
                telegram_user_id=_required_model_id(
                    candidate.telegram_user,
                    model_name="TelegramUser",
                ),
            )
            result = await self._warning_creator.create_system_warning(
                telegram_user=candidate.telegram_user,
                reason_code=WarningReasonCode.WAR_ATTACK_MISSED,
                event_key=event_key,
                affected_accounts=list(candidate.affected_accounts),
                clan=candidate.clan,
                created_cwl_season_key=candidate.created_cwl_season_key,
            )

            if result.created:
                created_count += 1
            else:
                existing_count += 1

        await self._repository.flush()

        return DetectMissedWarAttacksJobResult(
            discovered_count=len(candidates),
            created_count=created_count,
            existing_count=existing_count,
            skipped_not_ready_count=skipped_not_ready_count,
            skipped_non_main_count=skipped_non_main_count,
            skipped_without_affected_accounts_count=skipped_without_affected_accounts_count,
        )


def register_detect_missed_war_attacks_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует detect missed war attacks job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=DETECT_MISSED_WAR_ATTACKS_JOB_NAME)
    async def detect_missed_war_attacks(context: WorkerJobContext) -> None:
        """Запускает обнаружение пропущенных атак КВ внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("detect_missed_war_attacks требует DB session.")

        job = DetectMissedWarAttacksJob.from_session(session=context.session)
        await job.run(context)


def _is_ready_for_detection(war_snapshot: WarSnapshot, *, observed_at: datetime) -> bool:
    """Проверяет, что война закончилась минимум 5 минут назад.

    Args:
        war_snapshot: Snapshot войны.
        observed_at: Время текущей проверки.

    Returns:
        `True`, если detect можно запускать.
    """
    return observed_at >= war_snapshot.end_time + _WAR_DETECTION_DELAY


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
    "DETECT_MISSED_WAR_ATTACKS_JOB_NAME",
    "DetectMissedWarAttacksJob",
    "DetectMissedWarAttacksJobResult",
    "DetectMissedWarAttacksRepository",
    "MissedWarAttackCandidate",
    "SqlAlchemyDetectMissedWarAttacksRepository",
    "WarAttackWarningCreator",
    "register_detect_missed_war_attacks_job",
]
