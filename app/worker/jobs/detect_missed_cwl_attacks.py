"""Worker job обнаружения пропущенных атак в ЛВК."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import (
    ApiError,
    Clan,
    ClanMemberSnapshot,
    CwlSeason,
    CwlWar,
    PlayerAccount,
    TelegramUser,
)
from app.domain import ClanType, WarningReasonCode, build_cwl_warning_event_key
from app.integrations.clash import (
    ClashApiClient,
    ClashApiError,
    ClashCwlWar,
    ClashWarSideSummary,
    map_clash_api_error_to_context,
)
from app.services import WarningAffectedAccount, WarningCreationResult, WarningCreationService
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

DETECT_MISSED_CWL_ATTACKS_JOB_NAME = "detect_missed_cwl_attacks"

_CWL_DETECTION_DELAY = timedelta(minutes=5)
_CWL_WAR_ENTITY_TYPE = "cwl_war"
_MAIN_CLAN_TYPE = ClanType.MAIN.value
_NON_SANCTION_CLAN_TYPES = frozenset({ClanType.ACADEMY.value, ClanType.FREEZER.value})


class ClashCwlWarProvider(Protocol):
    """Contract Clash API provider для проверки конкретных CWL wars."""

    async def get_cwl_war(self, war_tag: str) -> ClashCwlWar:
        """Получает конкретную CWL-war по war tag.

        Args:
            war_tag: War tag из Clash API.

        Returns:
            DTO конкретной войны ЛВК.
        """


class CwlAttackWarningCreator(Protocol):
    """Contract сервиса создания warn за пропущенную атаку ЛВК."""

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


@dataclass(frozen=True, slots=True)
class CwlWarDetectionCandidate:
    """Кандидат на проверку пропущенных атак в конкретной CWL-war."""

    clan: Clan
    cwl_season: CwlSeason
    cwl_war: CwlWar


@dataclass(frozen=True, slots=True)
class LinkedCwlMember:
    """Привязанный участник CWL-war."""

    player_tag: str
    player_name: str
    telegram_user: TelegramUser


class DetectMissedCwlAttacksRepository(Protocol):
    """Repository contract для detect missed cwl attacks job."""

    async def list_cwl_war_detection_candidates(
        self,
        *,
        observed_at: datetime,
    ) -> tuple[CwlWarDetectionCandidate, ...]:
        """Возвращает CWL wars, которые уже можно проверять.

        Args:
            observed_at: Время текущей проверки.

        Returns:
            Tuple CWL wars для проверки.
        """

    async def list_linked_members(
        self,
        *,
        player_tags: tuple[str, ...],
    ) -> tuple[LinkedCwlMember, ...]:
        """Возвращает Telegram-привязки по player tags.

        Args:
            player_tags: Теги игроков, пропустивших атаку.

        Returns:
            Tuple привязанных участников.
        """

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в unit of work.

        Args:
            api_error: Модель ошибки внешнего API.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyDetectMissedCwlAttacksRepository:
    """SQLAlchemy repository для detect missed cwl attacks job."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_cwl_war_detection_candidates(
        self,
        *,
        observed_at: datetime,
    ) -> tuple[CwlWarDetectionCandidate, ...]:
        """Возвращает main CWL wars после end_time + 5 минут.

        Args:
            observed_at: Время текущей проверки.

        Returns:
            Tuple CWL wars для проверки.
        """
        result = await self._session.execute(
            select(Clan, CwlSeason, CwlWar)
            .join(CwlSeason, CwlSeason.clan_id == Clan.id)
            .join(CwlWar, CwlWar.cwl_season_id == CwlSeason.id)
            .where(
                Clan.is_active.is_(True),
                Clan.type == _MAIN_CLAN_TYPE,
                CwlWar.end_time <= observed_at - _CWL_DETECTION_DELAY,
            )
            .order_by(CwlWar.end_time, CwlWar.war_tag)
        )
        return tuple(
            CwlWarDetectionCandidate(
                clan=clan,
                cwl_season=cwl_season,
                cwl_war=cwl_war,
            )
            for clan, cwl_season, cwl_war in result.all()
        )

    async def list_linked_members(
        self,
        *,
        player_tags: tuple[str, ...],
    ) -> tuple[LinkedCwlMember, ...]:
        """Возвращает linked TelegramUser для player tags.

        Args:
            player_tags: Теги игроков.

        Returns:
            Tuple привязанных участников, исключая текущую academy/freezer-позицию.
        """
        if not player_tags:
            return ()

        current_snapshot = aliased(ClanMemberSnapshot)
        current_clan = aliased(Clan)
        non_sanction_presence_exists = (
            select(current_snapshot.id)
            .join(current_clan, current_clan.id == current_snapshot.clan_id)
            .where(
                current_snapshot.player_tag == PlayerAccount.player_tag,
                current_snapshot.is_current.is_(True),
                current_clan.is_active.is_(True),
                current_clan.type.in_(sorted(_NON_SANCTION_CLAN_TYPES)),
            )
            .exists()
        )

        result = await self._session.execute(
            select(PlayerAccount, TelegramUser)
            .join(TelegramUser, TelegramUser.id == PlayerAccount.telegram_user_id)
            .where(
                PlayerAccount.player_tag.in_(player_tags),
                PlayerAccount.is_active.is_(True),
                PlayerAccount.telegram_user_id.is_not(None),
                ~non_sanction_presence_exists,
            )
            .order_by(TelegramUser.id, PlayerAccount.player_tag)
        )

        return tuple(
            LinkedCwlMember(
                player_tag=account.player_tag,
                player_name=account.name,
                telegram_user=telegram_user,
            )
            for account, telegram_user in result.all()
        )

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
class DetectMissedCwlAttacksJobResult:
    """Результат одного запуска detect missed cwl attacks job."""

    discovered_count: int
    checked_war_count: int
    failed_api_count: int
    created_count: int
    existing_count: int
    skipped_not_ready_count: int
    skipped_non_main_count: int
    skipped_without_missed_members_count: int
    skipped_without_linked_members_count: int


class DetectMissedCwlAttacksJob:
    """Job создания system warn за пропущенные атаки ЛВК."""

    def __init__(
        self,
        *,
        repository: DetectMissedCwlAttacksRepository,
        clash_client: ClashCwlWarProvider,
        warning_creator: CwlAttackWarningCreator,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository кандидатов по CWL wars.
            clash_client: Clash API client или совместимый provider.
            warning_creator: Сервис создания system warn.
            clock: Источник текущего времени для тестов.
        """
        self._repository = repository
        self._clash_client = clash_client
        self._warning_creator = warning_creator
        self._clock = clock or _utc_now

    @classmethod
    def from_session(
        cls,
        *,
        session: AsyncSession,
        clash_client: ClashCwlWarProvider,
    ) -> "DetectMissedCwlAttacksJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            clash_client: Clash API client или совместимый provider.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemyDetectMissedCwlAttacksRepository(session),
            clash_client=clash_client,
            warning_creator=WarningCreationService.from_session(session=session),
        )

    async def run(self, context: WorkerJobContext) -> DetectMissedCwlAttacksJobResult:
        """Создаёт warn за пропущенные атаки ЛВК.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        observed_at = self._clock()
        candidates = await self._repository.list_cwl_war_detection_candidates(
            observed_at=observed_at
        )

        checked_war_count = 0
        failed_api_count = 0
        created_count = 0
        existing_count = 0
        skipped_not_ready_count = 0
        skipped_non_main_count = 0
        skipped_without_missed_members_count = 0
        skipped_without_linked_members_count = 0

        for candidate in candidates:
            if context.should_stop:
                break

            if candidate.clan.type != _MAIN_CLAN_TYPE:
                skipped_non_main_count += 1
                continue

            if not _is_ready_for_detection(candidate.cwl_war, observed_at=observed_at):
                skipped_not_ready_count += 1
                continue

            try:
                cwl_war_payload = await self._clash_client.get_cwl_war(candidate.cwl_war.war_tag)
            except ClashApiError as error:
                self._handle_clash_api_error(
                    war_tag=candidate.cwl_war.war_tag,
                    error=error,
                    worker_name=context.job_name,
                )
                failed_api_count += 1
                continue

            checked_war_count += 1
            missed_members = _extract_missed_our_members(
                cwl_war_payload,
                our_clan_tag=candidate.cwl_war.our_clan_tag,
            )
            if not missed_members:
                skipped_without_missed_members_count += 1
                continue

            linked_members = await self._repository.list_linked_members(
                player_tags=tuple(member.player_tag for member in missed_members),
            )
            if not linked_members:
                skipped_without_linked_members_count += 1
                continue

            for telegram_user, affected_accounts in _group_by_telegram_user(
                missed_members=missed_members,
                linked_members=linked_members,
            ):
                event_key = build_cwl_warning_event_key(
                    clan_tag=candidate.clan.tag,
                    cwl_war_tag=candidate.cwl_war.war_tag,
                    telegram_user_id=_required_model_id(
                        telegram_user,
                        model_name="TelegramUser",
                    ),
                )
                result = await self._warning_creator.create_system_warning(
                    telegram_user=telegram_user,
                    reason_code=WarningReasonCode.CWL_ATTACK_MISSED,
                    event_key=event_key,
                    affected_accounts=list(affected_accounts),
                    clan=candidate.clan,
                    created_cwl_season_key=candidate.cwl_season.season,
                )
                if result.created:
                    created_count += 1
                else:
                    existing_count += 1

        await self._repository.flush()

        return DetectMissedCwlAttacksJobResult(
            discovered_count=len(candidates),
            checked_war_count=checked_war_count,
            failed_api_count=failed_api_count,
            created_count=created_count,
            existing_count=existing_count,
            skipped_not_ready_count=skipped_not_ready_count,
            skipped_non_main_count=skipped_non_main_count,
            skipped_without_missed_members_count=skipped_without_missed_members_count,
            skipped_without_linked_members_count=skipped_without_linked_members_count,
        )

    def _handle_clash_api_error(
        self,
        *,
        war_tag: str,
        error: ClashApiError,
        worker_name: str,
    ) -> None:
        """Обрабатывает typed ошибку Clash API для конкретной CWL-war.

        Args:
            war_tag: War tag, который не удалось получить.
            error: Typed Clash API exception.
            worker_name: Имя текущей worker job для debug-контекста.
        """
        error_context = map_clash_api_error_to_context(
            error,
            entity_type=_CWL_WAR_ENTITY_TYPE,
            entity_tag=war_tag,
            worker_name=worker_name,
            retry_count=0,
        )
        self._repository.add_api_error(ApiError(**error_context.to_api_error_values()))


def register_detect_missed_cwl_attacks_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует detect missed cwl attacks job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=DETECT_MISSED_CWL_ATTACKS_JOB_NAME)
    async def detect_missed_cwl_attacks(context: WorkerJobContext) -> None:
        """Запускает обнаружение пропущенных атак ЛВК внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("detect_missed_cwl_attacks требует DB session.")

        async with ClashApiClient.from_settings() as clash_client:
            job = DetectMissedCwlAttacksJob.from_session(
                session=context.session,
                clash_client=clash_client,
            )
            await job.run(context)


def _is_ready_for_detection(cwl_war: CwlWar, *, observed_at: datetime) -> bool:
    """Проверяет, что CWL-war закончилась минимум 5 минут назад.

    Args:
        cwl_war: Модель CWL-war.
        observed_at: Время текущей проверки.

    Returns:
        `True`, если detect можно запускать.
    """
    return observed_at >= cwl_war.end_time + _CWL_DETECTION_DELAY


def _extract_missed_our_members(
    cwl_war: ClashCwlWar,
    *,
    our_clan_tag: str,
) -> tuple[WarningAffectedAccount, ...]:
    """Извлекает участников нашего клана без атаки в CWL-war.

    Args:
        cwl_war: DTO конкретной CWL-war.
        our_clan_tag: Тег tracked clan.

    Returns:
        Tuple affected accounts.
    """
    our_side = _select_our_side(cwl_war, our_clan_tag=our_clan_tag)
    if our_side is None:
        return ()

    return tuple(
        WarningAffectedAccount(
            player_tag=member.player_tag,
            player_name=member.name,
        )
        for member in our_side.members
        if not member.attacks
    )


def _select_our_side(
    cwl_war: ClashCwlWar,
    *,
    our_clan_tag: str,
) -> ClashWarSideSummary | None:
    """Выбирает нашу сторону конкретной CWL-war.

    Args:
        cwl_war: DTO конкретной CWL-war.
        our_clan_tag: Нормализованный тег нашего клана.

    Returns:
        DTO нашей стороны или `None`.
    """
    if cwl_war.clan is not None and cwl_war.clan.tag == our_clan_tag:
        return cwl_war.clan

    if cwl_war.opponent is not None and cwl_war.opponent.tag == our_clan_tag:
        return cwl_war.opponent

    return None


def _group_by_telegram_user(
    *,
    missed_members: tuple[WarningAffectedAccount, ...],
    linked_members: tuple[LinkedCwlMember, ...],
) -> tuple[tuple[TelegramUser, tuple[WarningAffectedAccount, ...]], ...]:
    """Группирует пропущенные аккаунты по TelegramUser.

    Args:
        missed_members: Участники без атаки.
        linked_members: Telegram-привязки участников.

    Returns:
        Tuple `(telegram_user, affected_accounts)`.
    """
    missed_by_tag = {member.player_tag: member for member in missed_members}
    grouped: dict[int, tuple[TelegramUser, list[WarningAffectedAccount]]] = {}

    for linked_member in linked_members:
        if linked_member.player_tag not in missed_by_tag:
            continue

        telegram_user_id = _required_model_id(
            linked_member.telegram_user,
            model_name="TelegramUser",
        )
        if telegram_user_id not in grouped:
            grouped[telegram_user_id] = (linked_member.telegram_user, [])

        grouped[telegram_user_id][1].append(
            WarningAffectedAccount(
                player_tag=linked_member.player_tag,
                player_name=linked_member.player_name,
            )
        )

    return tuple(
        (telegram_user, tuple(affected_accounts))
        for telegram_user, affected_accounts in grouped.values()
    )


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
    "DETECT_MISSED_CWL_ATTACKS_JOB_NAME",
    "ClashCwlWarProvider",
    "CwlAttackWarningCreator",
    "CwlWarDetectionCandidate",
    "DetectMissedCwlAttacksJob",
    "DetectMissedCwlAttacksJobResult",
    "DetectMissedCwlAttacksRepository",
    "LinkedCwlMember",
    "SqlAlchemyDetectMissedCwlAttacksRepository",
    "register_detect_missed_cwl_attacks_job",
]
