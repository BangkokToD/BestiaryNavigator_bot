"""Сервис отмены и истечения warn."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CwlSeason, TelegramUser, Warning
from app.domain import WarningSource, WarningStatus


class WarningLifecycleError(RuntimeError):
    """Базовая ошибка сервиса жизненного цикла warn."""


class WarningNotFoundError(WarningLifecycleError):
    """Warn не найден."""


@dataclass(frozen=True, slots=True)
class WarningCancellationResult:
    """Результат отмены warn."""

    warning: Warning
    changed: bool


@dataclass(frozen=True, slots=True)
class WarningExpirationResult:
    """Результат истечения warn по CWL season."""

    expired_warnings: list[Warning]
    expired_count: int


class WarningLifecycleRepository(Protocol):
    """Repository contract для жизненного цикла warn."""

    async def get_by_id(self, warning_id: int) -> Warning | None:
        """Возвращает warn по DB ID.

        Args:
            warning_id: DB ID warn.

        Returns:
            Warn или `None`.
        """

    async def list_active_expirable_warnings(
        self,
        *,
        clan_id: int,
        current_cwl_season_key: str,
        current_cwl_season_started_at: datetime,
    ) -> list[Warning]:
        """Возвращает active warn, которые должны истечь.

        Args:
            clan_id: DB ID клана, внутри которого истекают warn.
            current_cwl_season_key: Обнаруженный CWL season key.
            current_cwl_season_started_at: Время обнаружения текущего CWL season.

        Returns:
            Список active system impactful warn, которые должны истечь.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyWarningLifecycleRepository:
    """SQLAlchemy-реализация repository для жизненного цикла warn."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_id(self, warning_id: int) -> Warning | None:
        """Возвращает warn по DB ID.

        Args:
            warning_id: DB ID warn.

        Returns:
            Warn или `None`.
        """
        result = await self._session.execute(select(Warning).where(Warning.id == warning_id))
        return result.scalar_one_or_none()

    async def list_active_expirable_warnings(
        self,
        *,
        clan_id: int,
        current_cwl_season_key: str,
        current_cwl_season_started_at: datetime,
    ) -> list[Warning]:
        """Возвращает active warn, которые должны истечь.

        Args:
            clan_id: DB ID клана.
            current_cwl_season_key: Обнаруженный CWL season key.
            current_cwl_season_started_at: Время обнаружения текущего CWL season.

        Returns:
            Список active system impactful warn, которые должны истечь.
        """
        result = await self._session.execute(
            select(Warning).where(
                Warning.status == WarningStatus.ACTIVE.value,
                Warning.source == WarningSource.SYSTEM.value,
                Warning.is_impactful.is_(True),
                Warning.clan_id == clan_id,
                or_(
                    and_(
                        Warning.created_cwl_season_key.is_not(None),
                        Warning.created_cwl_season_key != current_cwl_season_key,
                    ),
                    and_(
                        Warning.created_cwl_season_key.is_(None),
                        Warning.created_at < current_cwl_season_started_at,
                    ),
                ),
            )
        )
        return list(result.scalars().all())

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class WarningLifecycleService:
    """Сервис отмены и истечения warn.

    Сервис не удаляет warn физически и не создаёт уведомления. Он меняет только
    lifecycle-поля самой warn-записи.
    """

    def __init__(self, *, repository: WarningLifecycleRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository warn-записей.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "WarningLifecycleService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyWarningLifecycleRepository(session))

    async def cancel_warning(
        self,
        *,
        warning_id: int,
        cancelled_by: TelegramUser,
        reason: str,
    ) -> WarningCancellationResult:
        """Отменяет active warn админом.

        Повторная отмена already-cancelled warn идемпотентна и не перетирает
        исходные данные отмены.

        Args:
            warning_id: DB ID warn.
            cancelled_by: TelegramUser администратора.
            reason: Причина отмены.

        Returns:
            Результат отмены warn.

        Raises:
            WarningNotFoundError: Если warn не найден.
            WarningLifecycleError: Если warn не active или входные данные невалидны.
        """
        normalized_warning_id = _validate_positive_int(warning_id, field_name="warning_id")
        normalized_reason = _normalize_required_text(reason, field_name="cancelled_reason")
        admin_id = _required_model_id(cancelled_by, model_name="TelegramUser")
        warning = await self._get_required_warning(normalized_warning_id)

        if warning.status == WarningStatus.CANCELLED.value:
            return WarningCancellationResult(warning=warning, changed=False)

        if warning.status != WarningStatus.ACTIVE.value:
            raise WarningLifecycleError("Отменять можно только active warn.")

        warning.status = WarningStatus.CANCELLED.value
        warning.cancelled_at = _utc_now()
        warning.cancelled_by_telegram_user_id = admin_id
        warning.cancelled_by = cancelled_by
        warning.cancelled_reason = normalized_reason

        await self._repository.flush()

        return WarningCancellationResult(warning=warning, changed=True)

    async def expire_warnings_by_cwl_season(
        self,
        *,
        current_cwl_season: CwlSeason,
        expired_at: datetime | None = None,
    ) -> WarningExpirationResult:
        """Переводит старые active warn в expired при новом CWL season.

        Истекают только active system impactful warn внутри того же клана.
        Warn без `created_cwl_season_key` истекает только если сезон был
        обнаружен после создания warn.

        Args:
            current_cwl_season: Последний обнаруженный CWL season клана.
            expired_at: Явное время истечения для тестов.

        Returns:
            Результат истечения warn.
        """
        if current_cwl_season.clan_id <= 0:
            raise WarningLifecycleError("CwlSeason.clan_id должен быть положительным числом.")

        normalized_season_key = _normalize_required_text(
            current_cwl_season.season,
            field_name="current_cwl_season.season",
        )
        current_cwl_season_started_at = _require_aware_datetime(
            current_cwl_season.started_at,
            field_name="current_cwl_season.started_at",
        )
        warnings = await self._repository.list_active_expirable_warnings(
            clan_id=current_cwl_season.clan_id,
            current_cwl_season_key=normalized_season_key,
            current_cwl_season_started_at=current_cwl_season_started_at,
        )

        if not warnings:
            return WarningExpirationResult(expired_warnings=[], expired_count=0)

        normalized_expired_at = _require_aware_datetime(
            expired_at or _utc_now(),
            field_name="expired_at",
        )
        for warning in warnings:
            warning.status = WarningStatus.EXPIRED.value
            warning.expired_at = normalized_expired_at

        await self._repository.flush()

        return WarningExpirationResult(
            expired_warnings=warnings,
            expired_count=len(warnings),
        )

    async def _get_required_warning(self, warning_id: int) -> Warning:
        """Возвращает warn или выбрасывает service error.

        Args:
            warning_id: DB ID warn.

        Returns:
            Warn.

        Raises:
            WarningNotFoundError: Если warn не найден.
        """
        warning = await self._repository.get_by_id(warning_id)
        if warning is None:
            raise WarningNotFoundError(f"Warn {warning_id} не найден.")

        return warning


def _validate_positive_int(value: int, *, field_name: str) -> int:
    """Проверяет положительный integer.

    Args:
        value: Проверяемое значение.
        field_name: Имя поля для текста ошибки.

    Returns:
        Проверенное значение.

    Raises:
        WarningLifecycleError: Если значение некорректное.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise WarningLifecycleError(f"{field_name} должен быть целым числом.")

    if value <= 0:
        raise WarningLifecycleError(f"{field_name} должен быть положительным числом.")

    return value


def _normalize_required_text(value: str, *, field_name: str) -> str:
    """Нормализует обязательную строку.

    Args:
        value: Сырое значение.
        field_name: Имя поля для текста ошибки.

    Returns:
        Строка без пробелов по краям.

    Raises:
        WarningLifecycleError: Если строка пустая.
    """
    if not isinstance(value, str):
        raise WarningLifecycleError(f"{field_name} должен быть строкой.")

    normalized = value.strip()
    if not normalized:
        raise WarningLifecycleError(f"{field_name} не может быть пустым.")

    return normalized


def _require_aware_datetime(value: datetime, *, field_name: str) -> datetime:
    """Проверяет timezone-aware datetime.

    Args:
        value: Значение datetime.
        field_name: Имя поля для текста ошибки.

    Returns:
        Проверенное значение datetime.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise WarningLifecycleError(f"{field_name} должен быть timezone-aware datetime.")

    return value


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.

    Raises:
        WarningLifecycleError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise WarningLifecycleError(f"{model_name} должен быть сохранён в БД.")


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "SqlAlchemyWarningLifecycleRepository",
    "WarningCancellationResult",
    "WarningExpirationResult",
    "WarningLifecycleError",
    "WarningLifecycleRepository",
    "WarningLifecycleService",
    "WarningNotFoundError",
]
