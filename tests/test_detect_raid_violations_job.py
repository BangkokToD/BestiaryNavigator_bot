"""Тесты worker job обнаружения raid-нарушений."""

import asyncio
from datetime import UTC, datetime

from app.db.models import Clan, RaidSeason, TelegramUser, Warning
from app.domain import (
    ClanType,
    RaidMemberStatus,
    WarningReasonCode,
    WarningSource,
    WarningStatus,
)
from app.services import WarningCreationService
from app.worker.jobs import DETECT_RAID_VIOLATIONS_JOB_NAME, DetectRaidViolationsJob
from app.worker.jobs.detect_raid_violations import RaidViolationMember
from app.worker.scheduler import WorkerJobContext, create_default_worker_registry


class InMemoryDetectRaidViolationsRepository:
    """In-memory repository для unit-тестов detect raid violations job."""

    def __init__(self, members: list[RaidViolationMember]) -> None:
        """Инициализирует repository.

        Args:
            members: Участники рейда для проверки.
        """
        self.members = members
        self.flush_count = 0
        self.observed_at_calls: list[datetime] = []

    async def list_raid_violation_members(
        self,
        *,
        observed_at: datetime,
    ) -> tuple[RaidViolationMember, ...]:
        """Возвращает участников рейдов.

        Args:
            observed_at: Время текущей проверки.

        Returns:
            Tuple участников рейда.
        """
        self.observed_at_calls.append(observed_at)
        return tuple(self.members)

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


async def test_detect_raid_violations_job_creates_raid_missed_warn() -> None:
    """Проверяет, что raid_missed создаёт impactful system warn."""
    telegram_user = _make_telegram_user(user_id=101)
    clan = _make_clan(clan_type=ClanType.MAIN)
    raid_season = _make_raid_season(clan=clan)
    warning_repository = InMemoryWarningRepository()
    repository = InMemoryDetectRaidViolationsRepository(
        [
            _make_violation_member(
                clan=clan,
                raid_season=raid_season,
                telegram_user=telegram_user,
                player_tag="#P0",
                player_name="Zero",
                status=RaidMemberStatus.RAID_MISSED,
            ),
            _make_violation_member(
                clan=clan,
                raid_season=raid_season,
                telegram_user=telegram_user,
                player_tag="#P4",
                player_name="Four",
                status=RaidMemberStatus.RAID_MISSED,
            ),
        ]
    )
    job = DetectRaidViolationsJob(
        repository=repository,
        warning_creator=WarningCreationService(repository=warning_repository),
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 2
    assert result.grouped_count == 1
    assert result.created_count == 1
    assert result.existing_count == 0
    assert repository.flush_count == 1
    assert len(warning_repository.added_warnings) == 1

    warning = warning_repository.added_warnings[0]
    assert warning.telegram_user_id == 101
    assert warning.source == WarningSource.SYSTEM.value
    assert warning.status == WarningStatus.ACTIVE.value
    assert warning.reason_code == WarningReasonCode.RAID_MISSED.value
    assert warning.category == "raid"
    assert warning.is_impactful is True
    assert warning.event_key == ("warn:auto:raid_missed:#MAIN:2026-05-17T07:00:00Z:101")
    assert warning.affected_player_tags_json == ["#P0", "#P4"]
    assert warning.affected_player_names_json == ["Zero", "Four"]
    assert warning.created_cwl_season_key == "2026-05"


async def test_detect_raid_violations_job_creates_raid_incomplete_warn() -> None:
    """Проверяет, что 5 атак создают raid_incomplete warn."""
    telegram_user = _make_telegram_user(user_id=101)
    clan = _make_clan(clan_type=ClanType.MAIN)
    raid_season = _make_raid_season(clan=clan)
    warning_repository = InMemoryWarningRepository()
    repository = InMemoryDetectRaidViolationsRepository(
        [
            _make_violation_member(
                clan=clan,
                raid_season=raid_season,
                telegram_user=telegram_user,
                player_tag="#P5",
                player_name="Five",
                status=RaidMemberStatus.RAID_INCOMPLETE,
            )
        ]
    )
    job = DetectRaidViolationsJob(
        repository=repository,
        warning_creator=WarningCreationService(repository=warning_repository),
    )

    result = await job.run(_build_context())

    assert result.created_count == 1

    warning = warning_repository.added_warnings[0]
    assert warning.reason_code == WarningReasonCode.RAID_INCOMPLETE.value
    assert warning.category == "raid"
    assert warning.is_impactful is True
    assert warning.event_key == ("warn:auto:raid_incomplete:#MAIN:2026-05-17T07:00:00Z:101")
    assert warning.affected_player_tags_json == ["#P5"]


async def test_detect_raid_violations_job_ignores_raid_full() -> None:
    """Проверяет, что raid_full не создаёт warn."""
    telegram_user = _make_telegram_user(user_id=101)
    clan = _make_clan(clan_type=ClanType.MAIN)
    raid_season = _make_raid_season(clan=clan)
    warning_repository = InMemoryWarningRepository()
    repository = InMemoryDetectRaidViolationsRepository(
        [
            _make_violation_member(
                clan=clan,
                raid_season=raid_season,
                telegram_user=telegram_user,
                player_tag="#P6",
                player_name="Six",
                status=RaidMemberStatus.RAID_FULL,
            )
        ]
    )
    job = DetectRaidViolationsJob(
        repository=repository,
        warning_creator=WarningCreationService(repository=warning_repository),
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 1
    assert result.created_count == 0
    assert result.skipped_without_violation_count == 1
    assert warning_repository.added_warnings == []


async def test_detect_raid_violations_job_uses_worst_reason_for_mixed_violations() -> None:
    """Проверяет один warn по худшей причине при разных нарушениях."""
    telegram_user = _make_telegram_user(user_id=101)
    clan = _make_clan(clan_type=ClanType.MAIN)
    raid_season = _make_raid_season(clan=clan)
    warning_repository = InMemoryWarningRepository()
    repository = InMemoryDetectRaidViolationsRepository(
        [
            _make_violation_member(
                clan=clan,
                raid_season=raid_season,
                telegram_user=telegram_user,
                player_tag="#P4",
                player_name="Four",
                status=RaidMemberStatus.RAID_MISSED,
            ),
            _make_violation_member(
                clan=clan,
                raid_season=raid_season,
                telegram_user=telegram_user,
                player_tag="#P5",
                player_name="Five",
                status=RaidMemberStatus.RAID_INCOMPLETE,
            ),
        ]
    )
    job = DetectRaidViolationsJob(
        repository=repository,
        warning_creator=WarningCreationService(repository=warning_repository),
    )

    result = await job.run(_build_context())

    assert result.created_count == 1
    assert len(warning_repository.added_warnings) == 1

    warning = warning_repository.added_warnings[0]
    assert warning.reason_code == WarningReasonCode.RAID_MISSED.value
    assert warning.affected_player_tags_json == ["#P4", "#P5"]


async def test_detect_raid_violations_job_waits_until_raid_season_ended() -> None:
    """Проверяет, что до окончания raid season warn не создаётся."""
    telegram_user = _make_telegram_user(user_id=101)
    clan = _make_clan(clan_type=ClanType.MAIN)
    raid_season = _make_raid_season(
        clan=clan,
        end_time=datetime(2999, 5, 20, 7, 0, tzinfo=UTC),
    )
    warning_repository = InMemoryWarningRepository()
    repository = InMemoryDetectRaidViolationsRepository(
        [
            _make_violation_member(
                clan=clan,
                raid_season=raid_season,
                telegram_user=telegram_user,
                player_tag="#P4",
                player_name="Four",
                status=RaidMemberStatus.RAID_MISSED,
            )
        ]
    )
    job = DetectRaidViolationsJob(
        repository=repository,
        warning_creator=WarningCreationService(repository=warning_repository),
    )

    result = await job.run(_build_context())

    assert result.created_count == 0
    assert result.skipped_not_ready_count == 1
    assert warning_repository.added_warnings == []


async def test_detect_raid_violations_job_skips_academy_and_freezer() -> None:
    """Проверяет, что academy/freezer не получают raid-warn."""
    telegram_user = _make_telegram_user(user_id=101)
    academy = _make_clan(clan_id=1, clan_type=ClanType.ACADEMY)
    freezer = _make_clan(clan_id=2, clan_type=ClanType.FREEZER)
    warning_repository = InMemoryWarningRepository()
    repository = InMemoryDetectRaidViolationsRepository(
        [
            _make_violation_member(
                clan=academy,
                raid_season=_make_raid_season(clan=academy),
                telegram_user=telegram_user,
                player_tag="#P1",
                player_name="Academy",
                status=RaidMemberStatus.RAID_MISSED,
            ),
            _make_violation_member(
                clan=freezer,
                raid_season=_make_raid_season(raid_id=302, clan=freezer),
                telegram_user=telegram_user,
                player_tag="#P2",
                player_name="Freezer",
                status=RaidMemberStatus.RAID_INCOMPLETE,
            ),
        ]
    )
    job = DetectRaidViolationsJob(
        repository=repository,
        warning_creator=WarningCreationService(repository=warning_repository),
    )

    result = await job.run(_build_context())

    assert result.created_count == 0
    assert result.skipped_non_main_count == 2
    assert warning_repository.added_warnings == []


async def test_detect_raid_violations_job_deduplicates_repeated_run() -> None:
    """Проверяет повторный worker-run без дубля warn."""
    telegram_user = _make_telegram_user(user_id=101)
    clan = _make_clan(clan_type=ClanType.MAIN)
    raid_season = _make_raid_season(clan=clan)
    warning_repository = InMemoryWarningRepository()
    repository = InMemoryDetectRaidViolationsRepository(
        [
            _make_violation_member(
                clan=clan,
                raid_season=raid_season,
                telegram_user=telegram_user,
                player_tag="#P4",
                player_name="Four",
                status=RaidMemberStatus.RAID_MISSED,
            )
        ]
    )
    job = DetectRaidViolationsJob(
        repository=repository,
        warning_creator=WarningCreationService(repository=warning_repository),
    )

    first_result = await job.run(_build_context())
    second_result = await job.run(_build_context())

    assert first_result.created_count == 1
    assert first_result.existing_count == 0
    assert second_result.created_count == 0
    assert second_result.existing_count == 1
    assert len(warning_repository.warnings) == 1
    assert repository.flush_count == 2


def test_default_worker_registry_registers_detect_raid_violations_job() -> None:
    """Проверяет, что default worker registry подключает detect raid violations job."""
    registry = create_default_worker_registry(default_interval_seconds=900)

    job = registry.get(DETECT_RAID_VIOLATIONS_JOB_NAME)

    assert job.name == DETECT_RAID_VIOLATIONS_JOB_NAME
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


def _make_raid_season(
    *,
    raid_id: int = 301,
    clan: Clan,
    end_time: datetime | None = None,
) -> RaidSeason:
    """Создаёт raid season для unit-тестов.

    Args:
        raid_id: DB ID raid season.
        clan: Клан raid season.
        end_time: Время окончания.

    Returns:
        Модель RaidSeason.
    """
    return RaidSeason(
        id=raid_id,
        clan_id=clan.id,
        state="ended",
        start_time=datetime(2026, 5, 17, 7, 0, tzinfo=UTC),
        end_time=end_time or datetime(2026, 5, 20, 7, 0, tzinfo=UTC),
        capital_total_loot=123456,
        raids_completed=12,
        total_attacks=240,
        enemy_districts_destroyed=44,
        offensive_reward=1200,
        defensive_reward=500,
        snapshot_at=datetime(2026, 5, 20, 8, 0, tzinfo=UTC),
    )


def _make_violation_member(
    *,
    clan: Clan,
    raid_season: RaidSeason,
    telegram_user: TelegramUser,
    player_tag: str,
    player_name: str,
    status: RaidMemberStatus,
) -> RaidViolationMember:
    """Создаёт участника raid-нарушения для unit-тестов.

    Args:
        clan: Клан.
        raid_season: Raid season.
        telegram_user: TelegramUser.
        player_tag: Тег игрока.
        player_name: Ник игрока.
        status: Raid status.

    Returns:
        Данные участника raid-нарушения.
    """
    return RaidViolationMember(
        clan=clan,
        raid_season=raid_season,
        telegram_user=telegram_user,
        player_tag=player_tag,
        player_name=player_name,
        status=status.value,
        created_cwl_season_key="2026-05",
    )


def _build_context() -> WorkerJobContext:
    """Создаёт runtime-контекст для unit-тестов job.

    Returns:
        Контекст worker job без DB session.
    """
    return WorkerJobContext(
        job_name=DETECT_RAID_VIOLATIONS_JOB_NAME,
        started_at=datetime(2026, 5, 20, tzinfo=UTC),
        stop_event=asyncio.Event(),
        session=None,
    )
