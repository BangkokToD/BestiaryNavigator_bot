"""Контракты и plain text rendering Telegram-уведомлений."""

from dataclasses import dataclass

from app.domain import NotificationType, WarningStatus, require_domain_enum_value


class NotificationRenderingError(RuntimeError):
    """Базовая ошибка rendering-слоя уведомлений."""


@dataclass(frozen=True, slots=True)
class RenderedNotification:
    """Результат rendering-а уведомления.

    Поле `parse_mode` оставлено для будущего перехода на HTML/Markdown, но
    текущая реализация намеренно возвращает plain text.
    """

    notification_type: NotificationType
    text: str
    payload_summary: str
    parse_mode: str | None = None


@dataclass(frozen=True, slots=True)
class WarPreparationStartedPayload:
    """Payload уведомления о начале подготовки войны."""

    clan_name: str
    opponent_name: str
    team_size: int
    starts_at_text: str
    town_halls_summary: str


@dataclass(frozen=True, slots=True)
class WarStartedPayload:
    """Payload уведомления о начале войны."""

    clan_name: str
    opponent_name: str
    team_size: int
    attacks_per_member: int
    ends_at_text: str


@dataclass(frozen=True, slots=True)
class WarReminderPayload:
    """Payload военного reminder-уведомления."""

    notification_type: NotificationType | str
    clan_name: str
    time_left_text: str
    unused_attacks_count: int


@dataclass(frozen=True, slots=True)
class RaidReportPayload:
    """Payload отчёта по рейдам."""

    clan_name: str
    attacks_used: int
    attacks_expected: int
    raid_status_text: str
    ends_at_text: str | None = None


@dataclass(frozen=True, slots=True)
class WarnNotificationPayload:
    """Payload warn-уведомления.

    В payload нет внутренних ID и игровых тегов. В текст выводятся только
    игровой ник и Telegram username.
    """

    status: WarningStatus | str
    player_name: str
    telegram_username: str | None
    reason_text: str
    cancelled_by_username: str | None = None
    cancelled_reason: str | None = None


@dataclass(frozen=True, slots=True)
class KickCandidateNotificationItem:
    """Один кандидат в вечернем списке на кик."""

    player_name: str
    telegram_username: str | None
    reason_text: str


@dataclass(frozen=True, slots=True)
class KickCandidatesEveningPayload:
    """Payload вечернего списка кандидатов на кик."""

    items: list[KickCandidateNotificationItem]


@dataclass(frozen=True, slots=True)
class ApiErrorNotificationItem:
    """Одна API-ошибка для админского уведомления."""

    endpoint: str
    status_code: int | None
    message: str


@dataclass(frozen=True, slots=True)
class ApiErrorAdminPayload:
    """Payload уведомления об API-ошибках."""

    errors: list[ApiErrorNotificationItem]


@dataclass(frozen=True, slots=True)
class DailyAdminReportPayload:
    """Payload ежедневного админского отчёта."""

    date_text: str
    active_clans_count: int
    active_warnings_count: int
    pending_kick_candidates_count: int
    api_errors_count: int


class NotificationRenderer:
    """Plain text renderer уведомлений.

    Renderer не отправляет сообщения, не пишет notification logs и не зависит от
    Telegram API. Его результат можно передать sender-слою и отдельно
    залогировать через `NotificationLogService`.
    """

    def render_war_preparation_started(
        self,
        payload: WarPreparationStartedPayload,
    ) -> RenderedNotification:
        """Рендерит уведомление о начале подготовки войны.

        Args:
            payload: Данные подготовки войны.

        Returns:
            Готовое уведомление.
        """
        team_size = _validate_non_negative_int(payload.team_size, field_name="team_size")
        starts_at_text = _required_text(payload.starts_at_text, field_name="starts_at_text")
        town_halls_summary = _required_text(
            payload.town_halls_summary,
            field_name="town_halls_summary",
        )
        text = _join_lines(
            "Подготовка войны началась",
            f"Клан: {_required_text(payload.clan_name, field_name='clan_name')}",
            f"Соперник: {_required_text(payload.opponent_name, field_name='opponent_name')}",
            f"Формат: {team_size} на {team_size}",
            f"Старт: {starts_at_text}",
            f"Состав по TH: {town_halls_summary}",
        )

        return RenderedNotification(
            notification_type=NotificationType.WAR_PREPARATION_STARTED,
            text=text,
            payload_summary=f"War preparation started for {payload.clan_name}",
        )

    def render_war_started(self, payload: WarStartedPayload) -> RenderedNotification:
        """Рендерит уведомление о начале войны.

        Args:
            payload: Данные начавшейся войны.

        Returns:
            Готовое уведомление.
        """
        team_size = _validate_non_negative_int(payload.team_size, field_name="team_size")
        attacks_per_member = _validate_non_negative_int(
            payload.attacks_per_member,
            field_name="attacks_per_member",
        )
        ends_at_text = _required_text(payload.ends_at_text, field_name="ends_at_text")
        text = _join_lines(
            "Война началась",
            f"Клан: {_required_text(payload.clan_name, field_name='clan_name')}",
            f"Соперник: {_required_text(payload.opponent_name, field_name='opponent_name')}",
            f"Формат: {team_size} на {team_size}",
            f"Атак на игрока: {attacks_per_member}",
            f"До конца: {ends_at_text}",
        )

        return RenderedNotification(
            notification_type=NotificationType.WAR_STARTED,
            text=text,
            payload_summary=f"War started for {payload.clan_name}",
        )

    def render_war_reminder(self, payload: WarReminderPayload) -> RenderedNotification:
        """Рендерит war reminder.

        Args:
            payload: Данные reminder-а.

        Returns:
            Готовое уведомление.
        """
        notification_type = _normalize_war_reminder_type(payload.notification_type)
        time_left_text = _required_text(payload.time_left_text, field_name="time_left_text")
        unused_attacks_count = _validate_non_negative_int(
            payload.unused_attacks_count,
            field_name="unused_attacks_count",
        )
        text = _join_lines(
            "Напоминание по войне",
            f"Клан: {_required_text(payload.clan_name, field_name='clan_name')}",
            f"Осталось: {time_left_text}",
            f"Неиспользованных атак: {unused_attacks_count}",
        )

        return RenderedNotification(
            notification_type=notification_type,
            text=text,
            payload_summary=f"War reminder {notification_type.value} for {payload.clan_name}",
        )

    def render_raid_report(self, payload: RaidReportPayload) -> RenderedNotification:
        """Рендерит отчёт по рейдам.

        Args:
            payload: Данные рейдового отчёта.

        Returns:
            Готовое уведомление.
        """
        attacks_used = _validate_non_negative_int(
            payload.attacks_used,
            field_name="attacks_used",
        )
        attacks_expected = _validate_non_negative_int(
            payload.attacks_expected,
            field_name="attacks_expected",
        )
        raid_status_text = _required_text(payload.raid_status_text, field_name="raid_status_text")
        lines = [
            "Отчёт по рейдам",
            f"Клан: {_required_text(payload.clan_name, field_name='clan_name')}",
            f"Атаки: {attacks_used} / {attacks_expected}",
            f"Статус: {raid_status_text}",
        ]
        if payload.ends_at_text is not None:
            ends_at_text = _required_text(payload.ends_at_text, field_name="ends_at_text")
            lines.append(f"До конца: {ends_at_text}")

        return RenderedNotification(
            notification_type=NotificationType.RAID_12H_REPORT,
            text=_join_lines(*lines),
            payload_summary=f"Raid report for {payload.clan_name}",
        )

    def render_warn_created(
        self,
        payload: WarnNotificationPayload,
    ) -> RenderedNotification | None:
        """Рендерит уведомление о созданном warn.

        Args:
            payload: Данные warn.

        Returns:
            Готовое уведомление или `None`, если warn expired.
        """
        if _is_expired_warn(payload.status):
            return None

        player = _format_player(payload.player_name, payload.telegram_username)
        text = _join_lines(
            "Warn выдан",
            f"Игрок: {player}",
            f"Причина: {_required_text(payload.reason_text, field_name='reason_text')}",
        )

        return RenderedNotification(
            notification_type=NotificationType.WARN_CREATED,
            text=text,
            payload_summary=f"Warn created for {payload.player_name}",
        )

    def render_warn_cancelled(
        self,
        payload: WarnNotificationPayload,
    ) -> RenderedNotification | None:
        """Рендерит уведомление об отменённом warn.

        Args:
            payload: Данные warn.

        Returns:
            Готовое уведомление или `None`, если warn expired.
        """
        if _is_expired_warn(payload.status):
            return None

        player = _format_player(payload.player_name, payload.telegram_username)
        cancelled_by = _format_username(payload.cancelled_by_username)
        reason = _required_text(
            payload.cancelled_reason or "без причины",
            field_name="cancelled_reason",
        )
        text = _join_lines(
            "Warn отменён",
            f"Игрок: {player}",
            f"Отменил: {cancelled_by}",
            f"Причина отмены: {reason}",
        )

        return RenderedNotification(
            notification_type=NotificationType.WARN_CANCELLED,
            text=text,
            payload_summary=f"Warn cancelled for {payload.player_name}",
        )

    def render_kick_candidates_evening(
        self,
        payload: KickCandidatesEveningPayload,
    ) -> RenderedNotification:
        """Рендерит вечерний список кандидатов на кик.

        Args:
            payload: Список кандидатов.

        Returns:
            Готовое уведомление.
        """
        if not payload.items:
            text = "Кандидаты на кик\nСегодня активных кандидатов нет."
        else:
            lines = ["Кандидаты на кик"]
            for index, item in enumerate(payload.items, start=1):
                lines.append(
                    f"{index}. {_format_player(item.player_name, item.telegram_username)} — "
                    f"{_required_text(item.reason_text, field_name='reason_text')}"
                )
            text = _join_lines(*lines)

        return RenderedNotification(
            notification_type=NotificationType.KICK_CANDIDATES_EVENING,
            text=text,
            payload_summary=f"Kick candidates evening: {len(payload.items)} item(s)",
        )

    def render_api_errors_admin(self, payload: ApiErrorAdminPayload) -> RenderedNotification:
        """Рендерит админское уведомление об API-ошибках.

        Args:
            payload: Ошибки API.

        Returns:
            Готовое уведомление.
        """
        if not payload.errors:
            text = "Ошибки API\nАктивных ошибок нет."
        else:
            lines = ["Ошибки API"]
            for index, item in enumerate(payload.errors, start=1):
                status = item.status_code if item.status_code is not None else "без HTTP status"
                lines.append(
                    f"{index}. {_required_text(item.endpoint, field_name='endpoint')} "
                    f"({status}): {_required_text(item.message, field_name='message')}"
                )
            text = _join_lines(*lines)

        return RenderedNotification(
            notification_type=NotificationType.API_ERRORS_ADMIN,
            text=text,
            payload_summary=f"API errors admin: {len(payload.errors)} item(s)",
        )

    def render_daily_admin_report(self, payload: DailyAdminReportPayload) -> RenderedNotification:
        """Рендерит ежедневный админский отчёт.

        Args:
            payload: Метрики отчёта.

        Returns:
            Готовое уведомление.
        """
        date_text = _required_text(payload.date_text, field_name="date_text")
        active_clans_count = _validate_non_negative_int(
            payload.active_clans_count,
            field_name="active_clans_count",
        )
        active_warnings_count = _validate_non_negative_int(
            payload.active_warnings_count,
            field_name="active_warnings_count",
        )
        pending_kick_candidates_count = _validate_non_negative_int(
            payload.pending_kick_candidates_count,
            field_name="pending_kick_candidates_count",
        )
        api_errors_count = _validate_non_negative_int(
            payload.api_errors_count,
            field_name="api_errors_count",
        )
        text = _join_lines(
            f"Ежедневный отчёт: {date_text}",
            f"Активных кланов: {active_clans_count}",
            f"Активных warn: {active_warnings_count}",
            f"Кандидатов на кик: {pending_kick_candidates_count}",
            f"Ошибок API: {api_errors_count}",
        )

        return RenderedNotification(
            notification_type=NotificationType.DAILY_ADMIN_REPORT,
            text=text,
            payload_summary=f"Daily admin report for {payload.date_text}",
        )


def _normalize_war_reminder_type(value: NotificationType | str) -> NotificationType:
    """Валидирует тип war reminder-а.

    Args:
        value: Тип уведомления.

    Returns:
        Enum member `NotificationType`.

    Raises:
        NotificationRenderingError: Если тип не является war reminder-ом.
    """
    notification_type = require_domain_enum_value(
        NotificationType,
        value,
        field_name="notification_type",
    )
    allowed_types = {
        NotificationType.WAR_6H_REMINDER,
        NotificationType.WAR_12H_REMINDER,
        NotificationType.WAR_3H_LEFT,
        NotificationType.WAR_1H_LEFT,
    }
    if notification_type not in allowed_types:
        raise NotificationRenderingError("notification_type должен быть war reminder type.")

    return notification_type


def _is_expired_warn(status: WarningStatus | str) -> bool:
    """Проверяет, является ли warn expired.

    Args:
        status: Статус warn.

    Returns:
        `True`, если warn expired.
    """
    warning_status = require_domain_enum_value(WarningStatus, status, field_name="warning_status")
    return warning_status == WarningStatus.EXPIRED


def _format_player(player_name: str, telegram_username: str | None) -> str:
    """Форматирует игрока без внутренних ID и игровых тегов.

    Args:
        player_name: Игровой ник.
        telegram_username: Telegram username.

    Returns:
        Строка вида `Name (@username)` или `Name (без username)`.
    """
    normalized_player_name = _required_text(player_name, field_name="player_name")
    return f"{normalized_player_name} ({_format_username(telegram_username)})"


def _format_username(value: str | None) -> str:
    """Форматирует Telegram username.

    Args:
        value: Username без `@` или с ним.

    Returns:
        `@username` или `без username`.
    """
    if value is None:
        return "без username"

    normalized = value.strip().lstrip("@")
    if not normalized:
        return "без username"

    return f"@{normalized}"


def _required_text(value: str, *, field_name: str) -> str:
    """Нормализует обязательный текст.

    Args:
        value: Сырое значение.
        field_name: Имя поля для текста ошибки.

    Returns:
        Строка без пробелов по краям.

    Raises:
        NotificationRenderingError: Если строка пустая.
    """
    if not isinstance(value, str):
        raise NotificationRenderingError(f"{field_name} должен быть строкой.")

    normalized = value.strip()
    if not normalized:
        raise NotificationRenderingError(f"{field_name} не может быть пустым.")

    return normalized


def _validate_non_negative_int(value: int, *, field_name: str) -> int:
    """Проверяет неотрицательный integer.

    Args:
        value: Проверяемое значение.
        field_name: Имя поля для текста ошибки.

    Returns:
        Проверенное значение.

    Raises:
        NotificationRenderingError: Если значение некорректное.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise NotificationRenderingError(f"{field_name} должен быть целым числом.")

    if value < 0:
        raise NotificationRenderingError(f"{field_name} не может быть отрицательным.")

    return value


def _join_lines(*lines: str) -> str:
    """Собирает строки сообщения.

    Args:
        *lines: Строки сообщения.

    Returns:
        Plain text сообщение.
    """
    return "\n".join(lines)


__all__ = [
    "ApiErrorAdminPayload",
    "ApiErrorNotificationItem",
    "DailyAdminReportPayload",
    "KickCandidateNotificationItem",
    "KickCandidatesEveningPayload",
    "NotificationRenderer",
    "NotificationRenderingError",
    "RaidReportPayload",
    "RenderedNotification",
    "WarPreparationStartedPayload",
    "WarReminderPayload",
    "WarStartedPayload",
    "WarnNotificationPayload",
]
