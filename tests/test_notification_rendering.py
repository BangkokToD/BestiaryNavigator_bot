"""Тесты plain text rendering-а уведомлений."""

import pytest

from app.domain import NotificationType, WarningStatus
from app.services import (
    ApiErrorAdminPayload,
    ApiErrorNotificationItem,
    DailyAdminReportPayload,
    KickCandidateNotificationItem,
    KickCandidatesEveningPayload,
    NotificationRenderer,
    NotificationRenderingError,
    RaidReportPayload,
    RenderedNotification,
    WarnNotificationPayload,
    WarPreparationStartedPayload,
    WarReminderPayload,
    WarStartedPayload,
)


def test_notification_renderer_renders_war_started_plain_text() -> None:
    """Проверяет snapshot строки war_started."""
    renderer = NotificationRenderer()

    result = renderer.render_war_started(
        WarStartedPayload(
            clan_name="Bestiary",
            opponent_name="Enemy",
            team_size=15,
            attacks_per_member=2,
            ends_at_text="через 23 часа",
        )
    )

    assert result == RenderedNotification(
        notification_type=NotificationType.WAR_STARTED,
        text=(
            "Война началась\n"
            "Клан: Bestiary\n"
            "Соперник: Enemy\n"
            "Формат: 15 на 15\n"
            "Атак на игрока: 2\n"
            "До конца: через 23 часа"
        ),
        payload_summary="War started for Bestiary",
    )


def test_notification_renderer_renders_warn_created_without_ids_or_tags() -> None:
    """Проверяет warn_created без внутренних ID и игровых тегов."""
    renderer = NotificationRenderer()

    result = renderer.render_warn_created(
        WarnNotificationPayload(
            status=WarningStatus.ACTIVE,
            player_name="Bangkok",
            telegram_username="bangkok",
            reason_text="Пропуск атаки КВ",
        )
    )

    assert result is not None
    assert result.notification_type == NotificationType.WARN_CREATED
    assert result.text == ("Warn выдан\nИгрок: Bangkok (@bangkok)\nПричина: Пропуск атаки КВ")
    assert "#2ABC" not in result.text
    assert "telegram_user_id" not in result.text
    assert "101" not in result.text


def test_notification_renderer_returns_none_for_expired_warn() -> None:
    """Проверяет, что expired warn не создаёт notification."""
    renderer = NotificationRenderer()

    result = renderer.render_warn_created(
        WarnNotificationPayload(
            status=WarningStatus.EXPIRED,
            player_name="Bangkok",
            telegram_username="bangkok",
            reason_text="Пропуск атаки КВ",
        )
    )

    assert result is None


def test_notification_renderer_renders_kick_candidates_evening() -> None:
    """Проверяет snapshot вечернего списка кандидатов."""
    renderer = NotificationRenderer()

    result = renderer.render_kick_candidates_evening(
        KickCandidatesEveningPayload(
            items=[
                KickCandidateNotificationItem(
                    player_name="Bangkok",
                    telegram_username="bangkok",
                    reason_text="2 активных боевых warn",
                ),
                KickCandidateNotificationItem(
                    player_name="Phoenix",
                    telegram_username=None,
                    reason_text="непривязанный аккаунт старше 3 дней",
                ),
            ]
        )
    )

    assert result == RenderedNotification(
        notification_type=NotificationType.KICK_CANDIDATES_EVENING,
        text=(
            "Кандидаты на кик\n"
            "1. Bangkok (@bangkok) — 2 активных боевых warn\n"
            "2. Phoenix (без username) — непривязанный аккаунт старше 3 дней"
        ),
        payload_summary="Kick candidates evening: 2 item(s)",
    )


def test_notification_renderer_renders_remaining_contracts_without_telegram_api() -> None:
    """Проверяет минимальные renderers остальных контрактов."""
    renderer = NotificationRenderer()

    preparation = renderer.render_war_preparation_started(
        WarPreparationStartedPayload(
            clan_name="Bestiary",
            opponent_name="Enemy",
            team_size=15,
            starts_at_text="20:00",
            town_halls_summary="TH16 x5, TH15 x10",
        )
    )
    reminder = renderer.render_war_reminder(
        WarReminderPayload(
            notification_type=NotificationType.WAR_3H_LEFT,
            clan_name="Bestiary",
            time_left_text="3 часа",
            unused_attacks_count=4,
        )
    )
    raid = renderer.render_raid_report(
        RaidReportPayload(
            clan_name="Bestiary",
            attacks_used=210,
            attacks_expected=300,
            raid_status_text="идут",
        )
    )
    cancelled = renderer.render_warn_cancelled(
        WarnNotificationPayload(
            status=WarningStatus.CANCELLED,
            player_name="Bangkok",
            telegram_username=None,
            reason_text="Пропуск атаки",
            cancelled_by_username="admin",
            cancelled_reason="ошибка системы",
        )
    )
    api_errors = renderer.render_api_errors_admin(
        ApiErrorAdminPayload(
            errors=[
                ApiErrorNotificationItem(
                    endpoint="/clans/%232ABC",
                    status_code=429,
                    message="rate limit",
                )
            ]
        )
    )
    daily = renderer.render_daily_admin_report(
        DailyAdminReportPayload(
            date_text="2026-05-20",
            active_clans_count=3,
            active_warnings_count=4,
            pending_kick_candidates_count=2,
            api_errors_count=1,
        )
    )

    assert preparation.notification_type == NotificationType.WAR_PREPARATION_STARTED
    assert reminder.notification_type == NotificationType.WAR_3H_LEFT
    assert raid.notification_type == NotificationType.RAID_12H_REPORT
    assert cancelled is not None
    assert cancelled.notification_type == NotificationType.WARN_CANCELLED
    assert api_errors.notification_type == NotificationType.API_ERRORS_ADMIN
    assert daily.notification_type == NotificationType.DAILY_ADMIN_REPORT


def test_notification_renderer_rejects_invalid_war_reminder_type() -> None:
    """Проверяет валидацию типа war reminder-а."""
    renderer = NotificationRenderer()

    with pytest.raises(NotificationRenderingError):
        renderer.render_war_reminder(
            WarReminderPayload(
                notification_type=NotificationType.WAR_STARTED,
                clan_name="Bestiary",
                time_left_text="3 часа",
                unused_attacks_count=1,
            )
        )
