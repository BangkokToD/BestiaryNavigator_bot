"""Сервис создания кандидатов на кик."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Clan, KickCandidate, TelegramUser, Warning
from app.domain import (
    KickCandidateReasonCode,
    KickCandidateStatus,
    WarningSource,
    WarningStatus,
    build_all_accounts_left_kick_event_key,
    build_linked_account_left_kick_event_key,
    build_two_impactful_warn_kick_event_key,
    build_unlinked_account_kick_event_key,
    normalize_player_tag,
)

_UNLINKED_ACCOUNT_MIN_AGE_DAYS = 3
_NOT_ENOUGH_IMPACTFUL_WARNINGS_REASON = "not_enough_impactful_warnings"
_UNLINKED_ACCOUNT_NOT_OLD_ENOUGH_REASON = "unlinked_account_not_old_enough"


class KickCandidateServiceError(RuntimeError):
    """Базовая ошибка сервиса кандидатов на кик."""


@dataclass(frozen=True, slots=True)
class KickCandidateCreationResult:
    """Результат создания кандидата на кик."""

    candidate: KickCandidate | None
    created: bool
    reason: str | None = None


class KickCandidateRepository(Protocol):
    """Repository contract для кандидатов на кик."""

    async def get_by_event_key(self, event_key: str) -> KickCandidate | None:
        """Возвращает кандидата по event key.

        Args:
            event_key: Идемпотентный ключ события.

        Returns:
            Кандидат или `None`.
        """

    def add(self, candidate: KickCandidate) -> None:
        """Добавляет кандидата в unit of work.

        Args:
            candidate: Новая модель кандидата.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyKickCandidateRepository:
    """SQLAlchemy-реализация repository для кандидатов на кик."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_event_key(self, event_key: str) -> KickCandidate | None:
        """Возвращает кандидата по event key.

        Args:
            event_key: Идемпотентный ключ события.

        Returns:
            Кандидат или `None`.
        """
        result = await self._session.execute(
            select(KickCandidate).where(KickCandidate.event_key == event_key)
        )
        return result.scalar_one_or_none()

    def add(self, candidate: KickCandidate) -> None:
        """Добавляет кандидата в текущую session.

        Args:
            candidate: Новая модель кандидата.
        """
        self._session.add(candidate)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class KickCandidateService:
    """Сервис создания кандидатов на кик.

    Сервис только создаёт idempotent candidates. Он не принимает решения,
    не исполняет удаление из Telegram и не отправляет уведомления.
    """

    def __init__(self, *, repository: KickCandidateRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository кандидатов.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "KickCandidateService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyKickCandidateRepository(session))

    async def create_for_two_impactful_warnings(
        self,
        *,
        telegram_user: TelegramUser,
        warnings: list[Warning],
        season_key: str,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата по двум active impactful system warn.

        Manual warn не учитываются, даже если caller ошибочно передал их в список.

        Args:
            telegram_user: TelegramUser, для которого проверяются warn.
            warnings: Warn-записи пользователя.
            season_key: Ключ сезона для идемпотентного candidate event key.

        Returns:
            Результат создания кандидата.
        """
        telegram_user_id = _required_model_id(telegram_user, model_name="TelegramUser")
        eligible_warnings = _filter_active_impactful_system_warnings(
            warnings,
            telegram_user_id=telegram_user_id,
        )

        if len(eligible_warnings) < 2:
            return KickCandidateCreationResult(
                candidate=None,
                created=False,
                reason=_NOT_ENOUGH_IMPACTFUL_WARNINGS_REASON,
            )

        event_key = build_two_impactful_warn_kick_event_key(
            telegram_user_id=telegram_user_id,
            season_key=season_key,
        )
        return await self._create_candidate(
            reason_code=KickCandidateReasonCode.TWO_IMPACTFUL_WARN,
            event_key=event_key,
            telegram_user=telegram_user,
        )

    async def create_for_unlinked_account(
        self,
        *,
        clan: Clan,
        player_tag: str,
        first_seen_at: datetime,
        observed_at: datetime | None = None,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата по непривязанному аккаунту старше 3 дней.

        Args:
            clan: Клан, где замечен непривязанный аккаунт.
            player_tag: Тег непривязанного аккаунта.
            first_seen_at: Первое появление аккаунта в API-составе.
            observed_at: Время текущей проверки. Если не передано, используется UTC now.

        Returns:
            Результат создания кандидата.
        """
        clan_id = _required_model_id(clan, model_name="Clan")
        normalized_player_tag = normalize_player_tag(player_tag)
        normalized_observed_at = observed_at or _utc_now()

        if not _is_at_least_days_old(
            first_seen_at=first_seen_at,
            observed_at=normalized_observed_at,
            days=_UNLINKED_ACCOUNT_MIN_AGE_DAYS,
        ):
            return KickCandidateCreationResult(
                candidate=None,
                created=False,
                reason=_UNLINKED_ACCOUNT_NOT_OLD_ENOUGH_REASON,
            )

        event_key = build_unlinked_account_kick_event_key(
            clan_id=clan_id,
            player_tag=normalized_player_tag,
        )
        return await self._create_candidate(
            reason_code=KickCandidateReasonCode.UNLINKED_AFTER_3_DAYS,
            event_key=event_key,
            player_tag=normalized_player_tag,
        )

    async def create_for_all_accounts_left(
        self,
        *,
        telegram_user: TelegramUser,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата по уходу всех аккаунтов TelegramUser.

        Args:
            telegram_user: TelegramUser, у которого все аккаунты покинули кланы.

        Returns:
            Результат создания кандидата.
        """
        telegram_user_id = _required_model_id(telegram_user, model_name="TelegramUser")
        event_key = build_all_accounts_left_kick_event_key(telegram_user_id=telegram_user_id)

        return await self._create_candidate(
            reason_code=KickCandidateReasonCode.ALL_ACCOUNTS_LEFT,
            event_key=event_key,
            telegram_user=telegram_user,
        )

    async def create_for_linked_account_left(
        self,
        *,
        telegram_user: TelegramUser,
        player_tag: str,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата по уходу одного привязанного аккаунта.

        Args:
            telegram_user: TelegramUser владельца аккаунта.
            player_tag: Тег ушедшего аккаунта.

        Returns:
            Результат создания кандидата.
        """
        telegram_user_id = _required_model_id(telegram_user, model_name="TelegramUser")
        normalized_player_tag = normalize_player_tag(player_tag)
        event_key = build_linked_account_left_kick_event_key(
            telegram_user_id=telegram_user_id,
            player_tag=normalized_player_tag,
        )

        return await self._create_candidate(
            reason_code=KickCandidateReasonCode.LINKED_ACCOUNT_LEFT,
            event_key=event_key,
            telegram_user=telegram_user,
            player_tag=normalized_player_tag,
        )

    async def create_manual_recommendation(
        self,
        *,
        telegram_user: TelegramUser | None = None,
        player_tag: str | None = None,
        created_by: TelegramUser | None = None,
        event_key: str | None = None,
    ) -> KickCandidateCreationResult:
        """Создаёт ручную рекомендацию на кик.

        По умолчанию manual recommendation создаётся без event key. Если caller
        явно передал event key, сервис выполняет dedup.

        Args:
            telegram_user: TelegramUser-кандидат, если известен.
            player_tag: Player tag-кандидат, если известен.
            created_by: Пользователь, создавший рекомендацию.
            event_key: Опциональный ключ дедупликации.

        Returns:
            Результат создания кандидата.

        Raises:
            KickCandidateServiceError: Если не передан ни TelegramUser, ни player tag.
        """
        if telegram_user is None and player_tag is None:
            raise KickCandidateServiceError(
                "Manual recommendation должна содержать telegram_user или player_tag."
            )

        return await self._create_candidate(
            reason_code=KickCandidateReasonCode.MANUAL_RECOMMENDATION,
            event_key=event_key,
            telegram_user=telegram_user,
            player_tag=player_tag,
            created_by=created_by,
        )

    async def _create_candidate(
        self,
        *,
        reason_code: KickCandidateReasonCode,
        event_key: str | None,
        telegram_user: TelegramUser | None = None,
        player_tag: str | None = None,
        created_by: TelegramUser | None = None,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата с общей логикой dedup.

        Args:
            reason_code: Причина кандидата.
            event_key: Event key для дедупликации.
            telegram_user: TelegramUser-кандидат.
            player_tag: Player tag-кандидат.
            created_by: Пользователь, создавший кандидата вручную или системно.

        Returns:
            Результат создания кандидата.
        """
        normalized_event_key = _normalize_optional_text(event_key)

        if normalized_event_key is not None:
            existing_candidate = await self._repository.get_by_event_key(normalized_event_key)
            if existing_candidate is not None:
                return KickCandidateCreationResult(candidate=existing_candidate, created=False)

        normalized_player_tag = normalize_player_tag(player_tag) if player_tag is not None else None
        candidate = KickCandidate(
            telegram_user_id=_optional_model_id(telegram_user, model_name="TelegramUser"),
            telegram_user=telegram_user,
            player_tag=normalized_player_tag,
            reason_code=reason_code.value,
            status=KickCandidateStatus.PENDING_ADMIN_DECISION.value,
            event_key=normalized_event_key,
            created_by=_optional_model_id(created_by, model_name="TelegramUser"),
            created_by_user=created_by,
        )

        self._repository.add(candidate)
        await self._repository.flush()

        return KickCandidateCreationResult(candidate=candidate, created=True)


def _filter_active_impactful_system_warnings(
    warnings: list[Warning],
    *,
    telegram_user_id: int,
) -> list[Warning]:
    """Фильтрует warn, которые влияют на кандидата по двум warn.

    Args:
        warnings: Warn-записи.
        telegram_user_id: DB ID TelegramUser.

    Returns:
        Active impactful system warn этого TelegramUser.
    """
    return [
        warning
        for warning in warnings
        if warning.telegram_user_id == telegram_user_id
        and warning.status == WarningStatus.ACTIVE.value
        and warning.is_impactful
        and warning.source == WarningSource.SYSTEM.value
    ]


def _is_at_least_days_old(
    *,
    first_seen_at: datetime,
    observed_at: datetime,
    days: int,
) -> bool:
    """Проверяет возраст непривязанного аккаунта.

    Args:
        first_seen_at: Первое появление аккаунта.
        observed_at: Время проверки.
        days: Минимальный возраст в днях.

    Returns:
        `True`, если аккаунт старше или равен порогу.

    Raises:
        KickCandidateServiceError: Если datetime не timezone-aware.
    """
    _validate_aware_datetime(first_seen_at, field_name="first_seen_at")
    _validate_aware_datetime(observed_at, field_name="observed_at")

    return observed_at - first_seen_at >= timedelta(days=days)


def _validate_aware_datetime(value: datetime, *, field_name: str) -> None:
    """Проверяет timezone-aware datetime.

    Args:
        value: Datetime-значение.
        field_name: Имя поля для текста ошибки.

    Raises:
        KickCandidateServiceError: Если datetime не содержит timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise KickCandidateServiceError(f"{field_name} должен быть timezone-aware datetime.")


def _normalize_optional_text(value: str | None) -> str | None:
    """Нормализует опциональную строку.

    Args:
        value: Сырое значение.

    Returns:
        Строка без пробелов по краям или `None`.
    """
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.

    Raises:
        KickCandidateServiceError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise KickCandidateServiceError(f"{model_name} должен быть сохранён в БД.")


def _optional_model_id(model: object | None, *, model_name: str) -> int | None:
    """Достаёт опциональный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model или `None`.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id или `None`.
    """
    if model is None:
        return None

    return _required_model_id(model, model_name=model_name)


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "KickCandidateCreationResult",
    "KickCandidateRepository",
    "KickCandidateService",
    "KickCandidateServiceError",
    "SqlAlchemyKickCandidateRepository",
]
