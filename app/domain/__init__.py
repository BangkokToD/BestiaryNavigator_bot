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
    require_domain_enum_value,
)
from app.domain.event_keys import (
    build_all_accounts_left_kick_event_key,
    build_cwl_warning_event_key,
    build_linked_account_left_kick_event_key,
    build_notification_event_key,
    build_raid_warning_event_key,
    build_two_impactful_warn_kick_event_key,
    build_unlinked_account_kick_event_key,
    build_war_event_key,
    build_war_warning_event_key,
)
from app.domain.exceptions import (
    DomainValidationError,
    EventKeyValidationError,
    TagValidationError,
)
from app.domain.tags import encode_tag_for_clash_url, normalize_clan_tag, normalize_player_tag
from app.domain.telegram import normalize_message_thread_id

__all__ = [
    "IMPACTFUL_WARNING_REASON_CODES",
    "MANUAL_WARNING_REASON_CODES",
    "SYSTEM_WARNING_REASON_CODES",
    "ClanType",
    "DomainStrEnum",
    "DomainValidationError",
    "EventKeyValidationError",
    "KickCandidateReasonCode",
    "KickCandidateStatus",
    "NotificationType",
    "RaidMemberStatus",
    "TagValidationError",
    "WarningImpact",
    "WarningReasonCode",
    "WarningSource",
    "WarningStatus",
    "build_all_accounts_left_kick_event_key",
    "build_cwl_warning_event_key",
    "build_linked_account_left_kick_event_key",
    "build_notification_event_key",
    "build_raid_warning_event_key",
    "build_two_impactful_warn_kick_event_key",
    "build_unlinked_account_kick_event_key",
    "build_war_event_key",
    "build_war_warning_event_key",
    "encode_tag_for_clash_url",
    "normalize_clan_tag",
    "normalize_message_thread_id",
    "normalize_player_tag",
    "require_domain_enum_value",
]
