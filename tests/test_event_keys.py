"""Тесты построения идемпотентных event keys."""

from datetime import UTC, date, datetime
from hashlib import sha1
from pathlib import Path

import pytest

from app.domain import (
    EventKeyValidationError,
    NotificationType,
    WarningReasonCode,
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
from app.domain import event_keys as event_keys_module


def test_build_war_event_key_is_stable_for_same_input() -> None:
    """Проверяет стабильность war_event_key на одинаковых входных данных."""
    preparation_start_time = datetime(2026, 5, 18, 10, 0, tzinfo=UTC)
    start_time = datetime(2026, 5, 18, 22, 0, tzinfo=UTC)
    end_time = datetime(2026, 5, 19, 22, 0, tzinfo=UTC)

    first_key = build_war_event_key(
        clan_tag="2abc",
        opponent_tag="#9xyz",
        preparation_start_time=preparation_start_time,
        start_time=start_time,
        end_time=end_time,
        team_size=15,
    )
    second_key = build_war_event_key(
        clan_tag="#2ABC",
        opponent_tag="9XYZ",
        preparation_start_time=preparation_start_time,
        start_time=start_time,
        end_time=end_time,
        team_size=15,
    )

    payload = ":".join(
        (
            "#2ABC",
            "#9XYZ",
            "2026-05-18T10:00:00Z",
            "2026-05-18T22:00:00Z",
            "2026-05-19T22:00:00Z",
            "15",
        )
    )
    expected_key = sha1(payload.encode("utf-8")).hexdigest()

    assert first_key == second_key
    assert first_key == expected_key


def test_build_war_event_key_changes_when_input_changes() -> None:
    """Проверяет, что изменение входных данных меняет war_event_key."""
    base_kwargs = {
        "clan_tag": "#2ABC",
        "opponent_tag": "#9XYZ",
        "preparation_start_time": datetime(2026, 5, 18, 10, 0, tzinfo=UTC),
        "start_time": datetime(2026, 5, 18, 22, 0, tzinfo=UTC),
        "end_time": datetime(2026, 5, 19, 22, 0, tzinfo=UTC),
        "team_size": 15,
    }

    first_key = build_war_event_key(**base_kwargs)
    second_key = build_war_event_key(**(base_kwargs | {"team_size": 20}))

    assert first_key != second_key


def test_build_war_event_key_rejects_naive_datetime() -> None:
    """Проверяет запрет naive datetime для war_event_key."""
    with pytest.raises(EventKeyValidationError):
        build_war_event_key(
            clan_tag="#2ABC",
            opponent_tag="#9XYZ",
            preparation_start_time=datetime(2026, 5, 18, 10, 0),
            start_time=datetime(2026, 5, 18, 22, 0, tzinfo=UTC),
            end_time=datetime(2026, 5, 19, 22, 0, tzinfo=UTC),
            team_size=15,
        )


def test_build_war_warning_event_key() -> None:
    """Проверяет event key автоматического warn за КВ."""
    assert (
        build_war_warning_event_key(
            clan_tag="2abc",
            war_event_key="warhash",
            telegram_user_id=42,
        )
        == "warn:auto:war_attack_missed:#2ABC:warhash:42"
    )


def test_build_cwl_warning_event_key() -> None:
    """Проверяет event key автоматического warn за ЛВК."""
    assert (
        build_cwl_warning_event_key(
            clan_tag="2abc",
            cwl_war_tag="cwl123",
            telegram_user_id=42,
        )
        == "warn:auto:cwl_attack_missed:#2ABC:#CWL123:42"
    )


def test_build_raid_warning_event_key() -> None:
    """Проверяет event key автоматического warn за рейды."""
    raid_start_time = datetime(2026, 5, 18, 7, 30, tzinfo=UTC)

    assert (
        build_raid_warning_event_key(
            reason_code=WarningReasonCode.RAID_INCOMPLETE,
            clan_tag="#2ABC",
            raid_start_time=raid_start_time,
            telegram_user_id=42,
        )
        == "warn:auto:raid_incomplete:#2ABC:2026-05-18T07:30:00Z:42"
    )


def test_build_raid_warning_event_key_rejects_non_raid_reason() -> None:
    """Проверяет запрет не-рейдовой причины для рейдового event key."""
    with pytest.raises(EventKeyValidationError):
        build_raid_warning_event_key(
            reason_code=WarningReasonCode.WAR_ATTACK_MISSED,
            clan_tag="#2ABC",
            raid_start_time=datetime(2026, 5, 18, 7, 30, tzinfo=UTC),
            telegram_user_id=42,
        )


def test_build_kick_candidate_event_keys() -> None:
    """Проверяет event keys кандидатов на кик."""
    assert (
        build_two_impactful_warn_kick_event_key(
            telegram_user_id=42,
            season_key="2026-05",
        )
        == "kick:2_impactful_warn:42:2026-05"
    )
    assert (
        build_unlinked_account_kick_event_key(
            clan_id=7,
            player_tag="2abc",
        )
        == "kick:unlinked_after_3_days:7:#2ABC"
    )
    assert build_all_accounts_left_kick_event_key(telegram_user_id=42) == (
        "kick:all_accounts_left:42"
    )
    assert (
        build_linked_account_left_kick_event_key(
            telegram_user_id=42,
            player_tag="#2abc",
        )
        == "kick:linked_account_left:42:#2ABC"
    )


def test_build_notification_event_key_uses_notification_type_value() -> None:
    """Проверяет, что notification event key использует enum value."""
    assert (
        build_notification_event_key(
            NotificationType.WAR_6H_REMINDER,
            7,
            "warhash",
        )
        == "notify:war_6h_reminder:7:warhash"
    )


def test_build_notification_event_key_formats_date_and_datetime() -> None:
    """Проверяет форматирование date и timezone-aware datetime в notification key."""
    assert (
        build_notification_event_key(
            NotificationType.KICK_CANDIDATES_EVENING,
            date(2026, 5, 18),
        )
        == "notify:kick_candidates_evening:2026-05-18"
    )
    assert (
        build_notification_event_key(
            NotificationType.RAID_12H_REPORT,
            7,
            datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
            2,
        )
        == "notify:raid_12h_report:7:2026-05-18T12:00:00Z:2"
    )


def test_build_notification_event_key_rejects_invalid_parts() -> None:
    """Проверяет валидацию частей notification event key."""
    with pytest.raises(EventKeyValidationError):
        build_notification_event_key(NotificationType.WAR_STARTED)

    with pytest.raises(EventKeyValidationError):
        build_notification_event_key(NotificationType.WAR_STARTED, 0)

    with pytest.raises(EventKeyValidationError):
        build_notification_event_key(NotificationType.WAR_STARTED, True)

    with pytest.raises(EventKeyValidationError):
        build_notification_event_key(NotificationType.WAR_STARTED, "")

    with pytest.raises(EventKeyValidationError):
        build_notification_event_key(NotificationType.WAR_STARTED, datetime(2026, 5, 18, 12, 0))


def test_event_key_builders_do_not_use_random_uuid() -> None:
    """Проверяет отсутствие случайных UUID в построении event keys."""
    source = Path(event_keys_module.__file__).read_text(encoding="utf-8")

    assert "uuid" not in source.lower()
