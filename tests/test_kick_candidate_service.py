"""Тесты сервиса кандидатов на кик."""

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Clan, KickCandidate, TelegramUser, Warning
from app.domain import (
    KickCandidateReasonCode,
    KickCandidateStatus,
    WarningReasonCode,
    WarningSource,
    WarningStatus,
)
from app.services import (
    KickCandidateCreationResult,
    KickCandidateService,
    KickCandidateServiceError,
)


class InMemoryKickCandidateRepository:
    """In-memory repository для unit-тестов KickCandidateService."""

    def __init__(self, candidates: list[KickCandidate] | None = None) -> None:
        """Инициализирует repository.

        Args:
            candidates: Начальный набор кандидатов.
        """
        self.candidates = candidates or []
        self.added_candidates: list[KickCandidate] = []
        self.flush_count = 0

    async def get_by_event_key(self, event_key: str) -> KickCandidate | None:
        """Возвращает кандидата по event key."""
        for candidate in self.candidates:
            if candidate.event_key == event_key:
                return candidate

        return None

    def add(self, candidate: KickCandidate) -> None:
        """Добавляет кандидата в in-memory storage."""
        self.added_candidates.append(candidate)
        self.candidates.append(candidate)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


def make_telegram_user(*, user_id: int = 101, telegram_id: int = 42) -> TelegramUser:
    """Создаёт TelegramUser для unit-тестов."""
    return TelegramUser(
        id=user_id,
        telegram_id=telegram_id,
        username="bangkok",
        display_name="Bangkok",
    )


def make_clan(*, clan_id: int = 7) -> Clan:
    """Создаёт Clan для unit-тестов."""
    return Clan(
        id=clan_id,
        tag="#MAIN",
        name="Bestiary",
        type="main",
        is_active=True,
    )


def make_warning(
    *,
    warning_id: int,
    telegram_user_id: int = 101,
    source: WarningSource = WarningSource.SYSTEM,
    status: WarningStatus = WarningStatus.ACTIVE,
    is_impactful: bool = True,
    reason_code: WarningReasonCode = WarningReasonCode.RAID_MISSED,
) -> Warning:
    """Создаёт Warning для unit-тестов."""
    return Warning(
        id=warning_id,
        telegram_user_id=telegram_user_id,
        source=source.value,
        status=status.value,
        reason_code=reason_code.value,
        category="raid",
        is_impactful=is_impactful,
        affected_player_tags_json=["#2ABC"],
        affected_player_names_json=["Bangkok"],
    )


@pytest.mark.asyncio
async def test_kick_candidate_service_creates_candidate_for_two_active_impactful_warnings() -> None:
    """Проверяет кандидата по двум active impactful warn."""
    repository = InMemoryKickCandidateRepository()
    service = KickCandidateService(repository=repository)
    telegram_user = make_telegram_user()
    warnings = [
        make_warning(warning_id=1),
        make_warning(warning_id=2, reason_code=WarningReasonCode.CWL_ATTACK_MISSED),
    ]

    result = await service.create_for_two_impactful_warnings(
        telegram_user=telegram_user,
        warnings=warnings,
        season_key="2026-05",
    )

    assert isinstance(result, KickCandidateCreationResult)
    assert result.created is True
    assert result.reason is None
    assert result.candidate is not None
    assert repository.added_candidates == [result.candidate]
    assert repository.flush_count == 1

    candidate = result.candidate
    assert candidate.telegram_user_id == 101
    assert candidate.telegram_user is telegram_user
    assert candidate.player_tag is None
    assert candidate.reason_code == KickCandidateReasonCode.TWO_IMPACTFUL_WARN.value
    assert candidate.status == KickCandidateStatus.PENDING_ADMIN_DECISION.value
    assert candidate.event_key == "kick:2_impactful_warn:101:2026-05"


@pytest.mark.asyncio
async def test_kick_candidate_service_manual_warning_does_not_create_candidate() -> None:
    """Проверяет, что manual warn не создаёт кандидата по двум warn."""
    repository = InMemoryKickCandidateRepository()
    service = KickCandidateService(repository=repository)
    warnings = [
        make_warning(warning_id=1),
        make_warning(
            warning_id=2,
            source=WarningSource.MANUAL,
            is_impactful=False,
            reason_code=WarningReasonCode.SPAM,
        ),
    ]

    result = await service.create_for_two_impactful_warnings(
        telegram_user=make_telegram_user(),
        warnings=warnings,
        season_key="2026-05",
    )

    assert result == KickCandidateCreationResult(
        candidate=None,
        created=False,
        reason="not_enough_impactful_warnings",
    )
    assert repository.added_candidates == []
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_kick_candidate_service_creates_candidate_for_unlinked_account_after_3_days() -> None:
    """Проверяет кандидата по непривязанному аккаунту через 3 дня."""
    repository = InMemoryKickCandidateRepository()
    service = KickCandidateService(repository=repository)
    first_seen_at = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    observed_at = first_seen_at + timedelta(days=3)

    result = await service.create_for_unlinked_account(
        clan=make_clan(clan_id=7),
        player_tag="2abc",
        first_seen_at=first_seen_at,
        observed_at=observed_at,
    )

    assert result.created is True
    assert result.candidate is not None
    assert repository.flush_count == 1

    candidate = result.candidate
    assert candidate.telegram_user_id is None
    assert candidate.player_tag == "#2ABC"
    assert candidate.reason_code == KickCandidateReasonCode.UNLINKED_AFTER_3_DAYS.value
    assert candidate.status == KickCandidateStatus.PENDING_ADMIN_DECISION.value
    assert candidate.event_key == "kick:unlinked_after_3_days:7:#2ABC"


@pytest.mark.asyncio
async def test_kick_candidate_service_does_not_create_unlinked_candidate_before_3_days() -> None:
    """Проверяет, что непривязанный аккаунт младше 3 дней не создаёт кандидата."""
    repository = InMemoryKickCandidateRepository()
    service = KickCandidateService(repository=repository)
    first_seen_at = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    observed_at = first_seen_at + timedelta(days=2, hours=23)

    result = await service.create_for_unlinked_account(
        clan=make_clan(clan_id=7),
        player_tag="2abc",
        first_seen_at=first_seen_at,
        observed_at=observed_at,
    )

    assert result == KickCandidateCreationResult(
        candidate=None,
        created=False,
        reason="unlinked_account_not_old_enough",
    )
    assert repository.added_candidates == []
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_kick_candidate_service_creates_candidate_for_all_accounts_left() -> None:
    """Проверяет кандидата по уходу всех аккаунтов пользователя."""
    repository = InMemoryKickCandidateRepository()
    service = KickCandidateService(repository=repository)
    telegram_user = make_telegram_user(user_id=101)

    result = await service.create_for_all_accounts_left(telegram_user=telegram_user)

    assert result.created is True
    assert result.candidate is not None
    assert result.candidate.telegram_user_id == 101
    assert result.candidate.reason_code == KickCandidateReasonCode.ALL_ACCOUNTS_LEFT.value
    assert result.candidate.event_key == "kick:all_accounts_left:101"


@pytest.mark.asyncio
async def test_kick_candidate_service_creates_candidate_for_linked_account_left() -> None:
    """Проверяет кандидата по уходу одного привязанного аккаунта."""
    repository = InMemoryKickCandidateRepository()
    service = KickCandidateService(repository=repository)
    telegram_user = make_telegram_user(user_id=101)

    result = await service.create_for_linked_account_left(
        telegram_user=telegram_user,
        player_tag="2abc",
    )

    assert result.created is True
    assert result.candidate is not None
    assert result.candidate.telegram_user_id == 101
    assert result.candidate.player_tag == "#2ABC"
    assert result.candidate.reason_code == KickCandidateReasonCode.LINKED_ACCOUNT_LEFT.value
    assert result.candidate.event_key == "kick:linked_account_left:101:#2ABC"


@pytest.mark.asyncio
async def test_kick_candidate_service_creates_manual_recommendation_without_event_key() -> None:
    """Проверяет ручную рекомендацию без обязательного event_key."""
    repository = InMemoryKickCandidateRepository()
    service = KickCandidateService(repository=repository)
    telegram_user = make_telegram_user(user_id=101)
    created_by = make_telegram_user(user_id=777, telegram_id=123456789)

    result = await service.create_manual_recommendation(
        telegram_user=telegram_user,
        created_by=created_by,
    )

    assert result.created is True
    assert result.candidate is not None

    candidate = result.candidate
    assert candidate.telegram_user_id == 101
    assert candidate.telegram_user is telegram_user
    assert candidate.player_tag is None
    assert candidate.reason_code == KickCandidateReasonCode.MANUAL_RECOMMENDATION.value
    assert candidate.status == KickCandidateStatus.PENDING_ADMIN_DECISION.value
    assert candidate.event_key is None
    assert candidate.created_by == 777
    assert candidate.created_by_user is created_by


@pytest.mark.asyncio
async def test_kick_candidate_service_deduplicates_by_event_key() -> None:
    """Проверяет dedup повторного вызова по event_key."""
    existing_candidate = KickCandidate(
        telegram_user_id=101,
        reason_code=KickCandidateReasonCode.TWO_IMPACTFUL_WARN.value,
        status=KickCandidateStatus.PENDING_ADMIN_DECISION.value,
        event_key="kick:2_impactful_warn:101:2026-05",
    )
    repository = InMemoryKickCandidateRepository([existing_candidate])
    service = KickCandidateService(repository=repository)

    result = await service.create_for_two_impactful_warnings(
        telegram_user=make_telegram_user(user_id=101),
        warnings=[
            make_warning(warning_id=1),
            make_warning(warning_id=2, reason_code=WarningReasonCode.CWL_ATTACK_MISSED),
        ],
        season_key="2026-05",
    )

    assert result.created is False
    assert result.candidate is existing_candidate
    assert result.reason is None
    assert repository.added_candidates == []
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_kick_candidate_service_deduplicates_manual_recommendation() -> None:
    """Проверяет dedup ручной рекомендации при явно переданном event_key."""
    existing_candidate = KickCandidate(
        telegram_user_id=101,
        reason_code=KickCandidateReasonCode.MANUAL_RECOMMENDATION.value,
        status=KickCandidateStatus.PENDING_ADMIN_DECISION.value,
        event_key="manual:kick:101:case-1",
    )
    repository = InMemoryKickCandidateRepository([existing_candidate])
    service = KickCandidateService(repository=repository)

    result = await service.create_manual_recommendation(
        telegram_user=make_telegram_user(user_id=101),
        event_key="manual:kick:101:case-1",
    )

    assert result.created is False
    assert result.candidate is existing_candidate
    assert repository.added_candidates == []
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_kick_candidate_service_rejects_manual_recommendation_without_target() -> None:
    """Проверяет запрет ручной рекомендации без цели."""
    repository = InMemoryKickCandidateRepository()
    service = KickCandidateService(repository=repository)

    with pytest.raises(KickCandidateServiceError):
        await service.create_manual_recommendation()

    assert repository.added_candidates == []
    assert repository.flush_count == 0
