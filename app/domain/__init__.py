"""Доменное ядро приложения."""

from app.domain.enums import (
    IMPACTFUL_WARNING_REASON_CODES,
    MANUAL_WARNING_REASON_CODES,
    SYSTEM_WARNING_REASON_CODES,
    ClanType,
    DomainStrEnum,
    KickCandidateReasonCode,
    KickCandidateStatus,
    NotificationType,
    RaidMemberStatus,
    WarningImpact,
    WarningReasonCode,
    WarningSource,
    WarningStatus,
)
from app.domain.exceptions import DomainValidationError, TagValidationError
from app.domain.tags import encode_tag_for_clash_url, normalize_clan_tag, normalize_player_tag

__all__ = [
    "IMPACTFUL_WARNING_REASON_CODES",
    "MANUAL_WARNING_REASON_CODES",
    "SYSTEM_WARNING_REASON_CODES",
    "ClanType",
    "DomainStrEnum",
    "DomainValidationError",
    "KickCandidateReasonCode",
    "KickCandidateStatus",
    "NotificationType",
    "RaidMemberStatus",
    "TagValidationError",
    "WarningImpact",
    "WarningReasonCode",
    "WarningSource",
    "WarningStatus",
    "encode_tag_for_clash_url",
    "normalize_clan_tag",
    "normalize_player_tag",
]
