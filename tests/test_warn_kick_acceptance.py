"""Acceptance-тесты warn и kick candidate service-сценариев."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.db.models import KickCandidate, PlayerAccount, TelegramUser, Warning
from app.domain import WarningReasonCode, WarningSource, WarningStatus, normalize_player_tag
from app.services import (
    KickCandidateService,
    WarningAffectedAccount,
    WarningCreationService,
)


class AcceptanceWarningRepository:
    """In-memory repository для acceptance-тестов warn."""

    def __init__(self, warnings: list[Warning] | None = None) -> None:
        """Инициализирует repository.

        Args:
            warnings: Начальный набор warn-записей.
        """
        self.warnings = warnings or []
        self.added_warnings: list[Warning] = []
        self.flush_count = 0

    async def get_by_event_key(self, event_key: str) -> Warning | None:
        """Возвращает warn по event key."""
        for warning in self.warnings:
            if warning.event_key == event_key:
                return warning

        return None

    def add(self, warning: Warning) -> None:
        """Добавляет warn в in-memory storage."""
        self.added_warnings.append(warning)
        self.warnings.append(warning)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


class AcceptanceKickCandidateRepository:
    """In-memory repository для acceptance-тестов kick candidates."""

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


@dataclass(frozen=True, slots=True)
class ResolvedWarnTarget:
    """Результат test-only разрешения цели `/warn`."""

    telegram_user: TelegramUser
    affected_account: WarningAffectedAccount


class FakeWarnTargetResolver:
    """Test-only resolver цели `/warn` без Telegram API и Clash API."""

    def __init__(
        self,
        *,
        accounts: list[PlayerAccount],
        telegram_users_by_id: dict[int, TelegramUser],
    ) -> None:
        """Инициализирует resolver.

        Args:
            accounts: Известные игровые аккаунты.
            telegram_users_by_id: TelegramUser по DB ID.
        """
        self._accounts = {normalize_player_tag(account.player_tag): account for account in accounts}
        self._telegram_users_by_id = telegram_users_by_id

    async def resolve(self, player_tag: str) -> ResolvedWarnTarget | None:
        """Разрешает player tag в TelegramUser.

        Args:
            player_tag: Тег игрового аккаунта.

        Returns:
            Цель warn или `None`, если аккаунт не привязан.
        """
        normalized_player_tag = normalize_player_tag(player_tag)
        account = self._accounts.get(normalized_player_tag)

        if account is None or account.telegram_user_id is None:
            return None

        telegram_user = self._telegram_users_by_id.get(account.telegram_user_id)
        if telegram_user is None:
            return None

        return ResolvedWarnTarget(
            telegram_user=telegram_user,
            affected_account=WarningAffectedAccount(
                player_tag=account.player_tag,
                player_name=account.name,
            ),
        )


@dataclass(frozen=True, slots=True)
class FakeWarnCommandResult:
    """Результат test-only `/warn` command flow."""

    accepted: bool
    warning_created: bool
    reason: str | None


class FakeManualWarnCommandFlow:
    """Test-only `/warn` flow до появления real Telegram handler."""

    def __init__(
        self,
        *,
        target_resolver: FakeWarnTargetResolver,
        warning_service: WarningCreationService,
    ) -> None:
        """Инициализирует fake flow.

        Args:
            target_resolver: Test-only resolver цели warn.
            warning_service: Сервис создания warn.
        """
        self._target_resolver = target_resolver
        self._warning_service = warning_service
        self._pending_target: ResolvedWarnTarget | None = None

    async def start_warn(self, *, player_tag: str) -> FakeWarnCommandResult:
        """Начинает `/warn`, но не создаёт запись без выбранной причины.

        Args:
            player_tag: Тег цели.

        Returns:
            Результат fake flow.
        """
        target = await self._target_resolver.resolve(player_tag)
        if target is None:
            return FakeWarnCommandResult(
                accepted=False,
                warning_created=False,
                reason="target_not_linked",
            )

        self._pending_target = target
        return FakeWarnCommandResult(
            accepted=True,
            warning_created=False,
            reason="reason_required",
        )

    async def choose_reason(self, *, reason_code: WarningReasonCode) -> FakeWarnCommandResult:
        """Выбирает причину и создаёт manual warn.

        Args:
            reason_code: Причина manual warn.

        Returns:
            Результат fake flow.

        Raises:
            RuntimeError: Если цель не была выбрана.
        """
        if self._pending_target is None:
            raise RuntimeError("Перед выбором причины нужно выбрать цель warn.")

        result = await self._warning_service.create_manual_warning(
            telegram_user=self._pending_target.telegram_user,
            reason_code=reason_code,
            affected_accounts=[self._pending_target.affected_account],
        )

        return FakeWarnCommandResult(
            accepted=True,
            warning_created=result.created,
            reason=None,
        )


def make_telegram_user(*, user_id: int = 101, telegram_id: int = 42) -> TelegramUser:
    """Создаёт TelegramUser для acceptance-тестов."""
    return TelegramUser(
        id=user_id,
        telegram_id=telegram_id,
        username="bangkok",
        display_name="Bangkok",
    )


def make_player_account(
    *,
    player_tag: str = "#2ABC",
    telegram_user_id: int | None = 101,
    name: str = "Bangkok",
) -> PlayerAccount:
    """Создаёт PlayerAccount для acceptance-тестов."""
    return PlayerAccount(
        telegram_user_id=telegram_user_id,
        player_tag=normalize_player_tag(player_tag),
        name=name,
        is_active=True,
        linked_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
    )


def make_system_warning(
    *,
    warning_id: int,
    telegram_user_id: int = 101,
    event_key: str,
    reason_code: WarningReasonCode = WarningReasonCode.RAID_MISSED,
) -> Warning:
    """Создаёт active impactful system warn для acceptance-тестов."""
    return Warning(
        id=warning_id,
        telegram_user_id=telegram_user_id,
        source=WarningSource.SYSTEM.value,
        status=WarningStatus.ACTIVE.value,
        reason_code=reason_code.value,
        category="raid",
        is_impactful=True,
        event_key=event_key,
        affected_player_tags_json=["#2ABC"],
        affected_player_names_json=["Bangkok"],
    )


@pytest.mark.asyncio
async def test_warn_command_waits_for_reason_before_creating_warning() -> None:
    """Проверяет, что `/warn`-аналог не создаёт запись до выбора причины."""
    telegram_user = make_telegram_user()
    account = make_player_account(telegram_user_id=telegram_user.id)
    warning_repository = AcceptanceWarningRepository()
    flow = FakeManualWarnCommandFlow(
        target_resolver=FakeWarnTargetResolver(
            accounts=[account],
            telegram_users_by_id={telegram_user.id: telegram_user},
        ),
        warning_service=WarningCreationService(repository=warning_repository),
    )

    result = await flow.start_warn(player_tag="2abc")

    assert result == FakeWarnCommandResult(
        accepted=True,
        warning_created=False,
        reason="reason_required",
    )
    assert warning_repository.warnings == []
    assert warning_repository.added_warnings == []
    assert warning_repository.flush_count == 0


@pytest.mark.asyncio
async def test_warn_command_rejects_unlinked_account_target() -> None:
    """Проверяет, что `/warn` нельзя выдать непривязанному аккаунту."""
    account = make_player_account(telegram_user_id=None)
    warning_repository = AcceptanceWarningRepository()
    flow = FakeManualWarnCommandFlow(
        target_resolver=FakeWarnTargetResolver(accounts=[account], telegram_users_by_id={}),
        warning_service=WarningCreationService(repository=warning_repository),
    )

    result = await flow.start_warn(player_tag="#2abc")

    assert result == FakeWarnCommandResult(
        accepted=False,
        warning_created=False,
        reason="target_not_linked",
    )
    assert warning_repository.warnings == []
    assert warning_repository.added_warnings == []
    assert warning_repository.flush_count == 0


@pytest.mark.asyncio
async def test_manual_warning_does_not_create_kick_candidate() -> None:
    """Проверяет, что manual warn не создаёт kick candidate."""
    telegram_user = make_telegram_user()
    warning_repository = AcceptanceWarningRepository()
    warning_service = WarningCreationService(repository=warning_repository)

    manual_result = await warning_service.create_manual_warning(
        telegram_user=telegram_user,
        reason_code=WarningReasonCode.SPAM,
        affected_accounts=[],
    )
    system_result = await warning_service.create_system_warning(
        telegram_user=telegram_user,
        reason_code=WarningReasonCode.RAID_MISSED,
        event_key="warn:auto:raid_missed:#MAIN:raid-start:101",
        affected_accounts=[WarningAffectedAccount(player_tag="2abc", player_name="Bangkok")],
    )

    kick_repository = AcceptanceKickCandidateRepository()
    kick_service = KickCandidateService(repository=kick_repository)

    candidate_result = await kick_service.create_for_two_impactful_warnings(
        telegram_user=telegram_user,
        warnings=[manual_result.warning, system_result.warning],
        season_key="2026-05",
    )

    assert candidate_result.candidate is None
    assert candidate_result.created is False
    assert candidate_result.reason == "not_enough_impactful_warnings"
    assert kick_repository.candidates == []
    assert kick_repository.added_candidates == []
    assert kick_repository.flush_count == 0


@pytest.mark.asyncio
async def test_two_active_impactful_warns_create_kick_candidate() -> None:
    """Проверяет, что 2 active impactful warn создают candidate."""
    telegram_user = make_telegram_user()
    warnings = [
        make_system_warning(
            warning_id=1,
            event_key="warn:auto:raid_missed:#MAIN:raid-start:101",
        ),
        make_system_warning(
            warning_id=2,
            event_key="warn:auto:cwl_attack_missed:#MAIN:#WAR1:101",
            reason_code=WarningReasonCode.CWL_ATTACK_MISSED,
        ),
    ]
    kick_repository = AcceptanceKickCandidateRepository()
    kick_service = KickCandidateService(repository=kick_repository)

    result = await kick_service.create_for_two_impactful_warnings(
        telegram_user=telegram_user,
        warnings=warnings,
        season_key="2026-05",
    )

    assert result.created is True
    assert result.candidate is not None
    assert kick_repository.added_candidates == [result.candidate]
    assert kick_repository.flush_count == 1
    assert result.candidate.telegram_user_id == 101
    assert result.candidate.event_key == "kick:2_impactful_warn:101:2026-05"


@pytest.mark.asyncio
async def test_warn_event_key_prevents_duplicate_warning() -> None:
    """Проверяет, что одинаковый event_key не создаёт дубль warn."""
    telegram_user = make_telegram_user()
    warning_repository = AcceptanceWarningRepository()
    warning_service = WarningCreationService(repository=warning_repository)
    event_key = "warn:auto:raid_incomplete:#MAIN:raid-start:101"

    first_result = await warning_service.create_system_warning(
        telegram_user=telegram_user,
        reason_code=WarningReasonCode.RAID_INCOMPLETE,
        event_key=event_key,
        affected_accounts=[WarningAffectedAccount(player_tag="2abc", player_name="Bangkok")],
    )
    second_result = await warning_service.create_system_warning(
        telegram_user=telegram_user,
        reason_code=WarningReasonCode.RAID_INCOMPLETE,
        event_key=event_key,
        affected_accounts=[WarningAffectedAccount(player_tag="2abc", player_name="Bangkok")],
    )

    assert first_result.created is True
    assert second_result.created is False
    assert second_result.warning is first_result.warning
    assert warning_repository.warnings == [first_result.warning]
    assert warning_repository.added_warnings == [first_result.warning]
    assert warning_repository.flush_count == 1


@pytest.mark.asyncio
async def test_kick_event_key_prevents_duplicate_candidate() -> None:
    """Проверяет, что одинаковый event_key не создаёт дубль kick candidate."""
    telegram_user = make_telegram_user()
    warnings = [
        make_system_warning(
            warning_id=1,
            event_key="warn:auto:raid_missed:#MAIN:raid-start:101",
        ),
        make_system_warning(
            warning_id=2,
            event_key="warn:auto:war_attack_missed:#MAIN:war-key:101",
            reason_code=WarningReasonCode.WAR_ATTACK_MISSED,
        ),
    ]
    kick_repository = AcceptanceKickCandidateRepository()
    kick_service = KickCandidateService(repository=kick_repository)

    first_result = await kick_service.create_for_two_impactful_warnings(
        telegram_user=telegram_user,
        warnings=warnings,
        season_key="2026-05",
    )
    second_result = await kick_service.create_for_two_impactful_warnings(
        telegram_user=telegram_user,
        warnings=warnings,
        season_key="2026-05",
    )

    assert first_result.created is True
    assert second_result.created is False
    assert second_result.candidate is first_result.candidate
    assert kick_repository.candidates == [first_result.candidate]
    assert kick_repository.added_candidates == [first_result.candidate]
    assert kick_repository.flush_count == 1
