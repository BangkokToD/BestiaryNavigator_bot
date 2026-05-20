"""Сервис создания warn-записей."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Clan, TelegramUser, Warning
from app.domain import (
    IMPACTFUL_WARNING_REASON_CODES,
    MANUAL_WARNING_REASON_CODES,
    SYSTEM_WARNING_REASON_CODES,
    WarningReasonCode,
    WarningSource,
    WarningStatus,
    normalize_player_tag,
    require_domain_enum_value,
)

_CATEGORY_CWL = "cwl"
_CATEGORY_DISCIPLINE = "discipline"
_CATEGORY_RAID = "raid"
_CATEGORY_WAR = "war"


class WarningCreationError(RuntimeError):
    """Базовая ошибка сервиса создания warn."""


@dataclass(frozen=True, slots=True)
class WarningAffectedAccount:
    """Затронутый warn игровой аккаунт."""

    player_tag: str
    player_name: str


@dataclass(frozen=True, slots=True)
class WarningCreationResult:
    """Результат создания warn."""

    warning: Warning
    created: bool


class WarningCreationRepository(Protocol):
    """Repository contract для создания warn."""

    async def get_by_event_key(self, event_key: str) -> Warning | None:
        """Возвращает warn по event key.

        Args:
            event_key: Идемпотентный ключ события.

        Returns:
            Warn или `None`.
        """

    def add(self, warning: Warning) -> None:
        """Добавляет warn в unit of work.

        Args:
            warning: Новая warn-запись.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyWarningRepository:
    """SQLAlchemy-реализация repository для warn."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_event_key(self, event_key: str) -> Warning | None:
        """Возвращает warn по event key.

        Args:
            event_key: Идемпотентный ключ события.

        Returns:
            Warn или `None`.
        """
        result = await self._session.execute(select(Warning).where(Warning.event_key == event_key))
        return result.scalar_one_or_none()

    def add(self, warning: Warning) -> None:
        """Добавляет warn в текущую session.

        Args:
            warning: Новая warn-запись.
        """
        self._session.add(warning)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class WarningCreationService:
    """Сервис создания manual/system warn.

    Сервис не создаёт kick candidates и не отправляет уведомления. Эти сценарии
    закрываются отдельными сервисами следующих коммитов.
    """

    def __init__(self, *, repository: WarningCreationRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository warn-записей.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "WarningCreationService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyWarningRepository(session))

    async def create_manual_warning(
        self,
        *,
        telegram_user: TelegramUser,
        reason_code: WarningReasonCode | str,
        author_telegram_user: TelegramUser | None = None,
        clan: Clan | None = None,
        affected_accounts: list[WarningAffectedAccount] | None = None,
        comment: str | None = None,
        event_key: str | None = None,
    ) -> WarningCreationResult:
        """Создаёт manual warn.

        Manual warn всегда non-impactful. По умолчанию manual warn создаётся без
        `event_key`, чтобы не блокировать повторное ручное наказание. Если
        `event_key` явно передан, сервис выполняет dedup.

        Args:
            telegram_user: Пользователь, которому создаётся warn.
            reason_code: Причина manual warn.
            author_telegram_user: Автор warn, если известен.
            clan: Клан контекста warn, если известен.
            affected_accounts: Затронутые игровые аккаунты.
            comment: Комментарий к warn.
            event_key: Опциональный ключ дедупликации.

        Returns:
            Результат создания warn.
        """
        normalized_reason = _normalize_reason_code(reason_code)
        if normalized_reason not in MANUAL_WARNING_REASON_CODES:
            raise WarningCreationError("Manual warn не может использовать system reason_code.")

        return await self._create_warning(
            telegram_user=telegram_user,
            source=WarningSource.MANUAL,
            reason_code=normalized_reason,
            is_impactful=False,
            author_telegram_user=author_telegram_user,
            clan=clan,
            affected_accounts=affected_accounts or [],
            affected_accounts_required=False,
            comment=comment,
            event_key=event_key,
            created_cwl_season_key=None,
        )

    async def create_system_warning(
        self,
        *,
        telegram_user: TelegramUser,
        reason_code: WarningReasonCode | str,
        event_key: str,
        affected_accounts: list[WarningAffectedAccount],
        clan: Clan | None = None,
        comment: str | None = None,
        created_cwl_season_key: str | None = None,
    ) -> WarningCreationResult:
        """Создаёт system warn.

        System warn требует `event_key` и affected accounts, потому что worker
        jobs должны быть идемпотентными и сохранять затронутые аккаунты.

        Args:
            telegram_user: Пользователь, которому создаётся warn.
            reason_code: Причина system warn.
            event_key: Идемпотентный ключ события.
            affected_accounts: Затронутые игровые аккаунты.
            clan: Клан контекста warn, если известен.
            comment: Комментарий к warn.
            created_cwl_season_key: CWL season key, если warn связан с ЛВК.

        Returns:
            Результат создания warn.
        """
        normalized_reason = _normalize_reason_code(reason_code)
        if normalized_reason not in SYSTEM_WARNING_REASON_CODES:
            raise WarningCreationError("System warn может использовать только system reason_code.")

        return await self._create_warning(
            telegram_user=telegram_user,
            source=WarningSource.SYSTEM,
            reason_code=normalized_reason,
            is_impactful=normalized_reason in IMPACTFUL_WARNING_REASON_CODES,
            author_telegram_user=None,
            clan=clan,
            affected_accounts=affected_accounts,
            affected_accounts_required=True,
            comment=comment,
            event_key=event_key,
            created_cwl_season_key=created_cwl_season_key,
        )

    async def _create_warning(
        self,
        *,
        telegram_user: TelegramUser,
        source: WarningSource,
        reason_code: WarningReasonCode,
        is_impactful: bool,
        author_telegram_user: TelegramUser | None,
        clan: Clan | None,
        affected_accounts: list[WarningAffectedAccount],
        affected_accounts_required: bool,
        comment: str | None,
        event_key: str | None,
        created_cwl_season_key: str | None,
    ) -> WarningCreationResult:
        """Создаёт warn с общей логикой dedup и нормализации.

        Args:
            telegram_user: Пользователь, которому создаётся warn.
            source: Источник warn.
            reason_code: Причина warn.
            is_impactful: Влияет ли warn на автоматические решения.
            author_telegram_user: Автор manual warn.
            clan: Клан контекста.
            affected_accounts: Затронутые аккаунты.
            affected_accounts_required: Требовать ли хотя бы один аккаунт.
            comment: Комментарий.
            event_key: Опциональный event key.
            created_cwl_season_key: CWL season key.

        Returns:
            Результат создания warn.
        """
        normalized_event_key = _normalize_optional_text(event_key)
        if normalized_event_key is not None:
            existing_warning = await self._repository.get_by_event_key(normalized_event_key)
            if existing_warning is not None:
                return WarningCreationResult(warning=existing_warning, created=False)

        affected_player_tags, affected_player_names = _normalize_affected_accounts(
            affected_accounts,
            required=affected_accounts_required,
        )

        warning = Warning(
            telegram_user_id=_required_model_id(telegram_user, model_name="TelegramUser"),
            telegram_user=telegram_user,
            source=source.value,
            status=WarningStatus.ACTIVE.value,
            reason_code=reason_code.value,
            category=_category_for_reason(reason_code),
            is_impactful=is_impactful,
            comment=_normalize_optional_text(comment),
            author_telegram_user_id=_optional_model_id(
                author_telegram_user,
                model_name="TelegramUser",
            ),
            author=author_telegram_user,
            clan_id=_optional_model_id(clan, model_name="Clan"),
            clan=clan,
            event_key=normalized_event_key,
            affected_player_tags_json=affected_player_tags,
            affected_player_names_json=affected_player_names,
            created_cwl_season_key=_normalize_optional_text(created_cwl_season_key),
        )
        self._repository.add(warning)
        await self._repository.flush()

        return WarningCreationResult(warning=warning, created=True)


def _normalize_reason_code(value: WarningReasonCode | str) -> WarningReasonCode:
    """Валидирует reason code через доменный enum.

    Args:
        value: Reason code.

    Returns:
        Enum member `WarningReasonCode`.
    """
    return require_domain_enum_value(WarningReasonCode, value, field_name="reason_code")


def _category_for_reason(reason_code: WarningReasonCode) -> str:
    """Определяет категорию warn по reason code.

    Args:
        reason_code: Причина warn.

    Returns:
        Строковая категория warn.
    """
    if reason_code in {
        WarningReasonCode.WAR_ATTACK_MISSED,
        WarningReasonCode.WAR_BAD_ATTACK,
        WarningReasonCode.WAR_WRONG_TARGET,
        WarningReasonCode.WAR_PLAN_IGNORED,
    }:
        return _CATEGORY_WAR

    if reason_code in {
        WarningReasonCode.CWL_ATTACK_MISSED,
        WarningReasonCode.CWL_WRONG_TARGET,
        WarningReasonCode.CWL_REMOVED,
    }:
        return _CATEGORY_CWL

    if reason_code in {
        WarningReasonCode.RAID_MISSED,
        WarningReasonCode.RAID_INCOMPLETE,
        WarningReasonCode.RAID_BAD_PLAY,
    }:
        return _CATEGORY_RAID

    return _CATEGORY_DISCIPLINE


def _normalize_affected_accounts(
    accounts: list[WarningAffectedAccount],
    *,
    required: bool,
) -> tuple[list[str], list[str]]:
    """Нормализует списки затронутых игровых аккаунтов.

    Args:
        accounts: Затронутые игровые аккаунты.
        required: Требовать ли непустой список.

    Returns:
        Пара списков: player tags и player names.

    Raises:
        WarningCreationError: Если список обязателен, пустой или содержит дубли.
    """
    if required and not accounts:
        raise WarningCreationError("System warn должен содержать affected accounts.")

    player_tags: list[str] = []
    player_names: list[str] = []
    seen_tags: set[str] = set()

    for account in accounts:
        player_tag = normalize_player_tag(account.player_tag)
        if player_tag in seen_tags:
            raise WarningCreationError(f"Дубликат affected player_tag: {player_tag}.")
        seen_tags.add(player_tag)

        player_name = account.player_name.strip()
        if not player_name:
            raise WarningCreationError("Affected player name не может быть пустым.")

        player_tags.append(player_tag)
        player_names.append(player_name)

    return player_tags, player_names


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
        WarningCreationError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise WarningCreationError(f"{model_name} должен быть сохранён в БД.")


def _optional_model_id(model: object | None, *, model_name: str) -> int | None:
    """Достаёт опциональный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model или `None`.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id или `None`.

    Raises:
        WarningCreationError: Если модель передана, но id отсутствует.
    """
    if model is None:
        return None

    return _required_model_id(model, model_name=model_name)


__all__ = [
    "SqlAlchemyWarningRepository",
    "WarningAffectedAccount",
    "WarningCreationError",
    "WarningCreationRepository",
    "WarningCreationResult",
    "WarningCreationService",
]
