"""Сервис отвязки игровых аккаунтов от Telegram-пользователей."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClanMemberSnapshot, PlayerAccount, PlayerEvent, TelegramUser
from app.domain import normalize_player_tag
from app.services.account_linking import PlayerEventRepository, SqlAlchemyPlayerEventRepository

_ACCOUNT_NOT_FOUND_REASON = "account_not_found"
_ACCOUNT_UNLINKED_EVENT_TYPE = "account_unlinked"


class AccountUnlinkingError(RuntimeError):
    """Базовая ошибка сервиса отвязки игровых аккаунтов."""


@dataclass(frozen=True, slots=True)
class AccountUnlinkingResult:
    """Результат сценария отвязки игрового аккаунта."""

    success: bool
    reason: str | None
    already_unlinked: bool
    player_account: PlayerAccount | None
    event: PlayerEvent | None
    was_in_current_clan: bool
    should_recommend_telegram_removal: bool


class AccountUnlinkingRepository(Protocol):
    """Repository contract для отвязки `PlayerAccount`."""

    async def get_by_player_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает аккаунт по нормализованному тегу.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            Модель аккаунта или `None`.
        """

    async def has_current_clan_member_snapshot(self, player_tag: str) -> bool:
        """Проверяет, есть ли аккаунт в текущем API-составе клана.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            `True`, если существует текущий `ClanMemberSnapshot`.
        """

    async def has_other_active_accounts(
        self,
        *,
        telegram_user_id: int,
        excluding_player_tag: str,
    ) -> bool:
        """Проверяет, есть ли у TelegramUser другие активные аккаунты.

        Args:
            telegram_user_id: DB ID TelegramUser.
            excluding_player_tag: Тег аккаунта, который сейчас отвязывается.

        Returns:
            `True`, если есть другой активный аккаунт.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyAccountUnlinkingRepository:
    """SQLAlchemy-реализация repository для отвязки аккаунтов."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_player_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает аккаунт по нормализованному тегу.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            Модель аккаунта или `None`.
        """
        result = await self._session.execute(
            select(PlayerAccount).where(PlayerAccount.player_tag == player_tag)
        )
        return result.scalar_one_or_none()

    async def has_current_clan_member_snapshot(self, player_tag: str) -> bool:
        """Проверяет, есть ли аккаунт в текущем составе клана.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            `True`, если аккаунт найден в текущих snapshot-данных состава.
        """
        result = await self._session.execute(
            select(ClanMemberSnapshot.id)
            .where(
                ClanMemberSnapshot.player_tag == player_tag,
                ClanMemberSnapshot.is_current.is_(True),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def has_other_active_accounts(
        self,
        *,
        telegram_user_id: int,
        excluding_player_tag: str,
    ) -> bool:
        """Проверяет наличие других активных аккаунтов TelegramUser.

        Args:
            telegram_user_id: DB ID TelegramUser.
            excluding_player_tag: Тег аккаунта, который сейчас отвязывается.

        Returns:
            `True`, если есть другой активный аккаунт.
        """
        result = await self._session.execute(
            select(PlayerAccount.id)
            .where(
                PlayerAccount.telegram_user_id == telegram_user_id,
                PlayerAccount.player_tag != excluding_player_tag,
                PlayerAccount.is_active.is_(True),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class AccountUnlinkingService:
    """Сервис отвязки игрового аккаунта от Telegram-пользователя.

    Сервис не удаляет `PlayerAccount` физически. Аккаунт переводится в
    неактивное состояние без Telegram-связи, а история сохраняется через
    `PlayerEvent`.
    """

    def __init__(
        self,
        *,
        account_repository: AccountUnlinkingRepository,
        event_repository: PlayerEventRepository,
    ) -> None:
        """Инициализирует service.

        Args:
            account_repository: Repository игровых аккаунтов.
            event_repository: Repository событий игрока.
        """
        self._account_repository = account_repository
        self._event_repository = event_repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "AccountUnlinkingService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(
            account_repository=SqlAlchemyAccountUnlinkingRepository(session),
            event_repository=SqlAlchemyPlayerEventRepository(session),
        )

    async def unlink_account(
        self,
        *,
        telegram_user: TelegramUser,
        player_tag: str,
    ) -> AccountUnlinkingResult:
        """Отвязывает игровой аккаунт от Telegram-пользователя.

        Args:
            telegram_user: Telegram-пользователь, который отвязывает свой аккаунт.
            player_tag: Тег игрового аккаунта.

        Returns:
            Результат отвязки.

        Raises:
            AccountUnlinkingError: Если аккаунт принадлежит другому TelegramUser
                или TelegramUser не имеет DB ID.
        """
        telegram_user_id = _required_model_id(telegram_user)
        normalized_player_tag = normalize_player_tag(player_tag)
        account = await self._account_repository.get_by_player_tag(normalized_player_tag)

        if account is None:
            return AccountUnlinkingResult(
                success=False,
                reason=_ACCOUNT_NOT_FOUND_REASON,
                already_unlinked=False,
                player_account=None,
                event=None,
                was_in_current_clan=False,
                should_recommend_telegram_removal=False,
            )

        if account.telegram_user_id is not None and account.telegram_user_id != telegram_user_id:
            raise AccountUnlinkingError("Нельзя отвязать аккаунт другого Telegram-пользователя.")

        was_in_current_clan = await self._account_repository.has_current_clan_member_snapshot(
            normalized_player_tag
        )

        if account.telegram_user_id is None and account.is_active is False:
            return AccountUnlinkingResult(
                success=True,
                reason=None,
                already_unlinked=True,
                player_account=account,
                event=None,
                was_in_current_clan=was_in_current_clan,
                should_recommend_telegram_removal=False,
            )

        has_other_active_accounts = await self._account_repository.has_other_active_accounts(
            telegram_user_id=telegram_user_id,
            excluding_player_tag=normalized_player_tag,
        )
        should_recommend_telegram_removal = not has_other_active_accounts
        unlinked_at = _utc_now()

        account.telegram_user_id = None
        account.telegram_user = None
        account.is_active = False
        account.unlinked_at = unlinked_at

        event = _build_account_unlinked_event(
            telegram_user=telegram_user,
            player_account=account,
            was_in_current_clan=was_in_current_clan,
            should_recommend_telegram_removal=should_recommend_telegram_removal,
            created_at=unlinked_at,
        )
        self._event_repository.add(event)

        await self._account_repository.flush()

        return AccountUnlinkingResult(
            success=True,
            reason=None,
            already_unlinked=False,
            player_account=account,
            event=event,
            was_in_current_clan=was_in_current_clan,
            should_recommend_telegram_removal=should_recommend_telegram_removal,
        )


def _build_account_unlinked_event(
    *,
    telegram_user: TelegramUser,
    player_account: PlayerAccount,
    was_in_current_clan: bool,
    should_recommend_telegram_removal: bool,
    created_at: datetime,
) -> PlayerEvent:
    """Создаёт событие истории об отвязке аккаунта.

    Args:
        telegram_user: Telegram-пользователь.
        player_account: Отвязанный игровой аккаунт.
        was_in_current_clan: Был ли аккаунт в текущем составе клана.
        should_recommend_telegram_removal: Нужно ли рекомендовать удаление из Telegram.
        created_at: Время события.

    Returns:
        Модель события игрока.
    """
    return PlayerEvent(
        telegram_user_id=_required_model_id(telegram_user),
        player_tag=player_account.player_tag,
        event_type=_ACCOUNT_UNLINKED_EVENT_TYPE,
        title="Игровой аккаунт отвязан",
        description=f"Аккаунт {player_account.name} отвязан от Telegram-пользователя.",
        metadata_json={
            "player_tag": player_account.player_tag,
            "player_name": player_account.name,
            "telegram_id": telegram_user.telegram_id,
            "was_in_current_clan": was_in_current_clan,
            "should_recommend_telegram_removal": should_recommend_telegram_removal,
        },
        created_at=created_at,
    )


def _required_model_id(model: object) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.

    Returns:
        Положительный DB id.

    Raises:
        AccountUnlinkingError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise AccountUnlinkingError("TelegramUser должен быть сохранён в БД перед отвязкой аккаунта.")


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "AccountUnlinkingError",
    "AccountUnlinkingRepository",
    "AccountUnlinkingResult",
    "AccountUnlinkingService",
    "SqlAlchemyAccountUnlinkingRepository",
]
