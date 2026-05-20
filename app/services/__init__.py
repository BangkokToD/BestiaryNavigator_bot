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
from app.services.member_lifecycle import (
    MemberLifecycleError,
    MemberLifecycleRepository,
    MemberLifecycleResult,
    MemberLifecycleService,
    SqlAlchemyMemberLifecycleRepository,
)
from app.services.telegram_users import (
    SqlAlchemyTelegramUserRepository,
    TelegramUserRepository,
    TelegramUserService,
)
from app.services.warning_creation import (
    SqlAlchemyWarningRepository,
    WarningAffectedAccount,
    WarningCreationError,
    WarningCreationRepository,
    WarningCreationResult,
    WarningCreationService,
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
    "MemberLifecycleError",
    "MemberLifecycleRepository",
    "MemberLifecycleResult",
    "MemberLifecycleService",
    "PlayerEventRepository",
    "SqlAlchemyAccountRepository",
    "SqlAlchemyAccountUnlinkingRepository",
    "SqlAlchemyClanRepository",
    "SqlAlchemyMemberLifecycleRepository",
    "SqlAlchemyPlayerEventRepository",
    "SqlAlchemyTelegramUserRepository",
    "SqlAlchemyWarningRepository",
    "TelegramUserRepository",
    "TelegramUserService",
    "WarningAffectedAccount",
    "WarningCreationError",
    "WarningCreationRepository",
    "WarningCreationResult",
    "WarningCreationService",
]
