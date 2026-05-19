"""Интеграция с Clash of Clans API."""

from app.integrations.clash.client import ClashApiClient
from app.integrations.clash.dto import (
    ClashCapitalRaidSeason,
    ClashClan,
    ClashClanMember,
    ClashCurrentWar,
    ClashCwlLeagueGroup,
    ClashCwlWar,
    ClashRaidMember,
    ClashWarLogEntry,
    ClashWarSideSummary,
    VerifyPlayerTokenResult,
)
from app.integrations.clash.error_context import (
    ClashApiErrorContext,
    map_clash_api_error_to_context,
)
from app.integrations.clash.exceptions import (
    ClashApiError,
    ClashForbiddenError,
    ClashNotFoundError,
    ClashRateLimitError,
    ClashServerError,
    ClashTimeoutError,
)

__all__ = [
    "ClashApiClient",
    "ClashApiError",
    "ClashApiErrorContext",
    "ClashCapitalRaidSeason",
    "ClashClan",
    "ClashClanMember",
    "ClashCurrentWar",
    "ClashCwlLeagueGroup",
    "ClashCwlWar",
    "ClashForbiddenError",
    "ClashNotFoundError",
    "ClashRaidMember",
    "ClashRateLimitError",
    "ClashServerError",
    "ClashTimeoutError",
    "ClashWarLogEntry",
    "ClashWarSideSummary",
    "VerifyPlayerTokenResult",
    "map_clash_api_error_to_context",
]
