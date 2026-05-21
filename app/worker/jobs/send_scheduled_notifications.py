"""Worker job отправки запланированных Telegram-уведомлений."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings, get_settings
from app.db.models import (
    ApiError,
    Clan,
    ClanMemberSnapshot,
    KickCandidate,
    NotificationRoute,
    PlayerAccount,
    RaidMember,
    RaidSeason,
    WarMember,
    Warning,
    WarSnapshot,
)
from app.domain import (
    ClanType,
    KickCandidateReasonCode,
    KickCandidateStatus,
    NotificationType,
    WarningStatus,
    build_notification_event_key,
)
from app.services import (
    DailyAdminReportPayload,
    KickCandidateNotificationItem,
    KickCandidatesEveningPayload,
    NotificationLogService,
    NotificationRenderer,
    RaidReportPayload,
    RenderedNotification,
    UnlinkedAccountNotificationItem,
    UnlinkedAccountsEveningPayload,
    WarEndedPayload,
    WarPreparationStartedPayload,
    WarReminderPayload,
    WarStartedPayload,
)
from app.services.notification_sender import (
    AiogramTelegramNotificationSender,
    TelegramNotificationSender,
)
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

SEND_SCHEDULED_NOTIFICATIONS_JOB_NAME = "send_scheduled_notifications"

_MAIN_AND_ACADEMY_TYPES = frozenset({ClanType.MAIN.value, ClanType.ACADEMY.value})
_KICK_CANDIDATE_STATUS_PENDING = KickCandidateStatus.PENDING_ADMIN_DECISION.value
_API_ERROR_STATUS_UNRESOLVED = "unresolved"
_OUR_WAR_SIDE = "our"
_RAID_EXPECTED_ATTACKS_PER_MEMBER = 6

_WAR_PREPARATION_STATES = frozenset({"preparation", "preparationday"})
_WAR_ACTIVE_STATES = frozenset({"inwar"})
_WAR_ENDED_STATES = frozenset({"warended", "ended"})


@dataclass(frozen=True, slots=True)
class NotificationScheduleConfig:
    """Настройки времени scheduled notifications."""

    evening_hour_utc: int
    daily_report_hour_utc: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "NotificationScheduleConfig":
        """Создаёт config из runtime settings.

        Args:
            settings: Runtime-настройки приложения.

        Returns:
            Настройки расписания уведомлений.
        """
        return cls(
            evening_hour_utc=settings.notification_evening_hour_utc,
            daily_report_hour_utc=settings.notification_daily_report_hour_utc,
        )


@dataclass(frozen=True, slots=True)
class ScheduledNotificationEvent:
    """Событие уведомления, готовое к маршрутизации и отправке."""

    clan: Clan
    notification_type: NotificationType
    event_parts: tuple[object, ...]
    rendered: RenderedNotification


class ScheduledNotificationRepository(Protocol):
    """Repository contract для scheduled notification sender."""

    async def list_due_events(
        self,
        *,
        observed_at: datetime,
        schedule_config: NotificationScheduleConfig,
        renderer: NotificationRenderer,
    ) -> tuple[ScheduledNotificationEvent, ...]:
        """Возвращает события уведомлений, которые пора отправить.

        Args:
            observed_at: Время текущей проверки.
            schedule_config: Настройки расписания.
            renderer: Renderer Telegram-уведомлений.

        Returns:
            Tuple due notification events.
        """

    async def list_enabled_routes(
        self,
        *,
        clan_id: int,
        notification_type: NotificationType,
    ) -> tuple[NotificationRoute, ...]:
        """Возвращает enabled routes для клана и типа уведомления.

        Args:
            clan_id: DB ID клана.
            notification_type: Тип уведомления.

        Returns:
            Tuple маршрутов.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyScheduledNotificationRepository:
    """SQLAlchemy repository для scheduled notification sender."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_due_events(
        self,
        *,
        observed_at: datetime,
        schedule_config: NotificationScheduleConfig,
        renderer: NotificationRenderer,
    ) -> tuple[ScheduledNotificationEvent, ...]:
        """Возвращает due notification events.

        Args:
            observed_at: Время текущей проверки.
            schedule_config: Настройки расписания.
            renderer: Renderer уведомлений.

        Returns:
            Tuple due notification events.
        """
        events: list[ScheduledNotificationEvent] = []

        events.extend(await self._build_war_events(observed_at=observed_at, renderer=renderer))
        events.extend(await self._build_raid_events(observed_at=observed_at, renderer=renderer))

        if _is_after_configured_hour(observed_at, schedule_config.evening_hour_utc):
            events.extend(
                await self._build_evening_kick_candidate_events(
                    observed_at=observed_at,
                    renderer=renderer,
                )
            )
            events.extend(
                await self._build_evening_unlinked_account_events(
                    observed_at=observed_at,
                    renderer=renderer,
                )
            )

        if _is_after_configured_hour(observed_at, schedule_config.daily_report_hour_utc):
            events.extend(
                await self._build_daily_admin_report_events(
                    observed_at=observed_at,
                    renderer=renderer,
                )
            )

        return tuple(events)

    async def list_enabled_routes(
        self,
        *,
        clan_id: int,
        notification_type: NotificationType,
    ) -> tuple[NotificationRoute, ...]:
        """Возвращает enabled routes по clan + notification type.

        Args:
            clan_id: DB ID клана.
            notification_type: Тип уведомления.

        Returns:
            Tuple маршрутов.
        """
        result = await self._session.execute(
            select(NotificationRoute)
            .where(
                NotificationRoute.clan_id == clan_id,
                NotificationRoute.notification_type == notification_type.value,
                NotificationRoute.enabled.is_(True),
            )
            .order_by(NotificationRoute.id)
        )
        return tuple(result.scalars().all())

    async def _build_war_events(
        self,
        *,
        observed_at: datetime,
        renderer: NotificationRenderer,
    ) -> tuple[ScheduledNotificationEvent, ...]:
        """Строит due-события обычной войны.

        Args:
            observed_at: Время текущей проверки.
            renderer: Renderer уведомлений.

        Returns:
            Tuple war notification events.
        """
        result = await self._session.execute(
            select(WarSnapshot, Clan)
            .join(Clan, Clan.id == WarSnapshot.clan_id)
            .where(
                Clan.is_active.is_(True),
                Clan.type.in_(sorted(_MAIN_AND_ACADEMY_TYPES)),
            )
            .order_by(WarSnapshot.clan_id, WarSnapshot.snapshot_at.desc(), WarSnapshot.id.desc())
        )

        latest_by_clan_id: dict[int, tuple[WarSnapshot, Clan]] = {}
        for war_snapshot, clan in result.all():
            if war_snapshot.clan_id not in latest_by_clan_id:
                latest_by_clan_id[war_snapshot.clan_id] = (war_snapshot, clan)

        events: list[ScheduledNotificationEvent] = []
        for war_snapshot, clan in latest_by_clan_id.values():
            state = _normalize_state(war_snapshot.state)
            opponent_name = war_snapshot.opponent_name or war_snapshot.opponent_tag or "неизвестно"
            common_parts = (clan.id, war_snapshot.war_event_key)

            if (
                state in _WAR_PREPARATION_STATES
                and war_snapshot.preparation_start_time <= observed_at < war_snapshot.start_time
            ):
                town_halls_summary = await self._build_war_town_halls_summary(
                    war_snapshot_id=_required_model_id(war_snapshot, model_name="WarSnapshot")
                )
                events.append(
                    ScheduledNotificationEvent(
                        clan=clan,
                        notification_type=NotificationType.WAR_PREPARATION_STARTED,
                        event_parts=common_parts,
                        rendered=renderer.render_war_preparation_started(
                            WarPreparationStartedPayload(
                                clan_name=clan.name,
                                opponent_name=opponent_name,
                                team_size=war_snapshot.team_size,
                                starts_at_text=_format_datetime_utc(war_snapshot.start_time),
                                town_halls_summary=town_halls_summary,
                            )
                        ),
                    )
                )

            if war_snapshot.start_time <= observed_at < war_snapshot.end_time:
                events.append(
                    ScheduledNotificationEvent(
                        clan=clan,
                        notification_type=NotificationType.WAR_STARTED,
                        event_parts=common_parts,
                        rendered=renderer.render_war_started(
                            WarStartedPayload(
                                clan_name=clan.name,
                                opponent_name=opponent_name,
                                team_size=war_snapshot.team_size,
                                attacks_per_member=war_snapshot.attacks_per_member,
                                ends_at_text=_format_time_left(
                                    war_snapshot.end_time,
                                    observed_at=observed_at,
                                ),
                            )
                        ),
                    )
                )
                unused_attacks_count = await self._count_unused_war_attacks(
                    war_snapshot_id=_required_model_id(war_snapshot, model_name="WarSnapshot")
                )
                for notification_type, threshold in (
                    (NotificationType.WAR_12H_REMINDER, timedelta(hours=12)),
                    (NotificationType.WAR_3H_LEFT, timedelta(hours=3)),
                    (NotificationType.WAR_1H_LEFT, timedelta(hours=1)),
                ):
                    if observed_at >= war_snapshot.end_time - threshold:
                        events.append(
                            ScheduledNotificationEvent(
                                clan=clan,
                                notification_type=notification_type,
                                event_parts=common_parts,
                                rendered=renderer.render_war_reminder(
                                    WarReminderPayload(
                                        notification_type=notification_type,
                                        clan_name=clan.name,
                                        time_left_text=_format_time_left(
                                            war_snapshot.end_time,
                                            observed_at=observed_at,
                                        ),
                                        unused_attacks_count=unused_attacks_count,
                                    )
                                ),
                            )
                        )

            if observed_at >= war_snapshot.end_time or state in _WAR_ENDED_STATES:
                events.append(
                    ScheduledNotificationEvent(
                        clan=clan,
                        notification_type=NotificationType.WAR_ENDED,
                        event_parts=common_parts,
                        rendered=renderer.render_war_ended(
                            WarEndedPayload(
                                clan_name=clan.name,
                                opponent_name=opponent_name,
                                our_stars=war_snapshot.our_stars,
                                opponent_stars=war_snapshot.opponent_stars,
                                our_destruction=float(war_snapshot.our_destruction),
                                opponent_destruction=float(war_snapshot.opponent_destruction),
                            )
                        ),
                    )
                )

        return tuple(events)

    async def _build_raid_events(
        self,
        *,
        observed_at: datetime,
        renderer: NotificationRenderer,
    ) -> tuple[ScheduledNotificationEvent, ...]:
        """Строит due-события рейдов.

        Args:
            observed_at: Время текущей проверки.
            renderer: Renderer уведомлений.

        Returns:
            Tuple raid notification events.
        """
        result = await self._session.execute(
            select(RaidSeason, Clan)
            .join(Clan, Clan.id == RaidSeason.clan_id)
            .where(
                Clan.is_active.is_(True),
                Clan.type.in_(sorted(_MAIN_AND_ACADEMY_TYPES)),
            )
            .order_by(RaidSeason.clan_id, RaidSeason.start_time.desc(), RaidSeason.id.desc())
        )

        latest_by_clan_id: dict[int, tuple[RaidSeason, Clan]] = {}
        for raid_season, clan in result.all():
            if raid_season.clan_id not in latest_by_clan_id:
                latest_by_clan_id[raid_season.clan_id] = (raid_season, clan)

        events: list[ScheduledNotificationEvent] = []
        for raid_season, clan in latest_by_clan_id.values():
            if not (
                raid_season.start_time + timedelta(hours=12) <= observed_at < raid_season.end_time
            ):
                continue

            members = await self._list_raid_members(
                raid_season_id=_required_model_id(raid_season, model_name="RaidSeason")
            )
            attacks_used = sum(member.attacks for member in members)
            attacks_expected = len(members) * _RAID_EXPECTED_ATTACKS_PER_MEMBER
            events.append(
                ScheduledNotificationEvent(
                    clan=clan,
                    notification_type=NotificationType.RAID_12H_REPORT,
                    event_parts=(clan.id, raid_season.start_time),
                    rendered=renderer.render_raid_report(
                        RaidReportPayload(
                            clan_name=clan.name,
                            attacks_used=attacks_used,
                            attacks_expected=attacks_expected,
                            raid_status_text=raid_season.state,
                            ends_at_text=_format_time_left(
                                raid_season.end_time,
                                observed_at=observed_at,
                            ),
                        )
                    ),
                )
            )

        return tuple(events)

    async def _build_evening_kick_candidate_events(
        self,
        *,
        observed_at: datetime,
        renderer: NotificationRenderer,
    ) -> tuple[ScheduledNotificationEvent, ...]:
        """Строит вечерние уведомления по кандидатам на кик.

        Args:
            observed_at: Время текущей проверки.
            renderer: Renderer уведомлений.

        Returns:
            Tuple events или пустой tuple, если кандидатов нет.
        """
        items = await self._list_kick_candidate_items()
        if not items:
            return ()

        clans = await self._list_active_main_clans()
        return tuple(
            ScheduledNotificationEvent(
                clan=clan,
                notification_type=NotificationType.KICK_CANDIDATES_EVENING,
                event_parts=(clan.id, observed_at.date()),
                rendered=renderer.render_kick_candidates_evening(
                    KickCandidatesEveningPayload(items=items)
                ),
            )
            for clan in clans
        )

    async def _build_evening_unlinked_account_events(
        self,
        *,
        observed_at: datetime,
        renderer: NotificationRenderer,
    ) -> tuple[ScheduledNotificationEvent, ...]:
        """Строит вечерние уведомления по непривязанным аккаунтам.

        Args:
            observed_at: Время текущей проверки.
            renderer: Renderer уведомлений.

        Returns:
            Tuple events.
        """
        clans = await self._list_active_main_and_academy_clans()
        events: list[ScheduledNotificationEvent] = []

        for clan in clans:
            items = await self._list_unlinked_account_items(
                clan_id=_required_model_id(clan, model_name="Clan")
            )
            if not items:
                continue

            events.append(
                ScheduledNotificationEvent(
                    clan=clan,
                    notification_type=NotificationType.UNLINKED_ACCOUNTS_EVENING,
                    event_parts=(clan.id, observed_at.date()),
                    rendered=renderer.render_unlinked_accounts_evening(
                        UnlinkedAccountsEveningPayload(items=items)
                    ),
                )
            )

        return tuple(events)

    async def _build_daily_admin_report_events(
        self,
        *,
        observed_at: datetime,
        renderer: NotificationRenderer,
    ) -> tuple[ScheduledNotificationEvent, ...]:
        """Строит ежедневный админский отчёт.

        Args:
            observed_at: Время текущей проверки.
            renderer: Renderer уведомлений.

        Returns:
            Tuple daily admin report events.
        """
        clans = await self._list_active_main_clans()
        active_clans_count = await self._count_active_clans()
        active_warnings_count = await self._count_active_warnings()
        pending_kick_candidates_count = await self._count_pending_kick_candidates()
        api_errors_count = await self._count_unresolved_api_errors()

        return tuple(
            ScheduledNotificationEvent(
                clan=clan,
                notification_type=NotificationType.DAILY_ADMIN_REPORT,
                event_parts=(clan.id, observed_at.date()),
                rendered=renderer.render_daily_admin_report(
                    DailyAdminReportPayload(
                        date_text=observed_at.date().isoformat(),
                        active_clans_count=active_clans_count,
                        active_warnings_count=active_warnings_count,
                        pending_kick_candidates_count=pending_kick_candidates_count,
                        api_errors_count=api_errors_count,
                    )
                ),
            )
            for clan in clans
        )

    async def _build_war_town_halls_summary(self, *, war_snapshot_id: int) -> str:
        """Строит краткую сводку TH по нашему составу войны.

        Args:
            war_snapshot_id: DB ID snapshot войны.

        Returns:
            Строка вида `TH16 x3, TH15 x2`.
        """
        result = await self._session.execute(
            select(WarMember.town_hall_level, func.count())
            .where(
                WarMember.war_snapshot_id == war_snapshot_id,
                WarMember.side == _OUR_WAR_SIDE,
                WarMember.town_hall_level.is_not(None),
            )
            .group_by(WarMember.town_hall_level)
            .order_by(WarMember.town_hall_level.desc())
        )
        parts = [f"TH{th_level} x{count}" for th_level, count in result.all()]
        return ", ".join(parts) if parts else "нет данных"

    async def _count_unused_war_attacks(self, *, war_snapshot_id: int) -> int:
        """Считает неиспользованные атаки нашего клана.

        Args:
            war_snapshot_id: DB ID snapshot войны.

        Returns:
            Количество неиспользованных атак.
        """
        result = await self._session.execute(
            select(func.coalesce(func.sum(WarMember.attacks_left), 0)).where(
                WarMember.war_snapshot_id == war_snapshot_id,
                WarMember.side == _OUR_WAR_SIDE,
            )
        )
        return int(result.scalar_one())

    async def _list_raid_members(self, *, raid_season_id: int) -> tuple[RaidMember, ...]:
        """Возвращает участников рейдового сезона.

        Args:
            raid_season_id: DB ID рейдового сезона.

        Returns:
            Tuple участников.
        """
        result = await self._session.execute(
            select(RaidMember).where(RaidMember.raid_season_id == raid_season_id)
        )
        return tuple(result.scalars().all())

    async def _list_active_main_clans(self) -> tuple[Clan, ...]:
        """Возвращает active main clans.

        Returns:
            Tuple active main clans.
        """
        result = await self._session.execute(
            select(Clan)
            .where(Clan.is_active.is_(True), Clan.type == ClanType.MAIN.value)
            .order_by(Clan.id)
        )
        return tuple(result.scalars().all())

    async def _list_active_main_and_academy_clans(self) -> tuple[Clan, ...]:
        """Возвращает active main+academy clans.

        Returns:
            Tuple active main+academy clans.
        """
        result = await self._session.execute(
            select(Clan)
            .where(
                Clan.is_active.is_(True),
                Clan.type.in_(sorted(_MAIN_AND_ACADEMY_TYPES)),
            )
            .order_by(Clan.id)
        )
        return tuple(result.scalars().all())

    async def _list_kick_candidate_items(self) -> list[KickCandidateNotificationItem]:
        """Возвращает items вечернего списка кандидатов на кик.

        Returns:
            Список items для renderer-а.
        """
        result = await self._session.execute(
            select(KickCandidate, PlayerAccount)
            .outerjoin(PlayerAccount, PlayerAccount.player_tag == KickCandidate.player_tag)
            .where(KickCandidate.status == _KICK_CANDIDATE_STATUS_PENDING)
            .order_by(KickCandidate.id)
        )

        items: list[KickCandidateNotificationItem] = []
        for candidate, account in result.all():
            player_name = _kick_candidate_player_name(candidate=candidate, account=account)
            items.append(
                KickCandidateNotificationItem(
                    player_name=player_name,
                    telegram_username=None,
                    reason_text=_kick_candidate_reason_text(candidate.reason_code),
                )
            )

        return items

    async def _list_unlinked_account_items(
        self,
        *,
        clan_id: int,
    ) -> list[UnlinkedAccountNotificationItem]:
        """Возвращает непривязанные current members клана.

        Args:
            clan_id: DB ID клана.

        Returns:
            Список items для renderer-а.
        """
        linked_account_exists = (
            select(PlayerAccount.id)
            .where(
                PlayerAccount.player_tag == ClanMemberSnapshot.player_tag,
                PlayerAccount.is_active.is_(True),
                PlayerAccount.telegram_user_id.is_not(None),
            )
            .exists()
        )
        result = await self._session.execute(
            select(ClanMemberSnapshot)
            .where(
                ClanMemberSnapshot.clan_id == clan_id,
                ClanMemberSnapshot.is_current.is_(True),
                ~linked_account_exists,
            )
            .order_by(ClanMemberSnapshot.player_tag)
        )

        return [
            UnlinkedAccountNotificationItem(
                player_name=snapshot.name,
                player_tag=snapshot.player_tag,
                first_seen_text=_format_datetime_utc(snapshot.first_seen_at),
            )
            for snapshot in result.scalars().all()
        ]

    async def _count_active_clans(self) -> int:
        """Считает active clans.

        Returns:
            Количество active clans.
        """
        result = await self._session.execute(
            select(func.count()).select_from(Clan).where(Clan.is_active.is_(True))
        )
        return int(result.scalar_one())

    async def _count_active_warnings(self) -> int:
        """Считает active warn.

        Returns:
            Количество active warn.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(Warning)
            .where(Warning.status == WarningStatus.ACTIVE.value)
        )
        return int(result.scalar_one())

    async def _count_pending_kick_candidates(self) -> int:
        """Считает pending kick candidates.

        Returns:
            Количество pending candidates.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(KickCandidate)
            .where(KickCandidate.status == _KICK_CANDIDATE_STATUS_PENDING)
        )
        return int(result.scalar_one())

    async def _count_unresolved_api_errors(self) -> int:
        """Считает unresolved API errors.

        Returns:
            Количество unresolved API errors.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(ApiError)
            .where(ApiError.status == _API_ERROR_STATUS_UNRESOLVED)
        )
        return int(result.scalar_one())

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class SendScheduledNotificationsJobResult:
    """Результат одного запуска scheduled notification sender."""

    discovered_event_count: int
    route_count: int
    sent_count: int
    failed_count: int
    skipped_sent_count: int


class SendScheduledNotificationsJob:
    """Job отправки scheduled Telegram notifications."""

    def __init__(
        self,
        *,
        repository: ScheduledNotificationRepository,
        notification_log_service: NotificationLogService,
        notification_sender: TelegramNotificationSender,
        renderer: NotificationRenderer | None = None,
        schedule_config: NotificationScheduleConfig,
        clock: object | None = None,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository due-событий и маршрутов.
            notification_log_service: Сервис notification logs.
            notification_sender: Sender Telegram-уведомлений.
            renderer: Renderer уведомлений.
            schedule_config: Настройки расписания.
            clock: Источник времени для тестов.
        """
        self._repository = repository
        self._notification_log_service = notification_log_service
        self._notification_sender = notification_sender
        self._renderer = renderer or NotificationRenderer()
        self._schedule_config = schedule_config
        self._clock = clock

    @classmethod
    def from_session(
        cls,
        *,
        session: AsyncSession,
        notification_sender: TelegramNotificationSender,
        schedule_config: NotificationScheduleConfig,
    ) -> "SendScheduledNotificationsJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            notification_sender: Sender Telegram-уведомлений.
            schedule_config: Настройки расписания.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemyScheduledNotificationRepository(session),
            notification_log_service=NotificationLogService.from_session(session=session),
            notification_sender=notification_sender,
            renderer=NotificationRenderer(),
            schedule_config=schedule_config,
        )

    async def run(self, context: WorkerJobContext) -> SendScheduledNotificationsJobResult:
        """Отправляет due scheduled notifications.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        observed_at = self._now()
        events = await self._repository.list_due_events(
            observed_at=observed_at,
            schedule_config=self._schedule_config,
            renderer=self._renderer,
        )

        route_count = 0
        sent_count = 0
        failed_count = 0
        skipped_sent_count = 0

        for event in events:
            if context.should_stop:
                break

            routes = await self._repository.list_enabled_routes(
                clan_id=_required_model_id(event.clan, model_name="Clan"),
                notification_type=event.notification_type,
            )
            route_count += len(routes)

            for route in routes:
                if context.should_stop:
                    break

                event_key = build_notification_event_key(
                    event.notification_type,
                    *event.event_parts,
                    _required_model_id(route, model_name="NotificationRoute"),
                )

                if await self._notification_log_service.has_sent(event_key=event_key):
                    skipped_sent_count += 1
                    continue

                try:
                    send_result = await self._notification_sender.send(
                        route=route,
                        notification=event.rendered,
                    )
                except Exception as exc:
                    await self._notification_log_service.record_failed(
                        route=route,
                        notification_type=event.notification_type,
                        event_key=event_key,
                        payload_summary=event.rendered.payload_summary,
                        error_text=f"{type(exc).__name__}: {exc}",
                    )
                    failed_count += 1
                    continue

                await self._notification_log_service.record_sent(
                    route=route,
                    notification_type=event.notification_type,
                    event_key=event_key,
                    telegram_message_id=send_result.message_id,
                    payload_summary=event.rendered.payload_summary,
                )
                sent_count += 1

        await self._repository.flush()

        return SendScheduledNotificationsJobResult(
            discovered_event_count=len(events),
            route_count=route_count,
            sent_count=sent_count,
            failed_count=failed_count,
            skipped_sent_count=skipped_sent_count,
        )

    def _now(self) -> datetime:
        """Возвращает текущее время.

        Returns:
            Timezone-aware UTC datetime.
        """
        if callable(self._clock):
            value = self._clock()
            if not isinstance(value, datetime):
                raise TypeError("clock должен возвращать datetime.")
            return value

        return datetime.now(UTC)


def register_send_scheduled_notifications_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует scheduled notification sender в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=SEND_SCHEDULED_NOTIFICATIONS_JOB_NAME)
    async def send_scheduled_notifications(context: WorkerJobContext) -> None:
        """Запускает scheduled notification sender внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("send_scheduled_notifications требует DB session.")

        settings = get_settings()
        schedule_config = NotificationScheduleConfig.from_settings(settings)
        async with AiogramTelegramNotificationSender.from_settings(settings=settings) as sender:
            job = SendScheduledNotificationsJob.from_session(
                session=context.session,
                notification_sender=sender,
                schedule_config=schedule_config,
            )
            await job.run(context)


def _is_after_configured_hour(value: datetime, hour_utc: int) -> bool:
    """Проверяет, наступил ли configured hour в UTC.

    Args:
        value: Текущее время.
        hour_utc: Настроенный час UTC.

    Returns:
        `True`, если текущий час больше или равен настроенному.
    """
    if hour_utc < 0 or hour_utc > 23:
        raise ValueError("hour_utc должен быть в диапазоне 0..23.")

    return value.astimezone(UTC).hour >= hour_utc


def _normalize_state(value: str) -> str:
    """Нормализует state из API/БД для сравнений.

    Args:
        value: Сырое состояние.

    Returns:
        Нормализованная строка без `_`/`-`.
    """
    return value.replace("_", "").replace("-", "").strip().lower()


def _format_datetime_utc(value: datetime) -> str:
    """Форматирует datetime для plain text уведомления.

    Args:
        value: Datetime-значение.

    Returns:
        Строка в UTC.
    """
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _format_time_left(target: datetime, *, observed_at: datetime) -> str:
    """Форматирует оставшееся время.

    Args:
        target: Целевой datetime.
        observed_at: Текущее время.

    Returns:
        Текст оставшегося времени.
    """
    seconds_left = max(int((target - observed_at).total_seconds()), 0)
    hours, remainder = divmod(seconds_left, 3600)
    minutes = remainder // 60
    return f"{hours}ч {minutes}м"


def _kick_candidate_player_name(
    *,
    candidate: KickCandidate,
    account: PlayerAccount | None,
) -> str:
    """Определяет отображаемое имя кандидата на кик.

    Args:
        candidate: Кандидат на кик.
        account: Игровой аккаунт кандидата, если найден.

    Returns:
        Имя для уведомления.
    """
    if account is not None:
        return account.name

    if candidate.player_tag:
        return candidate.player_tag

    if candidate.telegram_user_id is not None:
        return f"TelegramUser {candidate.telegram_user_id}"

    return "неизвестный кандидат"


def _kick_candidate_reason_text(reason_code: str) -> str:
    """Возвращает человекочитаемую причину кандидата на кик.

    Args:
        reason_code: Код причины.

    Returns:
        Текст причины.
    """
    mapping = {
        KickCandidateReasonCode.TWO_IMPACTFUL_WARN.value: "2 активных боевых warn",
        KickCandidateReasonCode.UNLINKED_AFTER_3_DAYS.value: (
            "непривязанный аккаунт старше 3 дней"
        ),
        KickCandidateReasonCode.ALL_ACCOUNTS_LEFT.value: "все аккаунты вышли из кланов",
        KickCandidateReasonCode.LINKED_ACCOUNT_LEFT.value: "привязанный аккаунт вышел из клана",
        KickCandidateReasonCode.MANUAL_RECOMMENDATION.value: "ручная рекомендация",
    }
    return mapping.get(reason_code, reason_code)


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise ValueError(f"{model_name} должен быть сохранён в БД.")


__all__ = [
    "SEND_SCHEDULED_NOTIFICATIONS_JOB_NAME",
    "NotificationScheduleConfig",
    "ScheduledNotificationEvent",
    "ScheduledNotificationRepository",
    "SendScheduledNotificationsJob",
    "SendScheduledNotificationsJobResult",
    "SqlAlchemyScheduledNotificationRepository",
    "register_send_scheduled_notifications_job",
]
