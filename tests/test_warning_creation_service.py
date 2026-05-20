"""Тесты сервиса создания warn."""

import pytest

from app.db.models import Clan, TelegramUser, Warning
from app.domain import WarningReasonCode, WarningSource, WarningStatus
from app.services import (
    WarningAffectedAccount,
    WarningCreationError,
    WarningCreationResult,
    WarningCreationService,
)


class InMemoryWarningRepository:
    """In-memory repository для unit-тестов WarningCreationService."""

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
        """Добавляет warn в in-memory storage.

        Args:
            warning: Новая warn-запись.
        """
        self.added_warnings.append(warning)
        self.warnings.append(warning)

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


def make_affected_account(
    *,
    player_tag: str = "2abc",
    player_name: str = "Bangkok",
) -> WarningAffectedAccount:
    """Создаёт affected account для unit-тестов."""
    return WarningAffectedAccount(
        player_tag=player_tag,
        player_name=player_name,
    )


@pytest.mark.asyncio
async def test_warning_creation_service_creates_manual_warning_as_non_impactful() -> None:
    """Проверяет создание manual warn как non-impactful без event_key."""
    repository = InMemoryWarningRepository()
    service = WarningCreationService(repository=repository)
    telegram_user = make_telegram_user()
    author = make_telegram_user(user_id=202, telegram_id=777)
    clan = make_clan()

    result = await service.create_manual_warning(
        telegram_user=telegram_user,
        reason_code=WarningReasonCode.TOXICITY,
        author_telegram_user=author,
        clan=clan,
        affected_accounts=[],
        comment=" токсичность в чате ",
    )

    assert isinstance(result, WarningCreationResult)
    assert result.created is True
    assert repository.added_warnings == [result.warning]
    assert repository.flush_count == 1

    warning = result.warning
    assert warning.telegram_user_id == 101
    assert warning.telegram_user is telegram_user
    assert warning.source == WarningSource.MANUAL.value
    assert warning.status == WarningStatus.ACTIVE.value
    assert warning.reason_code == WarningReasonCode.TOXICITY.value
    assert warning.category == "discipline"
    assert warning.is_impactful is False
    assert warning.comment == "токсичность в чате"
    assert warning.author_telegram_user_id == 202
    assert warning.author is author
    assert warning.clan_id == 7
    assert warning.clan is clan
    assert warning.event_key is None
    assert warning.affected_player_tags_json == []
    assert warning.affected_player_names_json == []
    assert warning.created_cwl_season_key is None


@pytest.mark.asyncio
async def test_warning_creation_service_creates_system_warning_as_impactful() -> None:
    """Проверяет создание impactful system warn."""
    repository = InMemoryWarningRepository()
    service = WarningCreationService(repository=repository)
    telegram_user = make_telegram_user()
    clan = make_clan()

    result = await service.create_system_warning(
        telegram_user=telegram_user,
        reason_code="war_attack_missed",
        event_key="warn:auto:war_attack_missed:#MAIN:war-key:101",
        affected_accounts=[
            make_affected_account(player_tag="2abc", player_name="Bangkok"),
            make_affected_account(player_tag="#9xyz", player_name="Phoenix"),
        ],
        clan=clan,
        created_cwl_season_key="2026-05",
    )

    assert result.created is True
    assert repository.flush_count == 1

    warning = result.warning
    assert warning.source == WarningSource.SYSTEM.value
    assert warning.status == WarningStatus.ACTIVE.value
    assert warning.reason_code == WarningReasonCode.WAR_ATTACK_MISSED.value
    assert warning.category == "war"
    assert warning.is_impactful is True
    assert warning.event_key == "warn:auto:war_attack_missed:#MAIN:war-key:101"
    assert warning.affected_player_tags_json == ["#2ABC", "#9XYZ"]
    assert warning.affected_player_names_json == ["Bangkok", "Phoenix"]
    assert warning.created_cwl_season_key == "2026-05"


@pytest.mark.asyncio
async def test_warning_creation_service_deduplicates_by_event_key() -> None:
    """Проверяет dedup по event_key и возврат существующего warn."""
    existing_warning = Warning(
        telegram_user_id=101,
        source=WarningSource.SYSTEM.value,
        status=WarningStatus.ACTIVE.value,
        reason_code=WarningReasonCode.RAID_MISSED.value,
        category="raid",
        is_impactful=True,
        event_key="warn:auto:raid_missed:#MAIN:raid-start:101",
        affected_player_tags_json=["#2ABC"],
        affected_player_names_json=["Bangkok"],
    )
    repository = InMemoryWarningRepository([existing_warning])
    service = WarningCreationService(repository=repository)

    result = await service.create_system_warning(
        telegram_user=make_telegram_user(),
        reason_code=WarningReasonCode.RAID_MISSED,
        event_key="warn:auto:raid_missed:#MAIN:raid-start:101",
        affected_accounts=[make_affected_account()],
    )

    assert result.created is False
    assert result.warning is existing_warning
    assert repository.added_warnings == []
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_warning_creation_service_deduplicates_manual_warning_when_event_key_passed() -> None:
    """Проверяет dedup manual warn при явно переданном event_key."""
    existing_warning = Warning(
        telegram_user_id=101,
        source=WarningSource.MANUAL.value,
        status=WarningStatus.ACTIVE.value,
        reason_code=WarningReasonCode.SPAM.value,
        category="discipline",
        is_impactful=False,
        event_key="manual:spam:101:message-1",
        affected_player_tags_json=[],
        affected_player_names_json=[],
    )
    repository = InMemoryWarningRepository([existing_warning])
    service = WarningCreationService(repository=repository)

    result = await service.create_manual_warning(
        telegram_user=make_telegram_user(),
        reason_code=WarningReasonCode.SPAM,
        event_key="manual:spam:101:message-1",
    )

    assert result.created is False
    assert result.warning is existing_warning
    assert repository.added_warnings == []
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_warning_creation_service_requires_affected_accounts_for_system_warning() -> None:
    """Проверяет обязательные affected accounts для system warn."""
    repository = InMemoryWarningRepository()
    service = WarningCreationService(repository=repository)

    with pytest.raises(WarningCreationError):
        await service.create_system_warning(
            telegram_user=make_telegram_user(),
            reason_code=WarningReasonCode.CWL_ATTACK_MISSED,
            event_key="warn:auto:cwl_attack_missed:#MAIN:#WAR1:101",
            affected_accounts=[],
        )

    assert repository.added_warnings == []
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_warning_creation_service_rejects_manual_warning_with_system_reason() -> None:
    """Проверяет запрет system reason для manual warn."""
    repository = InMemoryWarningRepository()
    service = WarningCreationService(repository=repository)

    with pytest.raises(WarningCreationError):
        await service.create_manual_warning(
            telegram_user=make_telegram_user(),
            reason_code=WarningReasonCode.WAR_ATTACK_MISSED,
        )

    assert repository.added_warnings == []
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_warning_creation_service_rejects_system_warning_with_manual_reason() -> None:
    """Проверяет запрет manual reason для system warn."""
    repository = InMemoryWarningRepository()
    service = WarningCreationService(repository=repository)

    with pytest.raises(WarningCreationError):
        await service.create_system_warning(
            telegram_user=make_telegram_user(),
            reason_code=WarningReasonCode.SPAM,
            event_key="warn:auto:spam:#MAIN:event:101",
            affected_accounts=[make_affected_account()],
        )

    assert repository.added_warnings == []
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_warning_creation_service_rejects_duplicate_affected_accounts() -> None:
    """Проверяет запрет дублей affected accounts."""
    repository = InMemoryWarningRepository()
    service = WarningCreationService(repository=repository)

    with pytest.raises(WarningCreationError):
        await service.create_system_warning(
            telegram_user=make_telegram_user(),
            reason_code=WarningReasonCode.RAID_INCOMPLETE,
            event_key="warn:auto:raid_incomplete:#MAIN:raid-start:101",
            affected_accounts=[
                make_affected_account(player_tag="2abc"),
                make_affected_account(player_tag="#2ABC"),
            ],
        )

    assert repository.added_warnings == []
    assert repository.flush_count == 0
