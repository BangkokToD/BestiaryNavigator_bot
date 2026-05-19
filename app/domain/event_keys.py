"""Helpers для построения идемпотентных event keys."""

from datetime import UTC, date, datetime
from hashlib import sha1
from typing import Final

from app.domain.enums import KickCandidateReasonCode, NotificationType, WarningReasonCode
from app.domain.exceptions import EventKeyValidationError
from app.domain.tags import normalize_clan_tag, normalize_player_tag

_WARN_PREFIX: Final[str] = "warn"
_AUTO_WARN_SOURCE: Final[str] = "auto"
_KICK_PREFIX: Final[str] = "kick"
_NOTIFY_PREFIX: Final[str] = "notify"


def build_war_event_key(
    *,
    clan_tag: str,
    opponent_tag: str,
    preparation_start_time: datetime,
    start_time: datetime,
    end_time: datetime,
    team_size: int,
) -> str:
    """Строит стабильный hash-key обычной войны.

    Args:
        clan_tag: Тег нашего клана.
        opponent_tag: Тег клана соперника.
        preparation_start_time: Время начала подготовки.
        start_time: Время начала войны.
        end_time: Время окончания войны.
        team_size: Размер войны.

    Returns:
        SHA1 hash, построенный из нормализованных полей войны.

    Raises:
        EventKeyValidationError: Если время не timezone-aware или размер войны некорректен.
    """
    normalized_clan_tag = normalize_clan_tag(clan_tag)
    normalized_opponent_tag = normalize_clan_tag(opponent_tag)
    normalized_team_size = _validate_positive_int(team_size, field_name="team_size")

    payload = ":".join(
        (
            normalized_clan_tag,
            normalized_opponent_tag,
            _format_aware_datetime(
                preparation_start_time,
                field_name="preparation_start_time",
            ),
            _format_aware_datetime(start_time, field_name="start_time"),
            _format_aware_datetime(end_time, field_name="end_time"),
            str(normalized_team_size),
        )
    )

    return sha1(payload.encode("utf-8")).hexdigest()


def build_war_warning_event_key(
    *,
    clan_tag: str,
    war_event_key: str,
    telegram_user_id: int,
) -> str:
    """Строит event key автоматического warn за пропуск атаки КВ.

    Args:
        clan_tag: Тег клана.
        war_event_key: Стабильный key войны.
        telegram_user_id: ID TelegramUser в БД.

    Returns:
        Event key для дедупликации warn.
    """
    return _join_event_key_parts(
        _WARN_PREFIX,
        _AUTO_WARN_SOURCE,
        WarningReasonCode.WAR_ATTACK_MISSED.value,
        normalize_clan_tag(clan_tag),
        _normalize_non_empty_string(war_event_key, field_name="war_event_key"),
        _validate_positive_int(telegram_user_id, field_name="telegram_user_id"),
    )


def build_cwl_warning_event_key(
    *,
    clan_tag: str,
    cwl_war_tag: str,
    telegram_user_id: int,
) -> str:
    """Строит event key автоматического warn за пропуск атаки ЛВК.

    Args:
        clan_tag: Тег клана.
        cwl_war_tag: Реальный war tag из CWL API.
        telegram_user_id: ID TelegramUser в БД.

    Returns:
        Event key для дедупликации warn.
    """
    return _join_event_key_parts(
        _WARN_PREFIX,
        _AUTO_WARN_SOURCE,
        WarningReasonCode.CWL_ATTACK_MISSED.value,
        normalize_clan_tag(clan_tag),
        normalize_clan_tag(cwl_war_tag),
        _validate_positive_int(telegram_user_id, field_name="telegram_user_id"),
    )


def build_raid_warning_event_key(
    *,
    reason_code: WarningReasonCode,
    clan_tag: str,
    raid_start_time: datetime,
    telegram_user_id: int,
) -> str:
    """Строит event key автоматического warn за рейды.

    Args:
        reason_code: Причина `raid_missed` или `raid_incomplete`.
        clan_tag: Тег клана.
        raid_start_time: Время начала рейдового сезона.
        telegram_user_id: ID TelegramUser в БД.

    Returns:
        Event key для дедупликации warn.

    Raises:
        EventKeyValidationError: Если передана причина не из рейдовых автоматических причин.
    """
    if reason_code not in {
        WarningReasonCode.RAID_MISSED,
        WarningReasonCode.RAID_INCOMPLETE,
    }:
        raise EventKeyValidationError(
            "Для рейдового warn допустимы только raid_missed и raid_incomplete."
        )

    return _join_event_key_parts(
        _WARN_PREFIX,
        _AUTO_WARN_SOURCE,
        reason_code.value,
        normalize_clan_tag(clan_tag),
        _format_aware_datetime(raid_start_time, field_name="raid_start_time"),
        _validate_positive_int(telegram_user_id, field_name="telegram_user_id"),
    )


def build_two_impactful_warn_kick_event_key(
    *,
    telegram_user_id: int,
    season_key: str,
) -> str:
    """Строит event key кандидата по двум active impactful warn.

    Args:
        telegram_user_id: ID TelegramUser в БД.
        season_key: Ключ сезона, в рамках которого считается кандидат.

    Returns:
        Event key для дедупликации кандидата на кик.
    """
    return _join_event_key_parts(
        _KICK_PREFIX,
        KickCandidateReasonCode.TWO_IMPACTFUL_WARN.value,
        _validate_positive_int(telegram_user_id, field_name="telegram_user_id"),
        _normalize_non_empty_string(season_key, field_name="season_key"),
    )


def build_unlinked_account_kick_event_key(
    *,
    clan_id: int,
    player_tag: str,
) -> str:
    """Строит event key кандидата по непривязанному аккаунту.

    Args:
        clan_id: ID клана в БД.
        player_tag: Тег непривязанного аккаунта.

    Returns:
        Event key для дедупликации кандидата на кик.
    """
    return _join_event_key_parts(
        _KICK_PREFIX,
        KickCandidateReasonCode.UNLINKED_AFTER_3_DAYS.value,
        _validate_positive_int(clan_id, field_name="clan_id"),
        normalize_player_tag(player_tag),
    )


def build_all_accounts_left_kick_event_key(*, telegram_user_id: int) -> str:
    """Строит event key кандидата по уходу всех аккаунтов пользователя.

    Args:
        telegram_user_id: ID TelegramUser в БД.

    Returns:
        Event key для дедупликации кандидата на кик.
    """
    return _join_event_key_parts(
        _KICK_PREFIX,
        KickCandidateReasonCode.ALL_ACCOUNTS_LEFT.value,
        _validate_positive_int(telegram_user_id, field_name="telegram_user_id"),
    )


def build_linked_account_left_kick_event_key(
    *,
    telegram_user_id: int,
    player_tag: str,
) -> str:
    """Строит event key кандидата по уходу одного привязанного аккаунта.

    Args:
        telegram_user_id: ID TelegramUser в БД.
        player_tag: Тег ушедшего аккаунта.

    Returns:
        Event key для дедупликации кандидата на кик.
    """
    return _join_event_key_parts(
        _KICK_PREFIX,
        KickCandidateReasonCode.LINKED_ACCOUNT_LEFT.value,
        _validate_positive_int(telegram_user_id, field_name="telegram_user_id"),
        normalize_player_tag(player_tag),
    )


def build_notification_event_key(notification_type: NotificationType, *parts: object) -> str:
    """Строит event key уведомления.

    Args:
        notification_type: Тип уведомления.
        *parts: Стабильные части события: ID, event key, date или timezone-aware datetime.

    Returns:
        Event key для дедупликации уведомления.

    Raises:
        EventKeyValidationError: Если тип уведомления или части ключа невалидны.
    """
    if not isinstance(notification_type, NotificationType):
        raise EventKeyValidationError("notification_type должен быть NotificationType.")

    if not parts:
        raise EventKeyValidationError("Notification event key должен содержать части события.")

    return _join_event_key_parts(_NOTIFY_PREFIX, notification_type.value, *parts)


def _join_event_key_parts(*parts: object) -> str:
    """Собирает event key из нормализованных частей.

    Args:
        *parts: Части event key.

    Returns:
        Строковый event key.
    """
    return ":".join(_format_event_key_part(part) for part in parts)


def _format_event_key_part(part: object) -> str:
    """Форматирует одну часть event key.

    Args:
        part: Значение части event key.

    Returns:
        Строковое представление части event key.

    Raises:
        EventKeyValidationError: Если тип или значение части невалидны.
    """
    if isinstance(part, datetime):
        return _format_aware_datetime(part, field_name="event_key_part")

    if isinstance(part, date):
        return part.isoformat()

    if isinstance(part, bool):
        raise EventKeyValidationError("Boolean не может быть частью event key.")

    if isinstance(part, int):
        return str(_validate_positive_int(part, field_name="event_key_part"))

    if isinstance(part, str):
        return _normalize_non_empty_string(part, field_name="event_key_part")

    raise EventKeyValidationError(f"Неподдерживаемая часть event key: {type(part).__name__}.")


def _format_aware_datetime(value: datetime, *, field_name: str) -> str:
    """Форматирует timezone-aware datetime в стабильный UTC ISO-format.

    Args:
        value: Datetime-значение.
        field_name: Название поля для текста ошибки.

    Returns:
        UTC ISO-строка с точностью до секунд.

    Raises:
        EventKeyValidationError: Если datetime не содержит timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise EventKeyValidationError(f"{field_name} должен быть timezone-aware datetime.")

    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_positive_int(value: int, *, field_name: str) -> int:
    """Проверяет положительное целое число.

    Args:
        value: Проверяемое значение.
        field_name: Название поля для текста ошибки.

    Returns:
        Проверенное значение.

    Raises:
        EventKeyValidationError: Если значение не является положительным int.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise EventKeyValidationError(f"{field_name} должен быть целым числом.")

    if value <= 0:
        raise EventKeyValidationError(f"{field_name} должен быть положительным числом.")

    return value


def _normalize_non_empty_string(value: str, *, field_name: str) -> str:
    """Проверяет непустую строку.

    Args:
        value: Проверяемое значение.
        field_name: Название поля для текста ошибки.

    Returns:
        Строка без пробелов по краям.

    Raises:
        EventKeyValidationError: Если строка пустая.
    """
    normalized_value = value.strip()

    if not normalized_value:
        raise EventKeyValidationError(f"{field_name} не может быть пустым.")

    return normalized_value
