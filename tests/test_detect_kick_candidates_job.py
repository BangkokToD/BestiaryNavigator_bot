"""Тесты worker job обнаружения кандидатов на кик."""

import asyncio
from datetime import UTC, datetime

from app.db.models import KickCandidate, TelegramUser, Warning
from app.domain import (
    KickCandidateReasonCode,
    KickCandidateStatus,
    WarningReasonCode,
    WarningSource,
    WarningStatus,
)
from app.services import KickCandidateService
from app.worker.jobs import DETECT_KICK_CANDIDATES_JOB_NAME, DetectKickCandidatesJob
from app.worker.jobs.detect_kick_candidates import (
    ImpactfulWarningsCandidate,
    LinkedAccountPresence,
    TelegramUserAccountPresence,
)
from app.worker.scheduler import WorkerJobContext, create_default_worker_registry


class InMemoryDetectKickCandidatesRepository:
    """In-memory repository для unit-тестов detect kick candidates job."""

    def __init__(
        self,
        *,
        warning_candidates: list[ImpactfulWarningsCandidate] | None = None,
        account_presences: list[TelegramUserAccountPresence] | None = None,
    ) -> None:
        """Инициализирует repository.

        Args:
            warning_candidates: Кандидаты по warn.
            account_presences: Присутствие аккаунтов по TelegramUser.
        """
        self.warning_candidates = warning_candidates or []
        self.account_presences = account_presences or []
        self.flush_count = 0

    async def list_impactful_warning_candidates(self) -> tuple[ImpactfulWarningsCandidate, ...]:
        """Возвращает кандидатов по warn.

        Returns:
            Tuple кандидатов по warn.
        """
        return tuple(self.warning_candidates)

    async def list_account_presence_candidates(self) -> tuple[TelegramUserAccountPresence, ...]:
        """Возвращает присутствие аккаунтов.

        Returns:
            Tuple присутствия аккаунтов.
        """
        return tuple(self.account_presences)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


class InMemoryKickCandidateRepository:
    """In-memory repository для KickCandidateService."""

    def __init__(self, candidates: list[KickCandidate] | None = None) -> None:
        """Инициализирует repository.

        Args:
            candidates: Начальный набор кандидатов.
        """
        self.candidates = candidates or []
        self.added_candidates: list[KickCandidate] = []
        self.flush_count = 0

    async def get_by_event_key(self, event_key: str) -> KickCandidate | None:
        """Возвращает кандидата по event key.

        Args:
            event_key: Event key кандидата.

        Returns:
            Кандидат или `None`.
        """
        for candidate in self.candidates:
            if candidate.event_key == event_key:
                return candidate

        return None

    def add(self, candidate: KickCandidate) -> None:
        """Добавляет кандидата в память.

        Args:
            candidate: Новая модель кандидата.
        """
        self.added_candidates.append(candidate)
        self.candidates.append(candidate)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


async def test_detect_kick_candidates_job_creates_candidate_for_two_impactful_warnings() -> None:
    """Проверяет создание candidate по двум active impactful warn."""
    telegram_user = _make_telegram_user(user_id=101)
    kick_repository = InMemoryKickCandidateRepository()
    repository = InMemoryDetectKickCandidatesRepository(
        warning_candidates=[
            ImpactfulWarningsCandidate(
                telegram_user=telegram_user,
                warnings=(
                    _make_warning(warning_id=1, telegram_user_id=101),
                    _make_warning(
                        warning_id=2,
                        telegram_user_id=101,
                        reason_code=WarningReasonCode.CWL_ATTACK_MISSED,
                    ),
                ),
                season_key="2026-05",
            )
        ]
    )
    job = DetectKickCandidatesJob(
        repository=repository,
        candidate_creator=KickCandidateService(repository=kick_repository),
    )

    result = await job.run(_build_context())

    assert result.warning_candidate_users_count == 1
    assert result.warning_candidates_created_count == 1
    assert result.warning_candidates_existing_count == 0
    assert len(kick_repository.added_candidates) == 1

    candidate = kick_repository.added_candidates[0]
    assert candidate.telegram_user_id == 101
    assert candidate.reason_code == KickCandidateReasonCode.TWO_IMPACTFUL_WARN.value
    assert candidate.status == KickCandidateStatus.PENDING_ADMIN_DECISION.value
    assert candidate.event_key == "kick:2_impactful_warn:101:2026-05"


async def test_detect_kick_candidates_job_ignores_manual_cancelled_and_expired_warnings() -> None:
    """Проверяет, что manual/cancelled/expired warn не создают candidate."""
    telegram_user = _make_telegram_user(user_id=101)
    kick_repository = InMemoryKickCandidateRepository()
    repository = InMemoryDetectKickCandidatesRepository(
        warning_candidates=[
            ImpactfulWarningsCandidate(
                telegram_user=telegram_user,
                warnings=(
                    _make_warning(warning_id=1, telegram_user_id=101),
                    _make_warning(
                        warning_id=2,
                        telegram_user_id=101,
                        source=WarningSource.MANUAL,
                        is_impactful=False,
                        reason_code=WarningReasonCode.SPAM,
                    ),
                    _make_warning(
                        warning_id=3,
                        telegram_user_id=101,
                        status=WarningStatus.CANCELLED,
                    ),
                    _make_warning(
                        warning_id=4,
                        telegram_user_id=101,
                        status=WarningStatus.EXPIRED,
                    ),
                ),
                season_key="2026-05",
            )
        ]
    )
    job = DetectKickCandidatesJob(
        repository=repository,
        candidate_creator=KickCandidateService(repository=kick_repository),
    )

    result = await job.run(_build_context())

    assert result.warning_candidate_users_count == 1
    assert result.warning_candidates_created_count == 0
    assert result.warning_candidates_skipped_count == 1
    assert kick_repository.added_candidates == []


async def test_detect_kick_candidates_job_creates_candidate_for_all_accounts_left() -> None:
    """Проверяет создание all_accounts_left candidate."""
    telegram_user = _make_telegram_user(user_id=101)
    kick_repository = InMemoryKickCandidateRepository()
    repository = InMemoryDetectKickCandidatesRepository(
        account_presences=[
            TelegramUserAccountPresence(
                telegram_user=telegram_user,
                accounts=(
                    LinkedAccountPresence(
                        player_tag="#P1",
                        is_current_in_tracked_clan=False,
                    ),
                    LinkedAccountPresence(
                        player_tag="#P2",
                        is_current_in_tracked_clan=False,
                    ),
                ),
            )
        ]
    )
    job = DetectKickCandidatesJob(
        repository=repository,
        candidate_creator=KickCandidateService(repository=kick_repository),
    )

    result = await job.run(_build_context())

    assert result.account_presence_users_count == 1
    assert result.all_accounts_left_created_count == 1
    assert result.linked_account_left_created_count == 0

    candidate = kick_repository.added_candidates[0]
    assert candidate.telegram_user_id == 101
    assert candidate.player_tag is None
    assert candidate.reason_code == KickCandidateReasonCode.ALL_ACCOUNTS_LEFT.value
    assert candidate.event_key == "kick:all_accounts_left:101"


async def test_detect_kick_candidates_job_creates_candidate_for_linked_account_left() -> None:
    """Проверяет linked_account_left candidate, если другие аккаунты остались."""
    telegram_user = _make_telegram_user(user_id=101)
    kick_repository = InMemoryKickCandidateRepository()
    repository = InMemoryDetectKickCandidatesRepository(
        account_presences=[
            TelegramUserAccountPresence(
                telegram_user=telegram_user,
                accounts=(
                    LinkedAccountPresence(
                        player_tag="#P1",
                        is_current_in_tracked_clan=True,
                    ),
                    LinkedAccountPresence(
                        player_tag="#P2",
                        is_current_in_tracked_clan=False,
                    ),
                ),
            )
        ]
    )
    job = DetectKickCandidatesJob(
        repository=repository,
        candidate_creator=KickCandidateService(repository=kick_repository),
    )

    result = await job.run(_build_context())

    assert result.all_accounts_left_created_count == 0
    assert result.linked_account_left_created_count == 1

    candidate = kick_repository.added_candidates[0]
    assert candidate.telegram_user_id == 101
    assert candidate.player_tag == "#P2"
    assert candidate.reason_code == KickCandidateReasonCode.LINKED_ACCOUNT_LEFT.value
    assert candidate.event_key == "kick:linked_account_left:101:#P2"


async def test_detect_kick_candidates_job_skips_current_account_left_candidate() -> None:
    """Проверяет, что переход в academy/freezer не предлагает удаление из Telegram."""
    telegram_user = _make_telegram_user(user_id=101)
    kick_repository = InMemoryKickCandidateRepository()
    repository = InMemoryDetectKickCandidatesRepository(
        account_presences=[
            TelegramUserAccountPresence(
                telegram_user=telegram_user,
                accounts=(
                    LinkedAccountPresence(
                        player_tag="#P1",
                        is_current_in_tracked_clan=True,
                    ),
                ),
            )
        ]
    )
    job = DetectKickCandidatesJob(
        repository=repository,
        candidate_creator=KickCandidateService(repository=kick_repository),
    )

    result = await job.run(_build_context())

    assert result.all_accounts_left_created_count == 0
    assert result.linked_account_left_created_count == 0
    assert kick_repository.added_candidates == []


async def test_detect_kick_candidates_job_deduplicates_repeated_run() -> None:
    """Проверяет повторный запуск без дублей candidates."""
    telegram_user = _make_telegram_user(user_id=101)
    kick_repository = InMemoryKickCandidateRepository()
    repository = InMemoryDetectKickCandidatesRepository(
        warning_candidates=[
            ImpactfulWarningsCandidate(
                telegram_user=telegram_user,
                warnings=(
                    _make_warning(warning_id=1, telegram_user_id=101),
                    _make_warning(warning_id=2, telegram_user_id=101),
                ),
                season_key="2026-05",
            )
        ],
        account_presences=[
            TelegramUserAccountPresence(
                telegram_user=telegram_user,
                accounts=(
                    LinkedAccountPresence(
                        player_tag="#P1",
                        is_current_in_tracked_clan=False,
                    ),
                ),
            )
        ],
    )
    job = DetectKickCandidatesJob(
        repository=repository,
        candidate_creator=KickCandidateService(repository=kick_repository),
    )

    first_result = await job.run(_build_context())
    second_result = await job.run(_build_context())

    assert first_result.warning_candidates_created_count == 1
    assert first_result.all_accounts_left_created_count == 1
    assert second_result.warning_candidates_existing_count == 1
    assert second_result.all_accounts_left_existing_count == 1
    assert len(kick_repository.added_candidates) == 2
    assert repository.flush_count == 2


def test_default_worker_registry_registers_detect_kick_candidates_job() -> None:
    """Проверяет, что default worker registry подключает detect kick candidates job."""
    registry = create_default_worker_registry(default_interval_seconds=900)

    job = registry.get(DETECT_KICK_CANDIDATES_JOB_NAME)

    assert job.name == DETECT_KICK_CANDIDATES_JOB_NAME
    assert job.run_in_transaction is True
    assert job.run_on_start is True
    assert registry.resolve_interval_seconds(job) == 900


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


def _make_warning(
    *,
    warning_id: int,
    telegram_user_id: int,
    source: WarningSource = WarningSource.SYSTEM,
    status: WarningStatus = WarningStatus.ACTIVE,
    is_impactful: bool = True,
    reason_code: WarningReasonCode = WarningReasonCode.RAID_MISSED,
    created_cwl_season_key: str | None = "2026-05",
) -> Warning:
    """Создаёт Warning для unit-тестов.

    Args:
        warning_id: DB ID warn.
        telegram_user_id: DB ID TelegramUser.
        source: Источник warn.
        status: Статус warn.
        is_impactful: Влияет ли warn на решения.
        reason_code: Код причины warn.
        created_cwl_season_key: CWL season key.

    Returns:
        Модель Warning.
    """
    return Warning(
        id=warning_id,
        telegram_user_id=telegram_user_id,
        source=source.value,
        status=status.value,
        reason_code=reason_code.value,
        category="raid",
        is_impactful=is_impactful,
        created_cwl_season_key=created_cwl_season_key,
        affected_player_tags_json=["#P1"],
        affected_player_names_json=["Bangkok"],
    )


def _build_context() -> WorkerJobContext:
    """Создаёт runtime-контекст для unit-тестов job.

    Returns:
        Контекст worker job без DB session.
    """
    return WorkerJobContext(
        job_name=DETECT_KICK_CANDIDATES_JOB_NAME,
        started_at=datetime(2026, 5, 4, tzinfo=UTC),
        stop_event=asyncio.Event(),
        session=None,
    )
