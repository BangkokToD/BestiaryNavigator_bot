"""Доменные enum и константы проекта."""

from enum import StrEnum

from app.domain.exceptions import DomainValidationError


class DomainStrEnum(StrEnum):
    """Базовый строковый enum с helper для набора значений."""

    @classmethod
    def values(cls) -> frozenset[str]:
        """Возвращает набор строковых значений enum.

        Returns:
            Набор строковых значений всех enum members.
        """
        return frozenset(member.value for member in cls)


def require_domain_enum_value[DomainEnumT: DomainStrEnum](
    enum_type: type[DomainEnumT],
    value: str,
    *,
    field_name: str,
) -> DomainEnumT:
    """Проверяет строковое значение доменного enum.

    Helper нужен для service/worker/web/bot-слоёв, где значения приходят
    строками из форм, API payload или callback data. Модели БД могут хранить
    строки, но бизнес-логика должна валидировать их через доменные enum.

    Args:
        enum_type: Класс enum, наследующийся от `DomainStrEnum`.
        value: Проверяемое строковое значение.
        field_name: Имя поля для понятного текста ошибки.

    Returns:
        Enum member, соответствующий строковому значению.

    Raises:
        DomainValidationError: Если значение пустое, не строковое или не входит
            в набор допустимых enum values.
    """
    if not issubclass(enum_type, DomainStrEnum):
        raise DomainValidationError("enum_type должен быть подклассом DomainStrEnum.")

    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} должен быть строкой.")

    normalized_value = value.strip()
    if not normalized_value:
        raise DomainValidationError(f"{field_name} не может быть пустым.")

    try:
        return enum_type(normalized_value)
    except ValueError as exc:
        allowed_values = ", ".join(sorted(enum_type.values()))
        raise DomainValidationError(
            f"{field_name} должен быть одним из: {allowed_values}."
        ) from exc


class ClanType(DomainStrEnum):
    """Тип отслеживаемого клана."""

    MAIN = "main"
    ACADEMY = "academy"
    FREEZER = "freezer"


class WarningSource(DomainStrEnum):
    """Источник warn."""

    SYSTEM = "system"
    MANUAL = "manual"


class WarningStatus(DomainStrEnum):
    """Статус warn."""

    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class WarningImpact(DomainStrEnum):
    """Влияние warn на автоматические решения."""

    IMPACTFUL = "impactful"
    NON_IMPACTFUL = "non_impactful"


class KickCandidateStatus(DomainStrEnum):
    """Статус кандидата на кик."""

    PENDING_ADMIN_DECISION = "pending_admin_decision"
    APPROVED = "approved"
    REJECTED = "rejected"
    POSTPONED = "postponed"
    EXECUTED = "executed"
    MANUAL_REQUIRED = "manual_required"


class RaidMemberStatus(DomainStrEnum):
    """Статус участника рейдового сезона."""

    RAID_FULL = "raid_full"
    RAID_INCOMPLETE = "raid_incomplete"
    RAID_MISSED = "raid_missed"


class NotificationType(DomainStrEnum):
    """Тип Telegram-уведомления."""

    WAR_PREPARATION_STARTED = "war_preparation_started"
    WAR_STARTED = "war_started"
    WAR_6H_REMINDER = "war_6h_reminder"
    WAR_12H_REMINDER = "war_12h_reminder"
    WAR_3H_LEFT = "war_3h_left"
    WAR_1H_LEFT = "war_1h_left"
    WAR_ENDED = "war_ended"

    RAID_STARTED = "raid_started"
    RAID_LAUNCHED = "raid_launched"
    RAID_12H_REPORT = "raid_12h_report"

    CWL_STARTED = "cwl_started"
    CWL_ROUND_REPORT = "cwl_round_report"

    WARN_CREATED = "warn_created"
    WARN_CANCELLED = "warn_cancelled"

    KICK_CANDIDATES_EVENING = "kick_candidates_evening"
    UNLINKED_ACCOUNTS_EVENING = "unlinked_accounts_evening"
    API_ERRORS_ADMIN = "api_errors_admin"
    DAILY_ADMIN_REPORT = "daily_admin_report"


class WarningReasonCode(DomainStrEnum):
    """Код причины warn."""

    WAR_ATTACK_MISSED = "war_attack_missed"
    WAR_BAD_ATTACK = "war_bad_attack"
    WAR_WRONG_TARGET = "war_wrong_target"
    WAR_PLAN_IGNORED = "war_plan_ignored"

    CWL_ATTACK_MISSED = "cwl_attack_missed"
    CWL_WRONG_TARGET = "cwl_wrong_target"
    CWL_REMOVED = "cwl_removed"

    RAID_MISSED = "raid_missed"
    RAID_INCOMPLETE = "raid_incomplete"
    RAID_BAD_PLAY = "raid_bad_play"

    TOXICITY = "toxicity"
    SPAM = "spam"
    ADS = "ads"
    POLITICS = "politics"
    OFFICER_INFO_LEAK = "officer_info_leak"
    SABOTAGE = "sabotage"
    OTHER = "other"


class KickCandidateReasonCode(DomainStrEnum):
    """Код причины кандидата на кик."""

    TWO_IMPACTFUL_WARN = "2_impactful_warn"
    UNLINKED_AFTER_3_DAYS = "unlinked_after_3_days"
    ALL_ACCOUNTS_LEFT = "all_accounts_left"
    LINKED_ACCOUNT_LEFT = "linked_account_left"
    MANUAL_RECOMMENDATION = "manual_recommendation"


SYSTEM_WARNING_REASON_CODES = frozenset(
    {
        WarningReasonCode.WAR_ATTACK_MISSED,
        WarningReasonCode.CWL_ATTACK_MISSED,
        WarningReasonCode.RAID_MISSED,
        WarningReasonCode.RAID_INCOMPLETE,
    }
)
"""Причины warn, которые создаются системой."""

IMPACTFUL_WARNING_REASON_CODES = SYSTEM_WARNING_REASON_CODES
"""Причины warn, влияющие на автоматические решения."""

MANUAL_WARNING_REASON_CODES = frozenset(
    reason_code
    for reason_code in WarningReasonCode
    if reason_code not in SYSTEM_WARNING_REASON_CODES
)
"""Причины warn, которые могут выбираться вручную."""

__all__ = [
    "IMPACTFUL_WARNING_REASON_CODES",
    "MANUAL_WARNING_REASON_CODES",
    "SYSTEM_WARNING_REASON_CODES",
    "ClanType",
    "DomainStrEnum",
    "KickCandidateReasonCode",
    "KickCandidateStatus",
    "NotificationType",
    "RaidMemberStatus",
    "WarningImpact",
    "WarningReasonCode",
    "WarningSource",
    "WarningStatus",
    "require_domain_enum_value",
]
