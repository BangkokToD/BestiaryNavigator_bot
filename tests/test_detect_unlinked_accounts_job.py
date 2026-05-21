"""Тесты worker job обнаружения непривязанных аккаунтов."""

import asyncio
from datetime import UTC, datetime, timedelta

from app.db.models import Clan, KickCandidate
from app.domain import ClanType, KickCandidateReasonCode, KickCandidateStatus
from app.services.kick_candidates import KickCandidateCreationResult
from app.worker.jobs import DETECT_UNLINKED_ACCOUNTS_JOB_NAME, DetectUnlinkedAccountsJob
from app.worker.jobs.detect_unlinked_accounts import UnlinkedAccountCandidate
from app.worker.scheduler import WorkerJobContext, create_default_worker_registry


class InMemoryDetectUnlinkedAccountsRepository:
    """In-memory repository для unit-тестов detect unlinked accounts job."""

    def __init__(self, candidates: list[UnlinkedAccountCandidate]) -> None:
        """Инициализирует repository.

        Args:
            candidates: Набор непривязанных аккаунтов для проверки.
        """
        self.candidates = candidates
        self.flush_count = 0

    async def list_unlinked_current_members(self) -> tuple[UnlinkedAccountCandidate, ...]:
        """Возвращает непривязанные current members.

        Returns:
            Tuple данных непривязанных аккаунтов.
        """
        return tuple(self.candidates)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


class FakeUnlinkedAccountCandidateCreator:
    """Fake creator кандидатов на кик по непривязанным аккаунтам."""

    def __init__(self) -> None:
        """Инициализирует fake creator."""
        self.created_candidates_by_event_key: dict[str, KickCandidate] = {}
        self.calls: list[tuple[int, str, datetime, datetime | None]] = []

    async def create_for_unlinked_account(
        self,
        *,
        clan: Clan,
        player_tag: str,
        first_seen_at: datetime,
        observed_at: datetime | None = None,
    ) -> KickCandidateCreationResult:
        """Имитирует поведение KickCandidateService для непривязанного аккаунта.

        Args:
            clan: Клан, где найден аккаунт.
            player_tag: Тег аккаунта.
            first_seen_at: Первое появление аккаунта.
            observed_at: Время текущей проверки.

        Returns:
            Результат создания кандидата.
        """
        self.calls.append((clan.id, player_tag, first_seen_at, observed_at))
        if observed_at is None:
            raise AssertionError("observed_at должен передаваться явно.")

        if observed_at - first_seen_at < timedelta(days=3):
            return KickCandidateCreationResult(
                candidate=None,
                created=False,
                reason="unlinked_account_not_old_enough",
            )

        event_key = f"kick:unlinked_after_3_days:{clan.id}:{player_tag}"
        existing_candidate = self.created_candidates_by_event_key.get(event_key)
        if existing_candidate is not None:
            return KickCandidateCreationResult(candidate=existing_candidate, created=False)

        candidate = KickCandidate(
            player_tag=player_tag,
            reason_code=KickCandidateReasonCode.UNLINKED_AFTER_3_DAYS.value,
            status=KickCandidateStatus.PENDING_ADMIN_DECISION.value,
            event_key=event_key,
        )
        self.created_candidates_by_event_key[event_key] = candidate

        return KickCandidateCreationResult(candidate=candidate, created=True)


async def test_detect_unlinked_accounts_job_does_not_create_candidate_before_3_days() -> None:
    """Проверяет, что candidate не создаётся раньше 3 дней."""
    observed_at = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    first_seen_at = observed_at - timedelta(days=2, hours=23)
    clan = _make_clan(clan_id=1, tag="#MAIN", clan_type=ClanType.MAIN)
    repository = InMemoryDetectUnlinkedAccountsRepository(
        [
            UnlinkedAccountCandidate(
                clan=clan,
                player_tag="#P1",
                first_seen_at=first_seen_at,
            )
        ]
    )
    candidate_creator = FakeUnlinkedAccountCandidateCreator()
    job = DetectUnlinkedAccountsJob(
        repository=repository,
        candidate_creator=candidate_creator,
        clock=lambda: observed_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 1
    assert result.created_count == 0
    assert result.existing_count == 0
    assert result.not_old_enough_count == 1
    assert candidate_creator.created_candidates_by_event_key == {}
    assert candidate_creator.calls == [(1, "#P1", first_seen_at, observed_at)]
    assert repository.flush_count == 1


async def test_detect_unlinked_accounts_job_creates_candidate_after_3_days() -> None:
    """Проверяет создание candidate после 3 дней."""
    observed_at = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    first_seen_at = observed_at - timedelta(days=3)
    clan = _make_clan(clan_id=1, tag="#MAIN", clan_type=ClanType.MAIN)
    repository = InMemoryDetectUnlinkedAccountsRepository(
        [
            UnlinkedAccountCandidate(
                clan=clan,
                player_tag="#P1",
                first_seen_at=first_seen_at,
            )
        ]
    )
    candidate_creator = FakeUnlinkedAccountCandidateCreator()
    job = DetectUnlinkedAccountsJob(
        repository=repository,
        candidate_creator=candidate_creator,
        clock=lambda: observed_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 1
    assert result.created_count == 1
    assert result.existing_count == 0
    assert result.not_old_enough_count == 0

    assert len(candidate_creator.created_candidates_by_event_key) == 1
    candidate = next(iter(candidate_creator.created_candidates_by_event_key.values()))
    assert candidate.player_tag == "#P1"
    assert candidate.telegram_user_id is None
    assert candidate.reason_code == KickCandidateReasonCode.UNLINKED_AFTER_3_DAYS.value
    assert candidate.status == KickCandidateStatus.PENDING_ADMIN_DECISION.value
    assert candidate.event_key == "kick:unlinked_after_3_days:1:#P1"


async def test_detect_unlinked_accounts_job_deduplicates_repeated_run() -> None:
    """Проверяет повторный запуск без дубля candidate."""
    observed_at = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    first_seen_at = observed_at - timedelta(days=3)
    clan = _make_clan(clan_id=1, tag="#MAIN", clan_type=ClanType.MAIN)
    repository = InMemoryDetectUnlinkedAccountsRepository(
        [
            UnlinkedAccountCandidate(
                clan=clan,
                player_tag="#P1",
                first_seen_at=first_seen_at,
            )
        ]
    )
    candidate_creator = FakeUnlinkedAccountCandidateCreator()
    job = DetectUnlinkedAccountsJob(
        repository=repository,
        candidate_creator=candidate_creator,
        clock=lambda: observed_at,
    )

    first_result = await job.run(_build_context())
    second_result = await job.run(_build_context())

    assert first_result.created_count == 1
    assert first_result.existing_count == 0
    assert second_result.created_count == 0
    assert second_result.existing_count == 1
    assert len(candidate_creator.created_candidates_by_event_key) == 1
    assert repository.flush_count == 2


async def test_detect_unlinked_accounts_job_processes_main_and_academy_candidates() -> None:
    """Проверяет создание candidates для main и academy."""
    observed_at = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    first_seen_at = observed_at - timedelta(days=3)
    main_clan = _make_clan(clan_id=1, tag="#MAIN", clan_type=ClanType.MAIN)
    academy_clan = _make_clan(clan_id=2, tag="#ACA", clan_type=ClanType.ACADEMY)
    repository = InMemoryDetectUnlinkedAccountsRepository(
        [
            UnlinkedAccountCandidate(
                clan=main_clan,
                player_tag="#P1",
                first_seen_at=first_seen_at,
            ),
            UnlinkedAccountCandidate(
                clan=academy_clan,
                player_tag="#P2",
                first_seen_at=first_seen_at,
            ),
        ]
    )
    candidate_creator = FakeUnlinkedAccountCandidateCreator()
    job = DetectUnlinkedAccountsJob(
        repository=repository,
        candidate_creator=candidate_creator,
        clock=lambda: observed_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 2
    assert result.created_count == 2
    event_keys = set(candidate_creator.created_candidates_by_event_key)
    assert event_keys == {
        "kick:unlinked_after_3_days:1:#P1",
        "kick:unlinked_after_3_days:2:#P2",
    }


def test_default_worker_registry_registers_detect_unlinked_accounts_job() -> None:
    """Проверяет, что default worker registry подключает detect unlinked accounts job."""
    registry = create_default_worker_registry(default_interval_seconds=900)

    job = registry.get(DETECT_UNLINKED_ACCOUNTS_JOB_NAME)

    assert job.name == DETECT_UNLINKED_ACCOUNTS_JOB_NAME
    assert job.run_in_transaction is True
    assert job.run_on_start is True
    assert registry.resolve_interval_seconds(job) == 900


def _make_clan(*, clan_id: int, tag: str, clan_type: ClanType) -> Clan:
    """Создаёт клан для unit-тестов.

    Args:
        clan_id: DB id клана.
        tag: Нормализованный тег клана.
        clan_type: Тип клана.

    Returns:
        Модель клана.
    """
    return Clan(
        id=clan_id,
        tag=tag,
        name="Bestiary",
        type=clan_type.value,
        is_active=True,
    )


def _build_context() -> WorkerJobContext:
    """Создаёт runtime-контекст для unit-тестов job.

    Returns:
        Контекст worker job без DB session.
    """
    return WorkerJobContext(
        job_name=DETECT_UNLINKED_ACCOUNTS_JOB_NAME,
        started_at=datetime(2026, 5, 4, tzinfo=UTC),
        stop_event=asyncio.Event(),
        session=None,
    )
