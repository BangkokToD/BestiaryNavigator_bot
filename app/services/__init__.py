"""Service layer приложения."""

from app.services.account_linking import (
    AccountLinkingError,
    AccountLinkingResult,
    AccountLinkingService,
    AccountRepository,
    ClashAccountProvider,
    PlayerEventRepository,
    SqlAlchemyAccountRepository,
    SqlAlchemyPlayerEventRepository,
)
from app.services.account_unlinking import (
    AccountUnlinkingError,
    AccountUnlinkingRepository,
    AccountUnlinkingResult,
    AccountUnlinkingService,
    SqlAlchemyAccountUnlinkingRepository,
)
from app.services.clans import (
    ClanManagementError,
    ClanManagementService,
    ClanNotFoundError,
    ClanRepository,
    ClashClanProvider,
    SqlAlchemyClanRepository,
)
from app.services.telegram_users import (
    SqlAlchemyTelegramUserRepository,
    TelegramUserRepository,
    TelegramUserService,
)

__all__ = [
    "AccountLinkingError",
    "AccountLinkingResult",
    "AccountLinkingService",
    "AccountRepository",
    "AccountUnlinkingError",
    "AccountUnlinkingRepository",
    "AccountUnlinkingResult",
    "AccountUnlinkingService",
    "ClanManagementError",
    "ClanManagementService",
    "ClanNotFoundError",
    "ClanRepository",
    "ClashAccountProvider",
    "ClashClanProvider",
    "PlayerEventRepository",
    "SqlAlchemyAccountRepository",
    "SqlAlchemyAccountUnlinkingRepository",
    "SqlAlchemyClanRepository",
    "SqlAlchemyPlayerEventRepository",
    "SqlAlchemyTelegramUserRepository",
    "TelegramUserRepository",
    "TelegramUserService",
]
