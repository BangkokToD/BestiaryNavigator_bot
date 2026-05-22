"""Сервис поиска цели для ручного `/warn`."""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import ClanMemberSnapshot, PlayerAccount, TelegramUser
from app.domain import normalize_player_tag


class WarningTargetResolutionKind(StrEnum):
    """Тип результата поиска цели warn."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNLINKED = "unlinked"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class WarningTargetAccount:
    """Игровой аккаунт найденной цели warn."""

    player_tag: str
    player_name: str


@dataclass(frozen=True, slots=True)
class WarningTargetCandidate:
    """Кандидат цели warn.

    Attributes:
        telegram_user_id: DB ID TelegramUser.
        telegram_id: Внешний Telegram ID пользователя.
        username: Telegram username.
        display_name: Отображаемое имя.
        accounts: Подтверждённые активные игровые аккаунты пользователя.
    """

    telegram_user_id: int
    telegram_id: int
    username: str | None
    display_name: str | None
    accounts: tuple[WarningTargetAccount, ...]

    @property
    def label(self) -> str:
        """Возвращает короткую подпись цели.

        Returns:
            Display name, username или Telegram ID.
        """
        if self.display_name:
            return self.display_name

        if self.username:
            return f"@{self.username}"

        return f"Telegram ID {self.telegram_id}"


@dataclass(frozen=True, slots=True)
class WarningTargetResolution:
    """Результат поиска цели warn."""

    kind: WarningTargetResolutionKind
    candidates: tuple[WarningTargetCandidate, ...] = ()
    player_tag: str | None = None

    @classmethod
    def resolved(cls, candidate: WarningTargetCandidate) -> "WarningTargetResolution":
        """Создаёт успешный результат.

        Args:
            candidate: Найденная цель.

        Returns:
            Результат с одной целью.
        """
        return cls(kind=WarningTargetResolutionKind.RESOLVED, candidates=(candidate,))

    @classmethod
    def ambiguous(
        cls,
        candidates: tuple[WarningTargetCandidate, ...],
    ) -> "WarningTargetResolution":
        """Создаёт неоднозначный результат.

        Args:
            candidates: Несколько подходящих целей.

        Returns:
            Результат, требующий уточнения.
        """
        return cls(kind=WarningTargetResolutionKind.AMBIGUOUS, candidates=candidates)

    @classmethod
    def unlinked(cls, *, player_tag: str | None = None) -> "WarningTargetResolution":
        """Создаёт результат для непривязанного аккаунта.

        Args:
            player_tag: Тег аккаунта, если он известен.

        Returns:
            Результат отказа из-за отсутствия Telegram-привязки.
        """
        return cls(kind=WarningTargetResolutionKind.UNLINKED, player_tag=player_tag)

    @classmethod
    def not_found(cls) -> "WarningTargetResolution":
        """Создаёт результат отсутствия цели.

        Returns:
            Результат `not_found`.
        """
        return cls(kind=WarningTargetResolutionKind.NOT_FOUND)


class WarningTargetResolverRepository:
    """Repository contract поиска цели warn."""

    async def get_telegram_user_by_telegram_id(self, telegram_id: int) -> TelegramUser | None:
        """Возвращает TelegramUser по внешнему Telegram ID."""
        raise NotImplementedError

    async def get_telegram_user_by_id(self, telegram_user_id: int) -> TelegramUser | None:
        """Возвращает TelegramUser по DB ID."""
        raise NotImplementedError

    async def list_telegram_users_by_username(self, username: str) -> tuple[TelegramUser, ...]:
        """Возвращает TelegramUser по username."""
        raise NotImplementedError

    async def get_player_account_by_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает подтверждённый аккаунт по player tag."""
        raise NotImplementedError

    async def list_accounts_by_telegram_user_id(
        self,
        telegram_user_id: int,
    ) -> tuple[PlayerAccount, ...]:
        """Возвращает активные аккаунты TelegramUser."""
        raise NotImplementedError

    async def get_current_snapshot_by_player_tag(
        self,
        player_tag: str,
    ) -> ClanMemberSnapshot | None:
        """Возвращает current snapshot по player tag."""
        raise NotImplementedError


class SqlAlchemyWarningTargetResolverRepository(WarningTargetResolverRepository):
    """SQLAlchemy repository поиска цели warn."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_telegram_user_by_telegram_id(self, telegram_id: int) -> TelegramUser | None:
        """Возвращает TelegramUser по внешнему Telegram ID."""
        result = await self._session.execute(
            select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def get_telegram_user_by_id(self, telegram_user_id: int) -> TelegramUser | None:
        """Возвращает TelegramUser по DB ID."""
        result = await self._session.execute(
            select(TelegramUser).where(TelegramUser.id == telegram_user_id)
        )
        return result.scalar_one_or_none()

    async def list_telegram_users_by_username(self, username: str) -> tuple[TelegramUser, ...]:
        """Возвращает TelegramUser по username без учёта регистра."""
        normalized_username = _normalize_username(username)
        result = await self._session.execute(
            select(TelegramUser)
            .where(func.lower(TelegramUser.username) == normalized_username.lower())
            .order_by(TelegramUser.id.asc())
        )
        return tuple(result.scalars().all())

    async def get_player_account_by_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает подтверждённый аккаунт по player tag."""
        result = await self._session.execute(
            select(PlayerAccount)
            .options(selectinload(PlayerAccount.telegram_user))
            .where(PlayerAccount.player_tag == player_tag)
        )
        return result.scalar_one_or_none()

    async def list_accounts_by_telegram_user_id(
        self,
        telegram_user_id: int,
    ) -> tuple[PlayerAccount, ...]:
        """Возвращает активные привязанные аккаунты TelegramUser."""
        result = await self._session.execute(
            select(PlayerAccount)
            .where(
                PlayerAccount.telegram_user_id == telegram_user_id,
                PlayerAccount.is_active.is_(True),
                PlayerAccount.unlinked_at.is_(None),
            )
            .order_by(PlayerAccount.player_tag.asc())
        )
        return tuple(result.scalars().all())

    async def get_current_snapshot_by_player_tag(
        self,
        player_tag: str,
    ) -> ClanMemberSnapshot | None:
        """Возвращает current snapshot по player tag."""
        result = await self._session.execute(
            select(ClanMemberSnapshot)
            .where(
                ClanMemberSnapshot.player_tag == player_tag,
                ClanMemberSnapshot.is_current.is_(True),
            )
            .order_by(ClanMemberSnapshot.snapshot_at.desc(), ClanMemberSnapshot.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


class WarningTargetResolverService:
    """Сервис поиска цели для ручного `/warn`.

    Сервис ничего не пишет в БД и не создаёт `Warning`. Он только определяет,
    к какому `TelegramUser` относится цель команды.
    """

    def __init__(self, *, repository: WarningTargetResolverRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository поиска цели warn.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "WarningTargetResolverService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyWarningTargetResolverRepository(session))

    async def resolve_by_reply_telegram_id(self, telegram_id: int) -> WarningTargetResolution:
        """Ищет цель warn по Telegram ID из reply.

        Args:
            telegram_id: Внешний Telegram ID автора reply-сообщения.

        Returns:
            Результат поиска цели.
        """
        telegram_user = await self._repository.get_telegram_user_by_telegram_id(
            _validate_positive_int(telegram_id, field_name="telegram_id")
        )
        if telegram_user is None:
            return WarningTargetResolution.not_found()

        return await self._resolution_from_telegram_users((telegram_user,))

    async def resolve_by_telegram_user_id(self, telegram_user_id: int) -> WarningTargetResolution:
        """Ищет цель warn по DB ID TelegramUser.

        Args:
            telegram_user_id: DB ID TelegramUser.

        Returns:
            Результат поиска цели.
        """
        telegram_user = await self._repository.get_telegram_user_by_id(
            _validate_positive_int(telegram_user_id, field_name="telegram_user_id")
        )
        if telegram_user is None:
            return WarningTargetResolution.not_found()

        return await self._resolution_from_telegram_users((telegram_user,))

    async def resolve_by_player_tag(self, player_tag: str) -> WarningTargetResolution:
        """Ищет цель warn по игровому тегу.

        Args:
            player_tag: Тег игрового аккаунта.

        Returns:
            Результат поиска цели.
        """
        normalized_player_tag = normalize_player_tag(player_tag)
        player_account = await self._repository.get_player_account_by_tag(normalized_player_tag)

        if player_account is not None:
            if player_account.telegram_user_id is None:
                return WarningTargetResolution.unlinked(player_tag=normalized_player_tag)

            telegram_user = player_account.telegram_user
            if telegram_user is None:
                return WarningTargetResolution.unlinked(player_tag=normalized_player_tag)

            return await self._resolution_from_telegram_users((telegram_user,))

        current_snapshot = await self._repository.get_current_snapshot_by_player_tag(
            normalized_player_tag
        )
        if current_snapshot is not None:
            return WarningTargetResolution.unlinked(player_tag=normalized_player_tag)

        return WarningTargetResolution.not_found()

    async def resolve_by_username(self, username: str) -> WarningTargetResolution:
        """Ищет цель warn по Telegram username.

        Args:
            username: Username с `@` или без него.

        Returns:
            Результат поиска цели.
        """
        telegram_users = await self._repository.list_telegram_users_by_username(username)
        if not telegram_users:
            return WarningTargetResolution.not_found()

        return await self._resolution_from_telegram_users(telegram_users)

    async def _resolution_from_telegram_users(
        self,
        telegram_users: tuple[TelegramUser, ...],
    ) -> WarningTargetResolution:
        """Создаёт resolution из найденных TelegramUser.

        Args:
            telegram_users: Пользователи-кандидаты.

        Returns:
            Результат поиска цели.
        """
        candidates: list[WarningTargetCandidate] = []

        for telegram_user in telegram_users:
            candidate = await self._candidate_from_telegram_user(telegram_user)
            if candidate is not None:
                candidates.append(candidate)

        if not candidates:
            return WarningTargetResolution.unlinked()

        if len(candidates) == 1:
            return WarningTargetResolution.resolved(candidates[0])

        return WarningTargetResolution.ambiguous(tuple(candidates))

    async def _candidate_from_telegram_user(
        self,
        telegram_user: TelegramUser,
    ) -> WarningTargetCandidate | None:
        """Создаёт candidate по TelegramUser, если у него есть активные аккаунты.

        Args:
            telegram_user: TelegramUser.

        Returns:
            Candidate или `None`, если аккаунтов нет.
        """
        telegram_user_id = _required_model_id(telegram_user, model_name="TelegramUser")
        accounts = await self._repository.list_accounts_by_telegram_user_id(telegram_user_id)

        if not accounts:
            return None

        return WarningTargetCandidate(
            telegram_user_id=telegram_user_id,
            telegram_id=telegram_user.telegram_id,
            username=telegram_user.username,
            display_name=telegram_user.display_name,
            accounts=tuple(
                WarningTargetAccount(
                    player_tag=account.player_tag,
                    player_name=account.name,
                )
                for account in accounts
            ),
        )


def _normalize_username(value: str) -> str:
    """Нормализует Telegram username.

    Args:
        value: Username с `@` или без него.

    Returns:
        Username без `@`.

    Raises:
        ValueError: Если username пустой.
    """
    if not isinstance(value, str):
        raise ValueError("username должен быть строкой.")

    normalized = value.strip().removeprefix("@")
    if not normalized:
        raise ValueError("username не может быть пустым.")

    return normalized


def _validate_positive_int(value: int, *, field_name: str) -> int:
    """Проверяет положительный integer.

    Args:
        value: Значение.
        field_name: Название поля.

    Returns:
        Проверенное значение.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} должен быть целым числом.")

    if value <= 0:
        raise ValueError(f"{field_name} должен быть положительным числом.")

    return value


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели.

    Returns:
        Положительный DB id.

    Raises:
        ValueError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise ValueError(f"{model_name} должен быть сохранён в БД.")


__all__ = [
    "SqlAlchemyWarningTargetResolverRepository",
    "WarningTargetAccount",
    "WarningTargetCandidate",
    "WarningTargetResolution",
    "WarningTargetResolutionKind",
    "WarningTargetResolverRepository",
    "WarningTargetResolverService",
]
