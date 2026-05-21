"""Тесты worker job обнаружения пропущенных атак КВ."""

import asyncio
from datetime import UTC, datetime, timedelta

from app.db.models import Clan, TelegramUser, Warning, WarSnapshot
from app.domain import ClanType, WarningReasonCode, WarningSource, WarningStatus
from app.services import WarningAffectedAccount, WarningCreationService
from app.worker.jobs import DETECT_MISSED_WAR_ATTACKS_JOB_NAME, DetectMissedWarAttacksJob
from app.worker.jobs.detect_missed_war_attacks import MissedWarAttackCandidate
from app.worker.scheduler import WorkerJobContext, create_default_worker_registry


class InMemoryDetectMissedWarAttacksRepository:
    """In-memory repository для unit-тестов detect missed war attacks job."""

    def __init__(self, candidates: list[MissedWarAttackCandidate]) -> None:
        """Инициализирует repository.

        Args:
            candidates: Кандидаты на warn за пропущенные атаки.
        """
        self.candidates = candidates
        self.flush_count = 0
        self.observed_at_calls: list[datetime] = []

    async def list_missed_war_attack_candidates(
        self,
        *,
        observed_at: datetime,
    ) -> tuple[MissedWarAttackCandidate, ...]:
        """Возвращает кандидатов на warn.

        Args:
            observed_at: Время текущей проверки.

        Returns:
            Tuple кандидатов.
        """
        self.observed_at_calls.append(observed_at)
        return tuple(self.candidates)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


class InMemoryWarningRepository:
    """In-memory repository для WarningCreationService."""

    def __init__(self, warnings: list[Warning] | None = None) -> None:
        """Инициализирует repository.

        Args:
            warnings: Начальный набор warn-записей.
        """
        self.warnings = warnings or []
        self.added_warnings: list[Warning] = []
        self.flush_count = 0

    async def get_by_event_key(self, event_key: str) -> Warning | None:
        """Возвращает warn по event key.

        Args:
            event_key: Идемпотентный ключ события.

        Returns:
            Warn или `None`.
        """
        for warning in self.warnings:
            if warning.event_key == event_key:
                return warning

        return None

    def add(self, warning: Warning) -> None:
        """Добавляет warn в память.

        Args:
            warning: Новая warn-запись.
        """
        self.added_warnings.append(warning)
        self.warnings.append(warning)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


async def test_detect_missed_war_attacks_job_creates_one_warn_for_multi_account_user() -> None:
    """Проверяет один warn на TelegramUser за одну войну с несколькими аккаунтами."""
    observed_at = datetime(2026, 5, 21, 12, 10, tzinfo=UTC)
    war_snapshot = _make_war_snapshot(end_time=observed_at - timedelta(minutes=5))
    telegram_user = _make_telegram_user(user_id=101)
    clan = _make_clan(clan_type=ClanType.MAIN)
    warning_repository = InMemoryWarningRepository()
    repository = InMemoryDetectMissedWarAttacksRepository(
        [
            MissedWarAttackCandidate(
                clan=clan,
                war_snapshot=war_snapshot,
                telegram_user=telegram_user,
                affected_accounts=(
                    WarningAffectedAccount(player_tag="#P1", player_name="Bangkok"),
                    WarningAffectedAccount(player_tag="#P2", player_name="Phoenix"),
                ),
                created_cwl_season_key="2026-05",
            )
        ]
    )
    job = DetectMissedWarAttacksJob(
        repository=repository,
        warning_creator=WarningCreationService(repository=warning_repository),
        clock=lambda: observed_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 1
    assert result.created_count == 1
    assert result.existing_count == 0
    assert warning_repository.flush_count == 1
    assert repository.flush_count == 1
    assert len(warning_repository.added_warnings) == 1

    warning = warning_repository.added_warnings[0]
    assert warning.telegram_user_id == 101
    assert warning.source == WarningSource.SYSTEM.value
    assert warning.status == WarningStatus.ACTIVE.value
    assert warning.reason_code == WarningReasonCode.WAR_ATTACK_MISSED.value
    assert warning.category == "war"
    assert warning.is_impactful is True
    assert warning.event_key == "warn:auto:war_attack_missed:#MAIN:war-key:101"
    assert warning.affected_player_tags_json == ["#P1", "#P2"]
    assert warning.affected_player_names_json == ["Bangkok", "Phoenix"]
    assert warning.created_cwl_season_key == "2026-05"


async def test_detect_missed_war_attacks_job_waits_until_war_ended_plus_5_minutes() -> None:
    """Проверяет, что warn не создаётся раньше end_time + 5 минут."""
    observed_at = datetime(2026, 5, 21, 12, 4, tzinfo=UTC)
    war_snapshot = _make_war_snapshot(end_time=datetime(2026, 5, 21, 12, 0, tzinfo=UTC))
    warning_repository = InMemoryWarningRepository()
    repository = InMemoryDetectMissedWarAttacksRepository(
        [
            MissedWarAttackCandidate(
                clan=_make_clan(clan_type=ClanType.MAIN),
                war_snapshot=war_snapshot,
                telegram_user=_make_telegram_user(user_id=101),
                affected_accounts=(
                    WarningAffectedAccount(player_tag="#P1", player_name="Bangkok"),
                ),
                created_cwl_season_key="2026-05",
            )
        ]
    )
    job = DetectMissedWarAttacksJob(
        repository=repository,
        warning_creator=WarningCreationService(repository=warning_repository),
        clock=lambda: observed_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 1
    assert result.created_count == 0
    assert result.skipped_not_ready_count == 1
    assert warning_repository.added_warnings == []


async def test_detect_missed_war_attacks_job_skips_academy_and_freezer_candidates() -> None:
    """Проверяет, что academy/freezer не получают автоматический КВ-warn."""
    observed_at = datetime(2026, 5, 21, 12, 10, tzinfo=UTC)
    war_snapshot = _make_war_snapshot(end_time=observed_at - timedelta(minutes=5))
    warning_repository = InMemoryWarningRepository()
    repository = InMemoryDetectMissedWarAttacksRepository(
        [
            MissedWarAttackCandidate(
                clan=_make_clan(clan_type=ClanType.ACADEMY),
                war_snapshot=war_snapshot,
                telegram_user=_make_telegram_user(user_id=101),
                affected_accounts=(
                    WarningAffectedAccount(player_tag="#P1", player_name="Bangkok"),
                ),
                created_cwl_season_key="2026-05",
            ),
            MissedWarAttackCandidate(
                clan=_make_clan(clan_id=2, clan_type=ClanType.FREEZER),
                war_snapshot=war_snapshot,
                telegram_user=_make_telegram_user(user_id=102, telegram_id=43),
                affected_accounts=(
                    WarningAffectedAccount(player_tag="#P2", player_name="Phoenix"),
                ),
                created_cwl_season_key="2026-05",
            ),
        ]
    )
    job = DetectMissedWarAttacksJob(
        repository=repository,
        warning_creator=WarningCreationService(repository=warning_repository),
        clock=lambda: observed_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 2
    assert result.created_count == 0
    assert result.skipped_non_main_count == 2
    assert warning_repository.added_warnings == []


async def test_detect_missed_war_attacks_job_deduplicates_repeated_worker_run() -> None:
    """Проверяет повторный worker-run без дубля warn."""
    observed_at = datetime(2026, 5, 21, 12, 10, tzinfo=UTC)
    warning_repository = InMemoryWarningRepository()
    repository = InMemoryDetectMissedWarAttacksRepository(
        [
            MissedWarAttackCandidate(
                clan=_make_clan(clan_type=ClanType.MAIN),
                war_snapshot=_make_war_snapshot(end_time=observed_at - timedelta(minutes=5)),
                telegram_user=_make_telegram_user(user_id=101),
                affected_accounts=(
                    WarningAffectedAccount(player_tag="#P1", player_name="Bangkok"),
                ),
                created_cwl_season_key="2026-05",
            )
        ]
    )
    job = DetectMissedWarAttacksJob(
        repository=repository,
        warning_creator=WarningCreationService(repository=warning_repository),
        clock=lambda: observed_at,
    )

    first_result = await job.run(_build_context())
    second_result = await job.run(_build_context())

    assert first_result.created_count == 1
    assert first_result.existing_count == 0
    assert second_result.created_count == 0
    assert second_result.existing_count == 1
    assert len(warning_repository.warnings) == 1
    assert repository.flush_count == 2


def test_default_worker_registry_registers_detect_missed_war_attacks_job() -> None:
    """Проверяет, что default worker registry подключает detect missed war attacks job."""
    registry = create_default_worker_registry(default_interval_seconds=900)

    job = registry.get(DETECT_MISSED_WAR_ATTACKS_JOB_NAME)

    assert job.name == DETECT_MISSED_WAR_ATTACKS_JOB_NAME
    assert job.run_in_transaction is True
    assert job.run_on_start is True
    assert registry.resolve_interval_seconds(job) == 900


def _make_clan(
    *,
    clan_id: int = 1,
    clan_type: ClanType,
) -> Clan:
    """Создаёт клан для unit-тестов.

    Args:
        clan_id: DB ID клана.
        clan_type: Тип клана.

    Returns:
        Модель Clan.
    """
    return Clan(
        id=clan_id,
        tag="#MAIN",
        name="Bestiary",
        type=clan_type.value,
        is_active=True,
    )


def _make_telegram_user(*, user_id: int, telegram_id: int = 42) -> TelegramUser:
    """Создаёт TelegramUser для unit-тестов.

    Args:
        user_id: DB ID пользователя.
        telegram_id: Внешний Telegram ID.

    Returns:
        Модель TelegramUser.
    """
    return TelegramUser(
        id=user_id,
        telegram_id=telegram_id,
        username="bangkok",
        display_name="Bangkok",
    )


def _make_war_snapshot(*, end_time: datetime) -> WarSnapshot:
    """Создаёт snapshot войны для unit-тестов.

    Args:
        end_time: Время окончания войны.

    Returns:
        Модель WarSnapshot.
    """
    return WarSnapshot(
        id=501,
        clan_id=1,
        war_tag=None,
        war_event_key="war-key",
        state="warEnded",
        team_size=15,
        attacks_per_member=2,
        preparation_start_time=datetime(2026, 5, 20, 10, 0, tzinfo=UTC),
        start_time=datetime(2026, 5, 20, 22, 0, tzinfo=UTC),
        end_time=end_time,
        opponent_tag="#OPP",
        opponent_name="Enemy",
        our_stars=30,
        opponent_stars=28,
        our_destruction=99,
        opponent_destruction=97,
        our_attacks=28,
        opponent_attacks=30,
        snapshot_at=end_time,
    )


def _build_context() -> WorkerJobContext:
    """Создаёт runtime-контекст для unit-тестов job.

    Returns:
        Контекст worker job без DB session.
    """
    return WorkerJobContext(
        job_name=DETECT_MISSED_WAR_ATTACKS_JOB_NAME,
        started_at=datetime(2026, 5, 21, tzinfo=UTC),
        stop_event=asyncio.Event(),
        session=None,
    )
