"""Тесты доменных enum и констант."""

from pathlib import Path

import pytest

from app.domain import (
    IMPACTFUL_WARNING_REASON_CODES,
    MANUAL_WARNING_REASON_CODES,
    SYSTEM_WARNING_REASON_CODES,
    ClanType,
    DomainStrEnum,
    DomainValidationError,
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
from app.domain import enums as enums_module


def test_clan_type_values() -> None:
    """Проверяет значения типов кланов."""
    assert ClanType.values() == frozenset({"main", "academy", "freezer"})


def test_warning_source_values() -> None:
    """Проверяет значения источников warn."""
    assert WarningSource.values() == frozenset({"system", "manual"})


def test_warning_status_values() -> None:
    """Проверяет значения статусов warn."""
    assert WarningStatus.values() == frozenset({"active", "expired", "cancelled"})


def test_warning_impact_values() -> None:
    """Проверяет значения влияния warn."""
    assert WarningImpact.values() == frozenset({"impactful", "non_impactful"})


def test_kick_candidate_status_values() -> None:
    """Проверяет значения статусов кандидата на кик."""
    assert KickCandidateStatus.values() == frozenset(
        {
            "pending_admin_decision",
            "approved",
            "rejected",
            "postponed",
            "executed",
            "manual_required",
        }
    )


def test_raid_member_status_values() -> None:
    """Проверяет значения статусов участника рейда."""
    assert RaidMemberStatus.values() == frozenset(
        {
            "raid_full",
            "raid_incomplete",
            "raid_missed",
        }
    )


def test_notification_type_values() -> None:
    """Проверяет значения типов уведомлений."""
    assert NotificationType.values() == frozenset(
        {
            "war_preparation_started",
            "war_started",
            "war_6h_reminder",
            "war_12h_reminder",
            "war_3h_left",
            "war_1h_left",
            "war_ended",
            "raid_started",
            "raid_launched",
            "raid_12h_report",
            "cwl_started",
            "cwl_round_report",
            "warn_created",
            "warn_cancelled",
            "kick_candidates_evening",
            "unlinked_accounts_evening",
            "api_errors_admin",
            "daily_admin_report",
        }
    )


def test_warning_reason_code_values() -> None:
    """Проверяет значения причин warn."""
    assert WarningReasonCode.values() == frozenset(
        {
            "war_attack_missed",
            "war_bad_attack",
            "war_wrong_target",
            "war_plan_ignored",
            "cwl_attack_missed",
            "cwl_wrong_target",
            "cwl_removed",
            "raid_missed",
            "raid_incomplete",
            "raid_bad_play",
            "toxicity",
            "spam",
            "ads",
            "politics",
            "officer_info_leak",
            "sabotage",
            "other",
        }
    )


def test_kick_candidate_reason_code_values() -> None:
    """Проверяет значения причин кандидата на кик."""
    assert KickCandidateReasonCode.values() == frozenset(
        {
            "2_impactful_warn",
            "unlinked_after_3_days",
            "all_accounts_left",
            "linked_account_left",
            "manual_recommendation",
        }
    )


def test_warning_reason_code_groups() -> None:
    """Проверяет группы системных, impactful и ручных причин warn."""
    assert (
        frozenset(
            {
                WarningReasonCode.WAR_ATTACK_MISSED,
                WarningReasonCode.CWL_ATTACK_MISSED,
                WarningReasonCode.RAID_MISSED,
                WarningReasonCode.RAID_INCOMPLETE,
            }
        )
        == SYSTEM_WARNING_REASON_CODES
    )
    assert IMPACTFUL_WARNING_REASON_CODES == SYSTEM_WARNING_REASON_CODES
    assert WarningReasonCode.CWL_REMOVED in MANUAL_WARNING_REASON_CODES
    assert WarningReasonCode.OTHER in MANUAL_WARNING_REASON_CODES
    assert not SYSTEM_WARNING_REASON_CODES & MANUAL_WARNING_REASON_CODES


def test_domain_str_enum_values_returns_strings() -> None:
    """Проверяет helper возврата строковых значений enum."""

    class SmokeEnum(DomainStrEnum):
        """Тестовый enum."""

        VALUE = "value"

    assert SmokeEnum.values() == frozenset({"value"})


def test_require_domain_enum_value_returns_enum_member() -> None:
    """Проверяет централизованную валидацию строкового значения enum."""
    assert (
        require_domain_enum_value(
            ClanType,
            " main ",
            field_name="clan_type",
        )
        is ClanType.MAIN
    )


def test_require_domain_enum_value_rejects_invalid_value() -> None:
    """Проверяет ошибку для значения, которого нет в доменном enum."""
    with pytest.raises(DomainValidationError) as exc_info:
        require_domain_enum_value(ClanType, "unknown", field_name="clan_type")

    error_message = str(exc_info.value)
    assert "clan_type должен быть одним из" in error_message
    assert "main" in error_message


def test_domain_enums_do_not_import_db_layer() -> None:
    """Проверяет отсутствие зависимости доменных enum от DB-layer."""
    source = Path(enums_module.__file__).read_text(encoding="utf-8")

    assert "app.db" not in source
