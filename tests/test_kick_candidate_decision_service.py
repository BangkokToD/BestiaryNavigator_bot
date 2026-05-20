"""Тесты workflow-решений по кандидатам на кик."""

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import KickCandidate, TelegramUser
from app.domain import KickCandidateReasonCode, KickCandidateStatus
from app.services import (
    KickCandidateDecisionError,
    KickCandidateDecisionResult,
    KickCandidateDecisionService,
    KickCandidateInvalidTransitionError,
    KickCandidateNotFoundError,
)


class InMemoryKickCandidateDecisionRepository:
    """In-memory repository для unit-тестов KickCandidateDecisionService."""

    def __init__(self, candidates: list[KickCandidate] | None = None) -> None:
        """Инициализирует repository.

        Args:
            candidates: Начальный набор кандидатов.
        """
        self.candidates = {candidate.id: candidate for candidate in candidates or []}
        self.flush_count = 0

    async def get_by_id(self, candidate_id: int) -> KickCandidate | None:
        """Возвращает кандидата по DB ID."""
        return self.candidates.get(candidate_id)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


def make_admin(*, user_id: int = 777, telegram_id: int = 123456789) -> TelegramUser:
    """Создаёт TelegramUser администратора для unit-тестов."""
    return TelegramUser(
        id=user_id,
        telegram_id=telegram_id,
        username="admin",
        display_name="Admin",
    )


def make_candidate(
    *,
    candidate_id: int = 1,
    status: KickCandidateStatus = KickCandidateStatus.PENDING_ADMIN_DECISION,
) -> KickCandidate:
    """Создаёт KickCandidate для unit-тестов."""
    return KickCandidate(
        id=candidate_id,
        telegram_user_id=101,
        reason_code=KickCandidateReasonCode.TWO_IMPACTFUL_WARN.value,
        status=status.value,
        event_key=f"kick:2_impactful_warn:101:2026-05:{candidate_id}",
    )


@pytest.mark.asyncio
async def test_kick_candidate_decision_service_approves_pending_candidate() -> None:
    """Проверяет approve из pending и сохранение decision-полей."""
    candidate = make_candidate()
    admin = make_admin()
    repository = InMemoryKickCandidateDecisionRepository([candidate])
    service = KickCandidateDecisionService(repository=repository)

    result = await service.approve_candidate(
        candidate_id=1,
        decision_by=admin,
        comment=" можно кикать ",
    )

    assert isinstance(result, KickCandidateDecisionResult)
    assert result.candidate is candidate
    assert result.changed is True
    assert repository.flush_count == 1
    assert candidate.status == KickCandidateStatus.APPROVED.value
    assert candidate.decision_by_telegram_user_id == 777
    assert candidate.decision_by_user is admin
    assert candidate.decision_at is not None
    assert candidate.decision_comment == "можно кикать"
    assert candidate.deadline_at is None
    assert candidate.executed_at is None


@pytest.mark.asyncio
async def test_kick_candidate_decision_service_rejects_pending_candidate_with_comment() -> None:
    """Проверяет reject из pending с обязательным комментарием."""
    candidate = make_candidate()
    admin = make_admin()
    repository = InMemoryKickCandidateDecisionRepository([candidate])
    service = KickCandidateDecisionService(repository=repository)

    result = await service.reject_candidate(
        candidate_id=1,
        decision_by=admin,
        comment="оставляем игрока",
    )

    assert result.candidate is candidate
    assert repository.flush_count == 1
    assert candidate.status == KickCandidateStatus.REJECTED.value
    assert candidate.decision_by_telegram_user_id == 777
    assert candidate.decision_at is not None
    assert candidate.decision_comment == "оставляем игрока"


@pytest.mark.asyncio
async def test_kick_candidate_decision_service_postpones_pending_candidate() -> None:
    """Проверяет postpone с обязательными deadline и comment."""
    candidate = make_candidate()
    admin = make_admin()
    deadline_at = datetime.now(UTC) + timedelta(days=2)
    repository = InMemoryKickCandidateDecisionRepository([candidate])
    service = KickCandidateDecisionService(repository=repository)

    result = await service.postpone_candidate(
        candidate_id=1,
        decision_by=admin,
        deadline_at=deadline_at,
        comment="проверим позже",
    )

    assert result.candidate is candidate
    assert repository.flush_count == 1
    assert candidate.status == KickCandidateStatus.POSTPONED.value
    assert candidate.deadline_at == deadline_at
    assert candidate.decision_by_telegram_user_id == 777
    assert candidate.decision_comment == "проверим позже"


@pytest.mark.asyncio
async def test_kick_candidate_decision_service_marks_manual_required() -> None:
    """Проверяет перевод в manual_required."""
    candidate = make_candidate()
    admin = make_admin()
    repository = InMemoryKickCandidateDecisionRepository([candidate])
    service = KickCandidateDecisionService(repository=repository)

    result = await service.mark_manual_required(
        candidate_id=1,
        decision_by=admin,
        comment="нужно вручную удалить из чата",
    )

    assert result.candidate is candidate
    assert repository.flush_count == 1
    assert candidate.status == KickCandidateStatus.MANUAL_REQUIRED.value
    assert candidate.decision_by_telegram_user_id == 777
    assert candidate.decision_comment == "нужно вручную удалить из чата"
    assert candidate.executed_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "initial_status",
    [KickCandidateStatus.APPROVED, KickCandidateStatus.MANUAL_REQUIRED],
)
async def test_kick_candidate_decision_service_marks_executed_from_allowed_statuses(
    initial_status: KickCandidateStatus,
) -> None:
    """Проверяет executed только из approved/manual_required."""
    candidate = make_candidate(status=initial_status)
    admin = make_admin()
    repository = InMemoryKickCandidateDecisionRepository([candidate])
    service = KickCandidateDecisionService(repository=repository)

    result = await service.mark_executed(
        candidate_id=1,
        decision_by=admin,
        comment="выполнено",
    )

    assert result.candidate is candidate
    assert repository.flush_count == 1
    assert candidate.status == KickCandidateStatus.EXECUTED.value
    assert candidate.executed_at is not None
    assert candidate.decision_by_telegram_user_id == 777
    assert candidate.decision_comment == "выполнено"


@pytest.mark.asyncio
async def test_kick_candidate_decision_service_rejects_executed_from_pending() -> None:
    """Проверяет запрет executed напрямую из pending."""
    candidate = make_candidate(status=KickCandidateStatus.PENDING_ADMIN_DECISION)
    repository = InMemoryKickCandidateDecisionRepository([candidate])
    service = KickCandidateDecisionService(repository=repository)

    with pytest.raises(KickCandidateInvalidTransitionError):
        await service.mark_executed(
            candidate_id=1,
            decision_by=make_admin(),
            comment="нельзя",
        )

    assert candidate.status == KickCandidateStatus.PENDING_ADMIN_DECISION.value
    assert candidate.executed_at is None
    assert repository.flush_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_status",
    [KickCandidateStatus.REJECTED, KickCandidateStatus.EXECUTED],
)
async def test_kick_candidate_decision_service_rejects_transition_from_terminal_status(
    terminal_status: KickCandidateStatus,
) -> None:
    """Проверяет запрет переходов из terminal statuses."""
    candidate = make_candidate(status=terminal_status)
    repository = InMemoryKickCandidateDecisionRepository([candidate])
    service = KickCandidateDecisionService(repository=repository)

    with pytest.raises(KickCandidateInvalidTransitionError):
        await service.approve_candidate(
            candidate_id=1,
            decision_by=make_admin(),
        )

    assert candidate.status == terminal_status.value
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_kick_candidate_decision_service_requires_comment_for_reject() -> None:
    """Проверяет обязательный comment для reject."""
    candidate = make_candidate()
    repository = InMemoryKickCandidateDecisionRepository([candidate])
    service = KickCandidateDecisionService(repository=repository)

    with pytest.raises(KickCandidateDecisionError):
        await service.reject_candidate(
            candidate_id=1,
            decision_by=make_admin(),
            comment="   ",
        )

    assert candidate.status == KickCandidateStatus.PENDING_ADMIN_DECISION.value
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_kick_candidate_decision_service_requires_aware_deadline_for_postpone() -> None:
    """Проверяет обязательный timezone-aware deadline для postpone."""
    candidate = make_candidate()
    repository = InMemoryKickCandidateDecisionRepository([candidate])
    service = KickCandidateDecisionService(repository=repository)

    with pytest.raises(KickCandidateDecisionError):
        await service.postpone_candidate(
            candidate_id=1,
            decision_by=make_admin(),
            deadline_at=datetime(2026, 5, 1, 12, 0),
            comment="ждём",
        )

    assert candidate.status == KickCandidateStatus.PENDING_ADMIN_DECISION.value
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_kick_candidate_decision_service_raises_for_missing_candidate() -> None:
    """Проверяет ошибку для отсутствующего кандидата."""
    repository = InMemoryKickCandidateDecisionRepository()
    service = KickCandidateDecisionService(repository=repository)

    with pytest.raises(KickCandidateNotFoundError):
        await service.approve_candidate(
            candidate_id=404,
            decision_by=make_admin(),
        )

    assert repository.flush_count == 0
