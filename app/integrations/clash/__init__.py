"""Интеграция с Clash of Clans API."""

from app.integrations.clash.client import ClashApiClient
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
    "ClashForbiddenError",
    "ClashNotFoundError",
    "ClashRateLimitError",
    "ClashServerError",
    "ClashTimeoutError",
]
