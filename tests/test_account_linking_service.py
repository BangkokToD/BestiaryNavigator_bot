"""Тесты сервиса привязки игровых аккаунтов."""

from datetime import UTC, datetime

import pytest

from app.db.models import PlayerAccount, PlayerEvent, TelegramUser
from app.integrations.clash import VerifyPlayerTokenResult
from app.services import (
    AccountLinkingError,
    AccountLinkingResult,
    AccountLinkingService,
)


class InMemoryAccountRepository:
    """In-memory repository для unit-тестов AccountLinkingService."""

    def __init__(self, accounts: list[PlayerAccount] | None = None) -> None:
        """Инициализирует repository.

        Args:
            accounts: Начальный набор игровых аккаунтов.
        """
        self.accounts = {account.player_tag: account for account in accounts or []}
        self.added_accounts: list[PlayerAccount] = []
        self.flush_count = 0

    async def get_by_player_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает аккаунт по нормализованному тегу.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            Модель аккаунта или `None`.
        """
        return self.accounts.get(player_tag)

    def add(self, player_account: PlayerAccount) -> None:
        """Добавляет аккаунт в in-memory storage.

        Args:
            player_account: Модель игрового аккаунта.
        """
        self.added_accounts.append(player_account)
        self.accounts[player_account.player_tag] = player_account

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


class FakeClashAccountProvider:
    """Fake provider Clash API для unit-тестов привязки аккаунта."""

    def __init__(
        self,
        *,
        verify_results: dict[str, VerifyPlayerTokenResult],
        player_payloads: dict[str, dict[str, object]],
    ) -> None:
        """Инициализирует fake provider.

        Args:
            verify_results: Результаты verifytoken по нормализованному player tag.
            player_payloads: Profile payloads по нормализованному player tag.
        """
        self.verify_results = verify_results
        self.player_payloads = player_payloads
        self.verify_calls: list[tuple[str, str]] = []
        self.get_player_calls: list[str] = []

    async def verify_player_token(
        self,
        player_tag: str,
        token: str,
    ) -> VerifyPlayerTokenResult:
        """Возвращает fake verifytoken result.

        Args:
            player_tag: Нормализованный тег игрока.
            token: One-time API token.

        Returns:
            Typed result проверки владения аккаунтом.
        """
        self.verify_calls.append((player_tag, token))
        return self.verify_results[player_tag]

    async def get_player(self, player_tag: str) -> dict[str, object]:
        """Возвращает fake profile payload.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            JSON object профиля игрока.
        """
        self.get_player_calls.append(player_tag)
        return self.player_payloads[player_tag]


@pytest.mark.asyncio
async def test_account_linking_service_creates_account_after_successful_verifytoken() -> None:
    """Проверяет создание PlayerAccount после successful verifytoken."""
    telegram_user = TelegramUser(
        id=101,
        telegram_id=42,
        username="bangkok",
        display_name="Bangkok",
    )
    account_repository = InMemoryAccountRepository()
    event_repository = InMemoryPlayerEventRepository()
    clash_provider = FakeClashAccountProvider(
        verify_results={"#2ABC": VerifyPlayerTokenResult(player_tag="#2ABC", status="ok")},
        player_payloads={"#2ABC": {"tag": "#2ABC", "name": "Bangkok"}},
    )
    service = AccountLinkingService(
        account_repository=account_repository,
        event_repository=event_repository,
        clash_client=clash_provider,
    )

    result = await service.link_account(
        telegram_user=telegram_user,
        player_tag="2abc",
        api_token="one-time-token",
    )

    assert isinstance(result, AccountLinkingResult)
    assert result.success is True
    assert result.reason is None
    assert result.verification_status == "ok"
    assert result.player_account is not None
    assert result.event is not None

    account = result.player_account
    assert account_repository.added_accounts == [account]
    assert account_repository.flush_count == 1
    assert account.player_tag == "#2ABC"
    assert account.name == "Bangkok"
    assert account.telegram_user_id == 101
    assert account.telegram_user is telegram_user
    assert account.is_active is True
    assert account.unlinked_at is None
    assert account.linked_at is not None

    assert event_repository.events == [result.event]
    assert result.event.telegram_user_id == 101
    assert result.event.player_tag == "#2ABC"
    assert result.event.event_type == "account_linked"
    assert result.event.metadata_json["telegram_id"] == 42
    assert result.event.metadata_json["previous_telegram_user_id"] is None

    assert clash_provider.verify_calls == [("#2ABC", "one-time-token")]
    assert clash_provider.get_player_calls == ["#2ABC"]
    assert "one-time-token" not in repr(account.__dict__)
    assert "one-time-token" not in repr(result.event.metadata_json)


@pytest.mark.asyncio
async def test_account_linking_service_failed_verifytoken_does_not_create_account() -> None:
    """Проверяет, что failed verifytoken не создаёт аккаунт и событие."""
    telegram_user = TelegramUser(
        id=101,
        telegram_id=42,
        username="bangkok",
        display_name="Bangkok",
    )
    account_repository = InMemoryAccountRepository()
    event_repository = InMemoryPlayerEventRepository()
    clash_provider = FakeClashAccountProvider(
        verify_results={"#2ABC": VerifyPlayerTokenResult(player_tag="#2ABC", status="invalid")},
        player_payloads={"#2ABC": {"tag": "#2ABC", "name": "Bangkok"}},
    )
    service = AccountLinkingService(
        account_repository=account_repository,
        event_repository=event_repository,
        clash_client=clash_provider,
    )

    result = await service.link_account(
        telegram_user=telegram_user,
        player_tag="#2abc",
        api_token="wrong-token",
    )

    assert result == AccountLinkingResult(
        success=False,
        reason="verification_failed",
        verification_status="invalid",
        player_account=None,
        event=None,
    )
    assert account_repository.accounts == {}
    assert account_repository.added_accounts == []
    assert account_repository.flush_count == 0
    assert event_repository.events == []
    assert clash_provider.verify_calls == [("#2ABC", "wrong-token")]
    assert clash_provider.get_player_calls == []


@pytest.mark.asyncio
async def test_account_linking_service_updates_existing_account_and_transfers_owner() -> None:
    """Проверяет обновление существующего аккаунта и перенос к новому TelegramUser."""
    old_user = TelegramUser(
        id=100,
        telegram_id=1000,
        username="old",
        display_name="Old",
    )
    new_user = TelegramUser(
        id=101,
        telegram_id=2000,
        username="new",
        display_name="New",
    )
    existing_account = PlayerAccount(
        telegram_user_id=100,
        player_tag="#2ABC",
        name="Old name",
        is_active=False,
        linked_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        unlinked_at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
    )
    existing_account.telegram_user = old_user

    account_repository = InMemoryAccountRepository([existing_account])
    event_repository = InMemoryPlayerEventRepository()
    clash_provider = FakeClashAccountProvider(
        verify_results={"#2ABC": VerifyPlayerTokenResult(player_tag="#2ABC", status="ok")},
        player_payloads={"#2ABC": {"tag": "#2ABC", "name": "Fresh name"}},
    )
    service = AccountLinkingService(
        account_repository=account_repository,
        event_repository=event_repository,
        clash_client=clash_provider,
    )

    result = await service.link_account(
        telegram_user=new_user,
        player_tag="2abc",
        api_token="new-token",
    )

    assert result.success is True
    assert result.player_account is existing_account
    assert account_repository.added_accounts == []
    assert account_repository.flush_count == 1
    assert existing_account.telegram_user_id == 101
    assert existing_account.telegram_user is new_user
    assert existing_account.name == "Fresh name"
    assert existing_account.is_active is True
    assert existing_account.unlinked_at is None
    assert result.event is not None
    assert result.event.telegram_user_id == 101
    assert result.event.metadata_json["previous_telegram_user_id"] == 100


@pytest.mark.asyncio
async def test_account_linking_service_allows_many_accounts_for_one_telegram_user() -> None:
    """Проверяет, что один TelegramUser может иметь несколько игровых аккаунтов."""
    telegram_user = TelegramUser(
        id=101,
        telegram_id=42,
        username="bangkok",
        display_name="Bangkok",
    )
    account_repository = InMemoryAccountRepository()
    event_repository = InMemoryPlayerEventRepository()
    clash_provider = FakeClashAccountProvider(
        verify_results={
            "#2ABC": VerifyPlayerTokenResult(player_tag="#2ABC", status="ok"),
            "#9XYZ": VerifyPlayerTokenResult(player_tag="#9XYZ", status="ok"),
        },
        player_payloads={
            "#2ABC": {"tag": "#2ABC", "name": "Bangkok"},
            "#9XYZ": {"tag": "#9XYZ", "name": "Phoenix"},
        },
    )
    service = AccountLinkingService(
        account_repository=account_repository,
        event_repository=event_repository,
        clash_client=clash_provider,
    )

    first_result = await service.link_account(
        telegram_user=telegram_user,
        player_tag="2abc",
        api_token="first-token",
    )
    second_result = await service.link_account(
        telegram_user=telegram_user,
        player_tag="9xyz",
        api_token="second-token",
    )

    assert first_result.success is True
    assert second_result.success is True
    assert set(account_repository.accounts) == {"#2ABC", "#9XYZ"}
    assert len(account_repository.added_accounts) == 2
    assert account_repository.flush_count == 2
    assert account_repository.accounts["#2ABC"].telegram_user is telegram_user
    assert account_repository.accounts["#9XYZ"].telegram_user is telegram_user
    assert len(event_repository.events) == 2


@pytest.mark.asyncio
async def test_account_linking_service_rejects_verifytoken_tag_mismatch() -> None:
    """Проверяет остановку привязки, если verifytoken вернул другой tag."""
    telegram_user = TelegramUser(
        id=101,
        telegram_id=42,
        username="bangkok",
        display_name="Bangkok",
    )
    account_repository = InMemoryAccountRepository()
    event_repository = InMemoryPlayerEventRepository()
    clash_provider = FakeClashAccountProvider(
        verify_results={"#2ABC": VerifyPlayerTokenResult(player_tag="#9XYZ", status="ok")},
        player_payloads={"#9XYZ": {"tag": "#9XYZ", "name": "Phoenix"}},
    )
    service = AccountLinkingService(
        account_repository=account_repository,
        event_repository=event_repository,
        clash_client=clash_provider,
    )

    with pytest.raises(AccountLinkingError):
        await service.link_account(
            telegram_user=telegram_user,
            player_tag="2abc",
            api_token="token",
        )

    assert account_repository.accounts == {}
    assert account_repository.flush_count == 0
    assert event_repository.events == []
    assert clash_provider.get_player_calls == []
