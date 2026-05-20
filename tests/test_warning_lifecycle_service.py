"""Тесты сервиса отмены и истечения warn."""

from datetime import UTC, datetime

import pytest

from app.db.models import TelegramUser, Warning
from app.domain import WarningReasonCode, WarningSource, WarningStatus
from app.services import (
    WarningCancellationResult,
    WarningExpirationResult,
    WarningLifecycleError,
    WarningLifecycleService,
    WarningNotFoundError,
)


class InMemoryWarningLifecycleRepository:
    """In-memory repository для unit-тестов WarningLifecycleService."""

    def __init__(self, warnings: list[Warning] | None = None) -> None:
        """Инициализирует repository.

        Args:
            warnings: Начальный набор warn-записей.
        """
        self.warnings = {warning.id: warning for warning in warnings or []}
        self.flush_count = 0

    async def get_by_id(self, warning_id: int) -> Warning | None:
        """Возвращает warn по DB ID.

        Args:
            warning_id: DB ID warn.

        Returns:
            Warn или `None`.
        """
        return self.warnings.get(warning_id)

    async def list_active_expirable_warnings(self, *, current_cwl_season_key: str) -> list[Warning]:
        """Возвращает active warn, которые должны истечь."""
        return [
            warning
            for warning in self.warnings.values()
            if warning.status == WarningStatus.ACTIVE.value
            and warning.created_cwl_season_key != current_cwl_season_key
        ]

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


def make_warning(
    *,
    warning_id: int = 1,
    status: WarningStatus = WarningStatus.ACTIVE,
    reason_code: WarningReasonCode = WarningReasonCode.RAID_MISSED,
    created_cwl_season_key: str | None = "2026-05",
) -> Warning:
    """Создаёт Warning для unit-тестов."""
    return Warning(
        id=warning_id,
        telegram_user_id=101,
        source=WarningSource.SYSTEM.value,
        status=status.value,
        reason_code=reason_code.value,
        category="raid",
        is_impactful=True,
        event_key=f"warn:auto:{reason_code.value}:#MAIN:event:{warning_id}",
        affected_player_tags_json=["#2ABC"],
        affected_player_names_json=["Bangkok"],
        created_cwl_season_key=created_cwl_season_key,
    )


def make_admin(*, user_id: int = 777, telegram_id: int = 123456789) -> TelegramUser:
    """Создаёт TelegramUser администратора для unit-тестов."""
    return TelegramUser(
        id=user_id,
        telegram_id=telegram_id,
        username="admin",
        display_name="Admin",
    )


@pytest.mark.asyncio
async def test_warning_lifecycle_service_cancels_active_warning() -> None:
    """Проверяет отмену active warn."""
    warning = make_warning()
    admin = make_admin()
    repository = InMemoryWarningLifecycleRepository([warning])
    service = WarningLifecycleService(repository=repository)

    result = await service.cancel_warning(
        warning_id=1,
        cancelled_by=admin,
        reason=" ошибочный warn ",
    )

    assert isinstance(result, WarningCancellationResult)
    assert result.warning is warning
    assert result.changed is True
    assert repository.flush_count == 1
    assert warning.status == WarningStatus.CANCELLED.value
    assert warning.cancelled_at is not None
    assert warning.cancelled_by_telegram_user_id == 777
    assert warning.cancelled_by is admin
    assert warning.cancelled_reason == "ошибочный warn"


@pytest.mark.asyncio
async def test_warning_lifecycle_service_repeated_cancel_is_idempotent() -> None:
    """Проверяет повторную отмену already-cancelled warn без перезаписи."""
    cancelled_at = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    warning = make_warning(status=WarningStatus.CANCELLED)
    warning.cancelled_at = cancelled_at
    warning.cancelled_by_telegram_user_id = 111
    warning.cancelled_reason = "old reason"

    repository = InMemoryWarningLifecycleRepository([warning])
    service = WarningLifecycleService(repository=repository)

    result = await service.cancel_warning(
        warning_id=1,
        cancelled_by=make_admin(user_id=222),
        reason="new reason",
    )

    assert result.warning is warning
    assert result.changed is False
    assert repository.flush_count == 0
    assert warning.status == WarningStatus.CANCELLED.value
    assert warning.cancelled_at == cancelled_at
    assert warning.cancelled_by_telegram_user_id == 111
    assert warning.cancelled_reason == "old reason"


@pytest.mark.asyncio
async def test_warning_lifecycle_service_rejects_cancel_for_expired_warning() -> None:
    """Проверяет запрет отмены expired warn."""
    warning = make_warning(status=WarningStatus.EXPIRED)
    repository = InMemoryWarningLifecycleRepository([warning])
    service = WarningLifecycleService(repository=repository)

    with pytest.raises(WarningLifecycleError):
        await service.cancel_warning(
            warning_id=1,
            cancelled_by=make_admin(),
            reason="late cancel",
        )

    assert warning.status == WarningStatus.EXPIRED.value
    assert warning.cancelled_at is None
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_warning_lifecycle_service_expires_old_and_no_season_active_warnings() -> None:
    """Проверяет истечение active warn другого season и warn без season."""
    old_season_warning = make_warning(warning_id=1, created_cwl_season_key="2026-04")
    no_season_warning = make_warning(warning_id=2, created_cwl_season_key=None)
    current_season_warning = make_warning(warning_id=3, created_cwl_season_key="2026-05")
    cancelled_warning = make_warning(warning_id=4, status=WarningStatus.CANCELLED)
    repository = InMemoryWarningLifecycleRepository(
        [
            old_season_warning,
            no_season_warning,
            current_season_warning,
            cancelled_warning,
        ]
    )
    service = WarningLifecycleService(repository=repository)

    result = await service.expire_warnings_by_cwl_season(current_cwl_season_key="2026-05")

    assert isinstance(result, WarningExpirationResult)
    assert result.expired_count == 2
    assert result.expired_warnings == [old_season_warning, no_season_warning]
    assert repository.flush_count == 1
    assert old_season_warning.status == WarningStatus.EXPIRED.value
    assert no_season_warning.status == WarningStatus.EXPIRED.value
    assert old_season_warning.expired_at is not None
    assert no_season_warning.expired_at is not None
    assert current_season_warning.status == WarningStatus.ACTIVE.value
    assert current_season_warning.expired_at is None
    assert cancelled_warning.status == WarningStatus.CANCELLED.value
    assert cancelled_warning.expired_at is None


@pytest.mark.asyncio
async def test_warning_lifecycle_service_expiration_is_idempotent_without_old_warnings() -> None:
    """Проверяет идемпотентность expiration при отсутствии подходящих warn."""
    current_season_warning = make_warning(warning_id=1, created_cwl_season_key="2026-05")
    expired_warning = make_warning(warning_id=2, status=WarningStatus.EXPIRED)
    cancelled_warning = make_warning(warning_id=3, status=WarningStatus.CANCELLED)
    repository = InMemoryWarningLifecycleRepository(
        [current_season_warning, expired_warning, cancelled_warning]
    )
    service = WarningLifecycleService(repository=repository)

    result = await service.expire_warnings_by_cwl_season(current_cwl_season_key="2026-05")

    assert result == WarningExpirationResult(expired_warnings=[], expired_count=0)
    assert repository.flush_count == 0
    assert current_season_warning.status == WarningStatus.ACTIVE.value
    assert expired_warning.status == WarningStatus.EXPIRED.value
    assert cancelled_warning.status == WarningStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_warning_lifecycle_service_raises_for_missing_warning() -> None:
    """Проверяет ошибку при отмене отсутствующего warn."""
    repository = InMemoryWarningLifecycleRepository()
    service = WarningLifecycleService(repository=repository)

    with pytest.raises(WarningNotFoundError):
        await service.cancel_warning(
            warning_id=404,
            cancelled_by=make_admin(),
            reason="not found",
        )

    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_warning_lifecycle_service_rejects_empty_cancel_reason() -> None:
    """Проверяет обязательную причину отмены."""
    warning = make_warning()
    repository = InMemoryWarningLifecycleRepository([warning])
    service = WarningLifecycleService(repository=repository)

    with pytest.raises(WarningLifecycleError):
        await service.cancel_warning(
            warning_id=1,
            cancelled_by=make_admin(),
            reason="   ",
        )

    assert warning.status == WarningStatus.ACTIVE.value
    assert repository.flush_count == 0
