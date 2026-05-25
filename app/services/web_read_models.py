"""Read models для пользовательских web-страниц.

Модуль собирает агрегированные данные для SSR-страниц. Он не вызывает Clash API,
не отправляет Telegram-сообщения и не меняет состояние БД.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Clan,
    ClanMemberSnapshot,
    CwlSeason,
    CwlWar,
    PlayerAccount,
    RaidMember,
    RaidSeason,
    WarSnapshot,
)
from app.domain.enums import ClanType, RaidMemberStatus

_SYNC_STATUS_OK = "ok"
_CLAN_TYPE_LABELS = {
    ClanType.MAIN.value: "Основа",
    ClanType.ACADEMY.value: "Академия",
    ClanType.FREEZER.value: "Морозилка",
}
_CLAN_TYPE_ICONS = {
    ClanType.MAIN.value: "🛡",
    ClanType.ACADEMY.value: "🎓",
    ClanType.FREEZER.value: "❄",
}
_CLAN_TYPE_VARIANTS = {
    ClanType.MAIN.value: "gold",
    ClanType.ACADEMY.value: "info",
    ClanType.FREEZER.value: "muted",
}


@dataclass(frozen=True, slots=True)
class DashboardMetricView:
    """View model верхней метрики Dashboard.

    Attributes:
        label: Название метрики.
        value: Основное значение.
        helper: Вспомогательный текст.
        variant: Семантический вариант отображения.
        icon: Текстовая иконка.
    """

    label: str
    value: str
    helper: str
    variant: str
    icon: str


@dataclass(frozen=True, slots=True)
class DashboardSummaryView:
    """Сводка Dashboard.

    Attributes:
        total_clans: Количество активных кланов.
        total_accounts: Количество аккаунтов в текущих составах.
        linked_accounts: Количество аккаунтов с Telegram-привязкой.
        real_people: Количество уникальных Telegram-пользователей.
        problems: Количество проблем/предупреждений для сводки.
        last_sync_text: Текст последней синхронизации.
    """

    total_clans: int
    total_accounts: int
    linked_accounts: int
    real_people: int
    problems: int
    last_sync_text: str

    @property
    def linked_percent_text(self) -> str:
        """Возвращает процент привязанных аккаунтов.

        Returns:
            Текст процента или `0%`, если аккаунтов нет.
        """
        if self.total_accounts <= 0:
            return "0%"

        return f"{round(self.linked_accounts / self.total_accounts * 100, 1)}%"

    @property
    def metrics(self) -> tuple[DashboardMetricView, ...]:
        """Возвращает карточки верхних метрик.

        Returns:
            Tuple метрик Dashboard.
        """
        return (
            DashboardMetricView(
                label="Всего кланов",
                value=str(self.total_clans),
                helper=f"Последняя синхронизация: {self.last_sync_text}",
                variant="info",
                icon="🛡",
            ),
            DashboardMetricView(
                label="Аккаунтов всего",
                value=str(self.total_accounts),
                helper="в текущих составах",
                variant="info",
                icon="👤",
            ),
            DashboardMetricView(
                label="Привязано Telegram",
                value=str(self.linked_accounts),
                helper=f"{self.linked_percent_text} от аккаунтов",
                variant="ok",
                icon="✈",
            ),
            DashboardMetricView(
                label="Реальных людей",
                value=str(self.real_people),
                helper="по Telegram",
                variant="gold",
                icon="👥",
            ),
            DashboardMetricView(
                label="Проблем / предупреждений",
                value=str(self.problems),
                helper="требуют внимания",
                variant="danger" if self.problems else "ok",
                icon="⚠",
            ),
        )


@dataclass(frozen=True, slots=True)
class DashboardWarLineView:
    """Краткая строка текущей войны для карточки клана."""

    state_label: str
    attacks_text: str
    score_text: str
    variant: str


@dataclass(frozen=True, slots=True)
class DashboardRaidLineView:
    """Краткая строка рейдов для карточки клана."""

    state_label: str
    attacks_text: str
    loot_text: str
    variant: str


@dataclass(frozen=True, slots=True)
class DashboardCwlLineView:
    """Краткая строка ЛВК для карточки клана."""

    state_label: str
    season_text: str
    round_text: str
    stars_text: str
    variant: str


@dataclass(frozen=True, slots=True)
class DashboardClanCardView:
    """View model карточки клана на Dashboard.

    Attributes:
        id: DB ID клана.
        tag: Тег клана.
        name: Название клана.
        type: Тип клана.
        type_label: Человекочитаемый тип.
        type_icon: Иконка типа.
        type_variant: Семантический вариант типа.
        level_text: Текст уровня.
        badge_url: URL badge из Clash API.
        sync_status_label: Подпись статуса синхронизации.
        sync_status_variant: Семантический вариант sync status.
        last_sync_text: Текст последней синхронизации.
        account_count: Количество аккаунтов в current-составе.
        linked_accounts_count: Количество привязанных аккаунтов.
        real_people_count: Количество уникальных TelegramUser.
        unlinked_accounts_count: Количество непривязанных аккаунтов.
        detail_url: URL будущей страницы клана.
        war: Краткая строка КВ.
        raid: Краткая строка рейдов.
        cwl: Краткая строка ЛВК.
    """

    id: int
    tag: str
    name: str
    type: str
    type_label: str
    type_icon: str
    type_variant: str
    level_text: str
    badge_url: str | None
    sync_status_label: str
    sync_status_variant: str
    last_sync_text: str
    account_count: int
    linked_accounts_count: int
    real_people_count: int
    unlinked_accounts_count: int
    detail_url: str
    war: DashboardWarLineView | None
    raid: DashboardRaidLineView | None
    cwl: DashboardCwlLineView | None

    @property
    def is_freezer(self) -> bool:
        """Проверяет, является ли клан морозилкой.

        Returns:
            `True`, если тип клана — `freezer`.
        """
        return self.type == ClanType.FREEZER.value


@dataclass(frozen=True, slots=True)
class DashboardClanGroupView:
    """Группа кланов на Dashboard."""

    title: str
    clan_type: str
    clans: tuple[DashboardClanCardView, ...]

    @property
    def has_clans(self) -> bool:
        """Проверяет наличие карточек в группе.

        Returns:
            `True`, если в группе есть кланы.
        """
        return bool(self.clans)


@dataclass(frozen=True, slots=True)
class DashboardView:
    """Полная view model Dashboard."""

    summary: DashboardSummaryView
    groups: tuple[DashboardClanGroupView, ...]

    @property
    def has_clans(self) -> bool:
        """Проверяет наличие активных кланов.

        Returns:
            `True`, если есть хотя бы один клан.
        """
        return any(group.has_clans for group in self.groups)


@dataclass(frozen=True, slots=True)
class _ClanCardBuildResult:
    """Внутренний результат сборки карточки клана."""

    card: DashboardClanCardView
    telegram_user_ids: frozenset[int]


class DashboardReadModelRepository(Protocol):
    """Repository contract для Dashboard read model."""

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает активные кланы."""

    async def list_current_members(self, *, clan_id: int) -> tuple[ClanMemberSnapshot, ...]:
        """Возвращает current-состав клана."""

    async def list_active_linked_accounts(
        self,
        *,
        player_tags: tuple[str, ...],
    ) -> tuple[PlayerAccount, ...]:
        """Возвращает активные аккаунты с Telegram-привязкой по тегам."""

    async def get_latest_war(self, *, clan_id: int) -> WarSnapshot | None:
        """Возвращает последний snapshot войны клана."""

    async def get_latest_raid(self, *, clan_id: int) -> RaidSeason | None:
        """Возвращает последний рейдовый сезон клана."""

    async def list_raid_members(self, *, raid_season_id: int) -> tuple[RaidMember, ...]:
        """Возвращает участников рейдового сезона."""

    async def get_latest_cwl_season(self, *, clan_id: int) -> CwlSeason | None:
        """Возвращает последний сезон ЛВК клана."""

    async def get_latest_cwl_war(self, *, cwl_season_id: int) -> CwlWar | None:
        """Возвращает последнюю войну сезона ЛВК."""


class SqlAlchemyDashboardReadModelRepository:
    """SQLAlchemy repository для Dashboard read model."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает активные кланы.

        Returns:
            Tuple активных кланов.
        """
        result = await self._session.execute(select(Clan).where(Clan.is_active.is_(True)))
        clans = tuple(result.scalars().all())

        return tuple(
            sorted(
                clans,
                key=lambda clan: (
                    _clan_type_sort_index(clan.type),
                    clan.name.lower(),
                    clan.tag,
                ),
            )
        )

    async def list_current_members(self, *, clan_id: int) -> tuple[ClanMemberSnapshot, ...]:
        """Возвращает current-состав клана."""
        result = await self._session.execute(
            select(ClanMemberSnapshot)
            .where(
                ClanMemberSnapshot.clan_id == clan_id,
                ClanMemberSnapshot.is_current.is_(True),
            )
            .order_by(
                ClanMemberSnapshot.town_hall_level.desc().nullslast(),
                ClanMemberSnapshot.name.asc(),
                ClanMemberSnapshot.player_tag.asc(),
            )
        )
        return tuple(result.scalars().all())

    async def list_active_linked_accounts(
        self,
        *,
        player_tags: tuple[str, ...],
    ) -> tuple[PlayerAccount, ...]:
        """Возвращает активные аккаунты с Telegram-привязкой по тегам."""
        if not player_tags:
            return ()

        result = await self._session.execute(
            select(PlayerAccount).where(
                PlayerAccount.player_tag.in_(player_tags),
                PlayerAccount.is_active.is_(True),
                PlayerAccount.telegram_user_id.is_not(None),
            )
        )
        return tuple(result.scalars().all())

    async def get_latest_war(self, *, clan_id: int) -> WarSnapshot | None:
        """Возвращает последний snapshot войны клана."""
        result = await self._session.execute(
            select(WarSnapshot)
            .where(WarSnapshot.clan_id == clan_id)
            .order_by(WarSnapshot.snapshot_at.desc(), WarSnapshot.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_raid(self, *, clan_id: int) -> RaidSeason | None:
        """Возвращает последний рейдовый сезон клана."""
        result = await self._session.execute(
            select(RaidSeason)
            .where(RaidSeason.clan_id == clan_id)
            .order_by(
                RaidSeason.start_time.desc(),
                RaidSeason.snapshot_at.desc(),
                RaidSeason.id.desc(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_raid_members(self, *, raid_season_id: int) -> tuple[RaidMember, ...]:
        """Возвращает участников рейдового сезона."""
        result = await self._session.execute(
            select(RaidMember)
            .where(RaidMember.raid_season_id == raid_season_id)
            .order_by(RaidMember.attacks.asc(), RaidMember.name.asc())
        )
        return tuple(result.scalars().all())

    async def get_latest_cwl_season(self, *, clan_id: int) -> CwlSeason | None:
        """Возвращает последний сезон ЛВК клана."""
        result = await self._session.execute(
            select(CwlSeason)
            .where(CwlSeason.clan_id == clan_id)
            .order_by(CwlSeason.started_at.desc(), CwlSeason.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_cwl_war(self, *, cwl_season_id: int) -> CwlWar | None:
        """Возвращает последнюю войну сезона ЛВК."""
        result = await self._session.execute(
            select(CwlWar)
            .where(CwlWar.cwl_season_id == cwl_season_id)
            .order_by(CwlWar.round_number.desc(), CwlWar.start_time.desc(), CwlWar.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


class DashboardReadModelService:
    """Сервис сборки Dashboard read model."""

    def __init__(self, *, repository: DashboardReadModelRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository чтения dashboard-данных.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "DashboardReadModelService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный read model service.
        """
        return cls(repository=SqlAlchemyDashboardReadModelRepository(session))

    async def get_dashboard(self) -> DashboardView:
        """Собирает Dashboard из сохранённых snapshot-данных.

        Returns:
            View model Dashboard.
        """
        clans = await self._repository.list_active_clans()
        build_results = [await self._build_clan_card(clan) for clan in clans]
        cards = tuple(result.card for result in build_results)
        people_ids = frozenset(
            telegram_user_id
            for result in build_results
            for telegram_user_id in result.telegram_user_ids
        )

        return DashboardView(
            summary=_build_summary(cards=cards, real_people_count=len(people_ids)),
            groups=_build_groups(cards),
        )

    async def _build_clan_card(self, clan: Clan) -> _ClanCardBuildResult:
        """Собирает карточку одного клана.

        Args:
            clan: Модель клана.

        Returns:
            Карточка клана и служебные TelegramUser IDs.
        """
        clan_id = _required_model_id(clan, model_name="Clan")
        current_members = await self._repository.list_current_members(clan_id=clan_id)
        current_player_tags = tuple(member.player_tag for member in current_members)
        linked_accounts = await self._repository.list_active_linked_accounts(
            player_tags=current_player_tags
        )
        linked_by_tag = {account.player_tag: account for account in linked_accounts}

        telegram_user_ids = frozenset(
            account.telegram_user_id
            for account in linked_accounts
            if account.telegram_user_id is not None
        )
        linked_accounts_count = sum(1 for tag in current_player_tags if tag in linked_by_tag)
        unlinked_accounts_count = max(len(current_members) - linked_accounts_count, 0)
        is_freezer = clan.type == ClanType.FREEZER.value

        war = None
        if not is_freezer:
            latest_war = await self._repository.get_latest_war(clan_id=clan_id)
            war = _build_war_line(latest_war)
        raid = None
        if not is_freezer:
            raid_season = await self._repository.get_latest_raid(clan_id=clan_id)
            raid = await self._build_raid_line(raid_season)

        cwl = None
        if not is_freezer:
            cwl_season = await self._repository.get_latest_cwl_season(clan_id=clan_id)
            cwl = await self._build_cwl_line(cwl_season)

        card = DashboardClanCardView(
            id=clan_id,
            tag=clan.tag,
            name=clan.name,
            type=clan.type,
            type_label=_clan_type_label(clan.type),
            type_icon=_clan_type_icon(clan.type),
            type_variant=_clan_type_variant(clan.type),
            level_text=_format_level(clan.level),
            badge_url=clan.badge_url,
            sync_status_label=_sync_status_label(clan.sync_status),
            sync_status_variant=_sync_status_variant(clan.sync_status),
            last_sync_text=_format_optional_datetime(clan.last_sync_at),
            account_count=len(current_members),
            linked_accounts_count=linked_accounts_count,
            real_people_count=len(telegram_user_ids),
            unlinked_accounts_count=unlinked_accounts_count,
            detail_url=f"/clans/{clan_id}",
            war=war,
            raid=raid,
            cwl=cwl,
        )

        return _ClanCardBuildResult(card=card, telegram_user_ids=telegram_user_ids)

    async def _build_raid_line(
        self, raid_season: RaidSeason | None
    ) -> DashboardRaidLineView | None:
        """Собирает строку рейдов.

        Args:
            raid_season: Последний рейдовый сезон.

        Returns:
            View model строки рейдов или `None`.
        """
        if raid_season is None:
            return None

        raid_season_id = _required_model_id(raid_season, model_name="RaidSeason")
        members = await self._repository.list_raid_members(raid_season_id=raid_season_id)
        used_attacks = sum(member.attacks for member in members)
        expected_attacks = sum(member.project_expected_attacks for member in members)
        problem_count = sum(
            1 for member in members if member.status != RaidMemberStatus.RAID_FULL.value
        )

        return DashboardRaidLineView(
            state_label=_state_label(raid_season.state),
            attacks_text=_format_ratio(used_attacks, expected_attacks, fallback="нет участников"),
            loot_text=f"{raid_season.capital_total_loot:,}".replace(",", " "),
            variant="warning" if problem_count else "ok",
        )

    async def _build_cwl_line(self, cwl_season: CwlSeason | None) -> DashboardCwlLineView | None:
        """Собирает строку ЛВК.

        Args:
            cwl_season: Последний сезон ЛВК.

        Returns:
            View model строки ЛВК или `None`.
        """
        if cwl_season is None:
            return None

        cwl_season_id = _required_model_id(cwl_season, model_name="CwlSeason")
        latest_war = await self._repository.get_latest_cwl_war(cwl_season_id=cwl_season_id)

        if latest_war is None:
            round_text = "раундов нет"
            stars_text = "звёзды —"
        else:
            round_text = f"Round {latest_war.round_number}"
            stars_text = f"{latest_war.our_stars} звёзд"

        return DashboardCwlLineView(
            state_label=_state_label(cwl_season.state),
            season_text=cwl_season.season,
            round_text=round_text,
            stars_text=stars_text,
            variant="info",
        )


def _build_war_line(war: WarSnapshot | None) -> DashboardWarLineView | None:
    """Собирает строку обычной войны.

    Args:
        war: Последний snapshot войны.

    Returns:
        View model строки войны или `None`.
    """
    if war is None:
        return None

    available_attacks = war.team_size * war.attacks_per_member
    return DashboardWarLineView(
        state_label=_state_label(war.state),
        attacks_text=_format_ratio(war.our_attacks, available_attacks, fallback="атак нет"),
        score_text=f"{war.our_stars} — {war.opponent_stars} звёзд",
        variant="ok" if war.our_stars >= war.opponent_stars else "warning",
    )


def _build_summary(
    *,
    cards: tuple[DashboardClanCardView, ...],
    real_people_count: int,
) -> DashboardSummaryView:
    """Собирает верхнюю сводку Dashboard.

    Args:
        cards: Карточки кланов.
        real_people_count: Количество уникальных TelegramUser.

    Returns:
        View model сводки.
    """
    problems = sum(card.unlinked_accounts_count for card in cards) + sum(
        1 for card in cards if card.sync_status_variant != "ok"
    )

    last_sync_candidates = [
        card.last_sync_text for card in cards if card.last_sync_text != "ещё не было"
    ]
    last_sync_text = last_sync_candidates[0] if last_sync_candidates else "ещё не было"

    return DashboardSummaryView(
        total_clans=len(cards),
        total_accounts=sum(card.account_count for card in cards),
        linked_accounts=sum(card.linked_accounts_count for card in cards),
        real_people=real_people_count,
        problems=problems,
        last_sync_text=last_sync_text,
    )


def _build_groups(cards: tuple[DashboardClanCardView, ...]) -> tuple[DashboardClanGroupView, ...]:
    """Группирует карточки кланов по типам.

    Args:
        cards: Карточки кланов.

    Returns:
        Tuple групп в порядке из ТЗ.
    """
    cards_by_type: dict[str, list[DashboardClanCardView]] = {
        ClanType.MAIN.value: [],
        ClanType.ACADEMY.value: [],
        ClanType.FREEZER.value: [],
    }

    for card in cards:
        cards_by_type.setdefault(card.type, []).append(card)

    return (
        DashboardClanGroupView(
            title="Основные",
            clan_type=ClanType.MAIN.value,
            clans=tuple(cards_by_type[ClanType.MAIN.value]),
        ),
        DashboardClanGroupView(
            title="Академии",
            clan_type=ClanType.ACADEMY.value,
            clans=tuple(cards_by_type[ClanType.ACADEMY.value]),
        ),
        DashboardClanGroupView(
            title="Морозилки",
            clan_type=ClanType.FREEZER.value,
            clans=tuple(cards_by_type[ClanType.FREEZER.value]),
        ),
    )


def _clan_type_sort_index(value: str) -> int:
    """Возвращает индекс сортировки типа клана."""
    if value == ClanType.MAIN.value:
        return 0

    if value == ClanType.ACADEMY.value:
        return 1

    if value == ClanType.FREEZER.value:
        return 2

    return 99


def _clan_type_label(value: str) -> str:
    """Возвращает подпись типа клана."""
    return _CLAN_TYPE_LABELS.get(value, value)


def _clan_type_icon(value: str) -> str:
    """Возвращает иконку типа клана."""
    return _CLAN_TYPE_ICONS.get(value, "●")


def _clan_type_variant(value: str) -> str:
    """Возвращает badge-вариант типа клана."""
    return _CLAN_TYPE_VARIANTS.get(value, "muted")


def _sync_status_label(value: str | None) -> str:
    """Возвращает подпись sync status."""
    if value == _SYNC_STATUS_OK:
        return "Sync: ok"

    if value:
        return f"Sync: {value}"

    return "Sync: нет данных"


def _sync_status_variant(value: str | None) -> str:
    """Возвращает семантический вариант sync status."""
    if value == _SYNC_STATUS_OK:
        return "ok"

    if value is None:
        return "muted"

    return "warning"


def _state_label(value: str) -> str:
    """Нормализует состояние внешнего события для UI."""
    normalized = value.replace("_", " ").replace("-", " ").strip()
    return normalized or "нет данных"


def _format_level(value: int | None) -> str:
    """Форматирует уровень клана."""
    if value is None:
        return "уровень —"

    return f"уровень {value}"


def _format_ratio(value: int, max_value: int, *, fallback: str) -> str:
    """Форматирует отношение чисел.

    Args:
        value: Текущее значение.
        max_value: Максимальное значение.
        fallback: Текст для случая, когда максимум неизвестен.

    Returns:
        Текст `value/max_value` или fallback.
    """
    if max_value <= 0:
        return fallback

    return f"{value}/{max_value}"


def _format_optional_datetime(value: datetime | None) -> str:
    """Форматирует datetime для Dashboard.

    Args:
        value: Datetime или `None`.

    Returns:
        Текст даты.
    """
    if value is None:
        return "ещё не было"

    return value.strftime("%Y-%m-%d %H:%M UTC")


def _format_decimal(value: Decimal) -> str:
    """Форматирует Decimal без лишних нулей."""
    return f"{float(value):.1f}".rstrip("0").rstrip(".")


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.

    Raises:
        RuntimeError: Если модель ещё не сохранена.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise RuntimeError(f"{model_name} должен быть сохранён в БД.")


__all__ = [
    "DashboardClanCardView",
    "DashboardClanGroupView",
    "DashboardCwlLineView",
    "DashboardMetricView",
    "DashboardRaidLineView",
    "DashboardReadModelRepository",
    "DashboardReadModelService",
    "DashboardSummaryView",
    "DashboardView",
    "DashboardWarLineView",
    "SqlAlchemyDashboardReadModelRepository",
]
