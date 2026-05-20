"""Тесты сервиса отвязки игровых аккаунтов."""

from datetime import UTC, datetime

import pytest

from app.db.models import PlayerAccount, PlayerEvent, TelegramUser
from app.services import AccountUnlinkingError, AccountUnlinkingResult, AccountUnlinkingService


class InMemoryAccountUnlinkingRepository:
    """In-memory repository для unit-тестов AccountUnlinkingService."""

    def __init__(
        self,
        *,
        accounts: list[PlayerAccount] | None = None,
        current_member_tags: set[str] | None = None,
    ) -> None:
        """Инициализирует repository.

        Args:
            accounts: Начальный набор игровых аккаунтов.
            current_member_tags: Теги аккаунтов, которые есть в текущем составе клана.
        """
        self.accounts = {account.player_tag: account for account in accounts or []}
        self.current_member_tags = current_member_tags or set()
        self.flush_count = 0

    async def get_by_player_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает аккаунт по нормализованному тегу.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            Модель аккаунта или `None`.
        """
        return self.accounts.get(player_tag)

    async def has_current_clan_member_snapshot(self, player_tag: str) -> bool:
        """Проверяет наличие аккаунта в текущем составе клана.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            `True`, если аккаунт есть в текущем составе.
        """
        return player_tag in self.current_member_tags

    async def has_other_active_accounts(
        self,
        *,
        telegram_user_id: int,
        excluding_player_tag: str,
    ) -> bool:
        """Проверяет наличие других активных аккаунтов у TelegramUser."""
        return any(
            account.telegram_user_id == telegram_user_id
            and account.player_tag != excluding_player_tag
            and account.is_active
            for account in self.accounts.values()
        )

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


class InMemoryPlayerEventRepository:
    """In-memory repository для событий игрока."""

    def __init__(self) -> None:
        """Инициализирует repository."""
        self.events: list[PlayerEvent] = []

    def add(self, player_event: PlayerEvent) -> None:
        """Добавляет событие в in-memory storage.

        Args:
            player_event: Модель события игрока.
        """
        self.events.append(player_event)


def make_telegram_user(
    *,
    user_id: int = 101,
    telegram_id: int = 42,
) -> TelegramUser:
    """Создаёт TelegramUser для unit-тестов.

    Args:
        user_id: DB ID пользователя.
        telegram_id: Внешний Telegram ID.

    Returns:
        Модель TelegramUser.
    """
    return TelegramUser(
        id=user_id,
        telegram_id=telegram_id,
        username="bangkok",
        display_name="Bangkok",
    )


def make_account(
    *,
    telegram_user_id: int | None = 101,
    player_tag: str = "#2ABC",
    is_active: bool = True,
    unlinked_at: datetime | None = None,
) -> PlayerAccount:
    """Создаёт PlayerAccount для unit-тестов.

    Args:
        telegram_user_id: DB ID владельца аккаунта.
        player_tag: Нормализованный player tag.
        is_active: Активна ли связь аккаунта.
        unlinked_at: Время отвязки.

    Returns:
        Модель PlayerAccount.
    """
    return PlayerAccount(
        telegram_user_id=telegram_user_id,
        player_tag=player_tag,
        name="Bangkok",
        is_active=is_active,
        linked_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        unlinked_at=unlinked_at,
    )


@pytest.mark.asyncio
async def test_account_unlinking_service_unlinks_last_account_and_recommends_removal() -> None:
    """Проверяет отвязку последнего аккаунта и result-флаг рекомендации."""
    telegram_user = make_telegram_user()
    account = make_account()
    account.telegram_user = telegram_user
    account_repository = InMemoryAccountUnlinkingRepository(
        accounts=[account],
        current_member_tags={"#2ABC"},
    )
    event_repository = InMemoryPlayerEventRepository()
    service = AccountUnlinkingService(
        account_repository=account_repository,
        event_repository=event_repository,
    )

    result = await service.unlink_account(telegram_user=telegram_user, player_tag="2abc")

    assert isinstance(result, AccountUnlinkingResult)
    assert result.success is True
    assert result.reason is None
    assert result.already_unlinked is False
    assert result.player_account is account
    assert result.event is not None
    assert result.was_in_current_clan is True
    assert result.should_recommend_telegram_removal is True

    assert account.telegram_user_id is None
    assert account.telegram_user is None
    assert account.is_active is False
    assert account.unlinked_at is not None
    assert account_repository.accounts == {"#2ABC": account}
    assert account_repository.flush_count == 1

    assert event_repository.events == [result.event]
    assert result.event.telegram_user_id == 101
    assert result.event.player_tag == "#2ABC"
    assert result.event.event_type == "account_unlinked"
    assert result.event.metadata_json["telegram_id"] == 42
    assert result.event.metadata_json["was_in_current_clan"] is True
    assert result.event.metadata_json["should_recommend_telegram_removal"] is True


@pytest.mark.asyncio
async def test_account_unlinking_service_unlinks_non_last_account_without_recommendation() -> None:
    """Проверяет отвязку не последнего аккаунта пользователя."""
    telegram_user = make_telegram_user()
    account = make_account(player_tag="#2ABC")
    other_account = make_account(player_tag="#9XYZ")
    account.telegram_user = telegram_user
    other_account.telegram_user = telegram_user
    account_repository = InMemoryAccountUnlinkingRepository(accounts=[account, other_account])
    event_repository = InMemoryPlayerEventRepository()
    service = AccountUnlinkingService(
        account_repository=account_repository,
        event_repository=event_repository,
    )

    result = await service.unlink_account(telegram_user=telegram_user, player_tag="2abc")

    assert result.success is True
    assert result.should_recommend_telegram_removal is False
    assert result.was_in_current_clan is False
    assert account.telegram_user_id is None
    assert account.is_active is False
    assert other_account.telegram_user_id == 101
    assert other_account.is_active is True
    assert account_repository.flush_count == 1
    assert len(event_repository.events) == 1


@pytest.mark.asyncio
async def test_account_unlinking_service_repeated_unlink_is_idempotent() -> None:
    """Проверяет повторную отвязку без нового события и flush."""
    telegram_user = make_telegram_user()
    original_unlinked_at = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    account = make_account(
        telegram_user_id=None,
        is_active=False,
        unlinked_at=original_unlinked_at,
    )
    account_repository = InMemoryAccountUnlinkingRepository(
        accounts=[account],
        current_member_tags={"#2ABC"},
    )
    event_repository = InMemoryPlayerEventRepository()
    service = AccountUnlinkingService(
        account_repository=account_repository,
        event_repository=event_repository,
    )

    result = await service.unlink_account(telegram_user=telegram_user, player_tag="#2abc")

    assert result.success is True
    assert result.already_unlinked is True
    assert result.player_account is account
    assert result.event is None
    assert result.was_in_current_clan is True
    assert result.should_recommend_telegram_removal is False
    assert account.unlinked_at == original_unlinked_at
    assert account_repository.flush_count == 0
    assert event_repository.events == []


@pytest.mark.asyncio
async def test_account_unlinking_service_rejects_account_of_another_telegram_user() -> None:
    """Проверяет запрет отвязки чужого аккаунта."""
    telegram_user = make_telegram_user(user_id=101, telegram_id=42)
    account = make_account(telegram_user_id=202)
    account_repository = InMemoryAccountUnlinkingRepository(accounts=[account])
    event_repository = InMemoryPlayerEventRepository()
    service = AccountUnlinkingService(
        account_repository=account_repository,
        event_repository=event_repository,
    )

    with pytest.raises(AccountUnlinkingError):
        await service.unlink_account(telegram_user=telegram_user, player_tag="2abc")

    assert account.telegram_user_id == 202
    assert account.is_active is True
    assert account_repository.flush_count == 0
    assert event_repository.events == []


@pytest.mark.asyncio
async def test_account_unlinking_service_returns_failure_for_missing_account() -> None:
    """Проверяет controlled failure для отсутствующего аккаунта."""
    telegram_user = make_telegram_user()
    account_repository = InMemoryAccountUnlinkingRepository()
    event_repository = InMemoryPlayerEventRepository()
    service = AccountUnlinkingService(
        account_repository=account_repository,
        event_repository=event_repository,
    )

    result = await service.unlink_account(telegram_user=telegram_user, player_tag="2abc")

    assert result == AccountUnlinkingResult(
        success=False,
        reason="account_not_found",
        already_unlinked=False,
        player_account=None,
        event=None,
        was_in_current_clan=False,
        should_recommend_telegram_removal=False,
    )
    assert account_repository.flush_count == 0
    assert event_repository.events == []


@pytest.mark.asyncio
async def test_account_unlinking_service_requires_persisted_telegram_user() -> None:
    """Проверяет запрет отвязки от несохранённого TelegramUser."""
    telegram_user = TelegramUser(
        telegram_id=42,
        username="bangkok",
        display_name="Bangkok",
    )
    account = make_account()
    account_repository = InMemoryAccountUnlinkingRepository(accounts=[account])
    event_repository = InMemoryPlayerEventRepository()
    service = AccountUnlinkingService(
        account_repository=account_repository,
        event_repository=event_repository,
    )

    with pytest.raises(AccountUnlinkingError):
        await service.unlink_account(telegram_user=telegram_user, player_tag="2abc")

    assert account_repository.flush_count == 0
    assert event_repository.events == []
