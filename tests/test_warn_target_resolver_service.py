"""Тесты сервиса поиска цели warn."""

from app.db.models import ClanMemberSnapshot, PlayerAccount, TelegramUser
from app.services import (
    WarningTargetResolutionKind,
    WarningTargetResolverService,
)


class InMemoryWarningTargetResolverRepository:
    """In-memory repository поиска цели warn."""

    def __init__(
        self,
        *,
        telegram_users: list[TelegramUser] | None = None,
        accounts: list[PlayerAccount] | None = None,
        snapshots: list[ClanMemberSnapshot] | None = None,
    ) -> None:
        """Инициализирует repository.

        Args:
            telegram_users: Telegram users.
            accounts: Player accounts.
            snapshots: Current member snapshots.
        """
        self.telegram_users = telegram_users or []
        self.accounts = accounts or []
        self.snapshots = snapshots or []

    async def get_telegram_user_by_telegram_id(self, telegram_id: int) -> TelegramUser | None:
        """Возвращает TelegramUser по Telegram ID."""
        for telegram_user in self.telegram_users:
            if telegram_user.telegram_id == telegram_id:
                return telegram_user

        return None

    async def get_telegram_user_by_id(self, telegram_user_id: int) -> TelegramUser | None:
        """Возвращает TelegramUser по DB ID."""
        for telegram_user in self.telegram_users:
            if telegram_user.id == telegram_user_id:
                return telegram_user

        return None

    async def list_telegram_users_by_username(self, username: str) -> tuple[TelegramUser, ...]:
        """Возвращает TelegramUser по username."""
        normalized = username.strip().removeprefix("@").lower()
        return tuple(
            telegram_user
            for telegram_user in self.telegram_users
            if (telegram_user.username or "").lower() == normalized
        )

    async def get_player_account_by_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает PlayerAccount по тегу."""
        for account in self.accounts:
            if account.player_tag == player_tag:
                account.telegram_user = await self.get_telegram_user_by_id(
                    account.telegram_user_id or 0
                )
                return account

        return None

    async def list_accounts_by_telegram_user_id(
        self,
        telegram_user_id: int,
    ) -> tuple[PlayerAccount, ...]:
        """Возвращает активные аккаунты TelegramUser."""
        return tuple(
            account
            for account in self.accounts
            if account.telegram_user_id == telegram_user_id
            and account.is_active
            and account.unlinked_at is None
        )

    async def get_current_snapshot_by_player_tag(
        self,
        player_tag: str,
    ) -> ClanMemberSnapshot | None:
        """Возвращает current snapshot по тегу."""
        for snapshot in self.snapshots:
            if snapshot.player_tag == player_tag and snapshot.is_current:
                return snapshot

        return None


async def test_warn_target_resolver_resolves_reply_target() -> None:
    """Проверяет поиск цели по reply Telegram ID."""
    user = _make_user()
    account = _make_account(telegram_user_id=101)
    service = WarningTargetResolverService(
        repository=InMemoryWarningTargetResolverRepository(
            telegram_users=[user],
            accounts=[account],
        )
    )

    result = await service.resolve_by_reply_telegram_id(telegram_id=42)

    assert result.kind == WarningTargetResolutionKind.RESOLVED
    assert result.candidates[0].telegram_user_id == 101
    assert result.candidates[0].accounts[0].player_tag == "#2ABC"


async def test_warn_target_resolver_resolves_player_tag() -> None:
    """Проверяет поиск цели по player tag."""
    user = _make_user()
    account = _make_account(telegram_user_id=101)
    service = WarningTargetResolverService(
        repository=InMemoryWarningTargetResolverRepository(
            telegram_users=[user],
            accounts=[account],
        )
    )

    result = await service.resolve_by_player_tag("2abc")

    assert result.kind == WarningTargetResolutionKind.RESOLVED
    assert result.candidates[0].telegram_id == 42


async def test_warn_target_resolver_resolves_username() -> None:
    """Проверяет поиск цели по username."""
    user = _make_user(username="bangkok")
    account = _make_account(telegram_user_id=101)
    service = WarningTargetResolverService(
        repository=InMemoryWarningTargetResolverRepository(
            telegram_users=[user],
            accounts=[account],
        )
    )

    result = await service.resolve_by_username("@Bangkok")

    assert result.kind == WarningTargetResolutionKind.RESOLVED
    assert result.candidates[0].username == "bangkok"


async def test_warn_target_resolver_returns_ambiguity_for_username() -> None:
    """Проверяет неоднозначный username."""
    first_user = _make_user(user_id=101, telegram_id=42, username="same")
    second_user = _make_user(user_id=202, telegram_id=777, username="same")
    service = WarningTargetResolverService(
        repository=InMemoryWarningTargetResolverRepository(
            telegram_users=[first_user, second_user],
            accounts=[
                _make_account(telegram_user_id=101, player_tag="#2ABC", name="Bangkok"),
                _make_account(telegram_user_id=202, player_tag="#9XYZ", name="Phoenix"),
            ],
        )
    )

    result = await service.resolve_by_username("@same")

    assert result.kind == WarningTargetResolutionKind.AMBIGUOUS
    assert [candidate.telegram_user_id for candidate in result.candidates] == [101, 202]


async def test_warn_target_resolver_rejects_unlinked_snapshot() -> None:
    """Проверяет отказ для непривязанного аккаунта из snapshot."""
    service = WarningTargetResolverService(
        repository=InMemoryWarningTargetResolverRepository(
            snapshots=[
                ClanMemberSnapshot(
                    player_tag="#2ABC",
                    name="Bangkok",
                    clan_id=1,
                    is_current=True,
                )
            ],
        )
    )

    result = await service.resolve_by_player_tag("#2ABC")

    assert result.kind == WarningTargetResolutionKind.UNLINKED
    assert result.player_tag == "#2ABC"


async def test_warn_target_resolver_rejects_user_without_accounts() -> None:
    """Проверяет отказ для TelegramUser без привязанных аккаунтов."""
    service = WarningTargetResolverService(
        repository=InMemoryWarningTargetResolverRepository(telegram_users=[_make_user()])
    )

    result = await service.resolve_by_reply_telegram_id(telegram_id=42)

    assert result.kind == WarningTargetResolutionKind.UNLINKED


def _make_user(
    *,
    user_id: int = 101,
    telegram_id: int = 42,
    username: str = "bangkok",
) -> TelegramUser:
    """Создаёт TelegramUser для тестов."""
    return TelegramUser(
        id=user_id,
        telegram_id=telegram_id,
        username=username,
        display_name=username.title(),
    )


def _make_account(
    *,
    telegram_user_id: int,
    player_tag: str = "#2ABC",
    name: str = "Bangkok",
) -> PlayerAccount:
    """Создаёт PlayerAccount для тестов."""
    return PlayerAccount(
        telegram_user_id=telegram_user_id,
        player_tag=player_tag,
        name=name,
        is_active=True,
    )
