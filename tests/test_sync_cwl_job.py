"""Тесты worker job синхронизации ЛВК."""

import asyncio
from datetime import UTC, datetime

from app.db.models import ApiError, Clan, CwlSeason, CwlWar
from app.domain import ClanType
from app.integrations.clash import (
    ClashCwlLeagueGroup,
    ClashCwlWar,
    ClashForbiddenError,
    ClashNotFoundError,
)
from app.worker.jobs import SYNC_CWL_JOB_NAME, SyncCwlJob
from app.worker.scheduler import WorkerJobContext, create_default_worker_registry


class InMemorySyncCwlRepository:
    """In-memory repository для unit-тестов sync cwl job."""

    def __init__(self, clans: list[Clan]) -> None:
        """Инициализирует repository.

        Args:
            clans: Набор кланов для тестового запуска.
        """
        self.clans = clans
        self.cwl_seasons: list[CwlSeason] = []
        self.cwl_wars: list[CwlWar] = []
        self.api_errors: list[ApiError] = []
        self.flush_count = 0
        self._next_season_id = 1

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает кланы для тестового запуска.

        Returns:
            Tuple кланов.
        """
        return tuple(self.clans)

    async def get_cwl_season(self, *, clan_id: int, season: str) -> CwlSeason | None:
        """Возвращает сезон ЛВК по клану и ключу сезона.

        Args:
            clan_id: DB ID клана.
            season: Ключ сезона.

        Returns:
            Сезон ЛВК или `None`.
        """
        for cwl_season in self.cwl_seasons:
            if cwl_season.clan_id == clan_id and cwl_season.season == season:
                return cwl_season

        return None

    def add_cwl_season(self, cwl_season: CwlSeason) -> None:
        """Добавляет сезон ЛВК в память.

        Args:
            cwl_season: Новая модель сезона.
        """
        cwl_season.id = self._next_season_id
        self._next_season_id += 1
        self.cwl_seasons.append(cwl_season)

    async def get_cwl_war_by_tag(self, war_tag: str) -> CwlWar | None:
        """Возвращает войну ЛВК по war tag.

        Args:
            war_tag: War tag.

        Returns:
            Модель войны или `None`.
        """
        for cwl_war in self.cwl_wars:
            if cwl_war.war_tag == war_tag:
                return cwl_war

        return None

    def add_cwl_war(self, cwl_war: CwlWar) -> None:
        """Добавляет войну ЛВК в память.

        Args:
            cwl_war: Новая модель войны.
        """
        self.cwl_wars.append(cwl_war)

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в память.

        Args:
            api_error: Модель ошибки внешнего API.
        """
        self.api_errors.append(api_error)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


class FakeClashCwlProvider:
    """Fake Clash API provider для sync cwl job."""

    def __init__(
        self,
        *,
        league_groups: dict[str, ClashCwlLeagueGroup | BaseException],
        wars: dict[str, ClashCwlWar | BaseException],
    ) -> None:
        """Инициализирует provider.

        Args:
            league_groups: Mapping `clan_tag -> league group или exception`.
            wars: Mapping `war_tag -> cwl war или exception`.
        """
        self.league_groups = league_groups
        self.wars = wars
        self.league_group_calls: list[str] = []
        self.war_calls: list[str] = []

    async def get_cwl_league_group(self, clan_tag: str) -> ClashCwlLeagueGroup:
        """Возвращает League Group или выбрасывает настроенную ошибку.

        Args:
            clan_tag: Тег клана.

        Returns:
            DTO League Group.

        Raises:
            BaseException: Если fake настроен на ошибку.
        """
        self.league_group_calls.append(clan_tag)
        result = self.league_groups[clan_tag]

        if isinstance(result, BaseException):
            raise result

        return result

    async def get_cwl_war(self, war_tag: str) -> ClashCwlWar:
        """Возвращает CWL war или выбрасывает настроенную ошибку.

        Args:
            war_tag: War tag.

        Returns:
            DTO войны ЛВК.

        Raises:
            BaseException: Если fake настроен на ошибку.
        """
        self.war_calls.append(war_tag)
        result = self.wars[war_tag]

        if isinstance(result, BaseException):
            raise result

        return result


async def test_sync_cwl_job_creates_season_and_tracked_wars_only() -> None:
    """Проверяет создание сезона и сохранение только войн tracked clan."""
    observed_at = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    clan = _make_clan(clan_id=1, tag="#MAIN", is_active=True)
    repository = InMemorySyncCwlRepository([clan])
    clash_provider = FakeClashCwlProvider(
        league_groups={
            "#MAIN": _make_league_group(
                state="inWar",
                rounds=(("#WAR1", "#0"), ("#WAR2",)),
            )
        },
        wars={
            "#WAR1": _make_cwl_war(
                war_tag="#WAR1",
                our_clan_tag="#MAIN",
                opponent_clan_tag="#OPP",
                state="inWar",
                our_stars=3,
            ),
            "#WAR2": _make_cwl_war(
                war_tag="#WAR2",
                our_clan_tag="#OTHER",
                opponent_clan_tag="#OPP",
                state="inWar",
            ),
        },
    )
    job = SyncCwlJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: observed_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 1
    assert result.synced_group_count == 1
    assert result.no_cwl_count == 0
    assert result.created_season_count == 1
    assert result.updated_season_count == 0
    assert result.created_war_count == 1
    assert result.updated_war_count == 0
    assert result.skipped_placeholder_war_count == 1
    assert result.skipped_unrelated_war_count == 1
    assert clash_provider.league_group_calls == ["#MAIN"]
    assert clash_provider.war_calls == ["#WAR1", "#WAR2"]

    assert len(repository.cwl_seasons) == 1
    season = repository.cwl_seasons[0]
    assert season.clan_id == 1
    assert season.season == "2026-05"
    assert season.state == "inWar"
    assert season.started_at == observed_at
    assert season.ended_at is None

    assert len(repository.cwl_wars) == 1
    war = repository.cwl_wars[0]
    assert war.cwl_season_id == season.id
    assert war.round_number == 1
    assert war.war_tag == "#WAR1"
    assert war.state == "inWar"
    assert war.our_clan_tag == "#MAIN"
    assert war.opponent_clan_tag == "#OPP"
    assert war.our_stars == 3
    assert war.opponent_stars == 0


async def test_sync_cwl_job_treats_league_group_404_as_no_cwl() -> None:
    """Проверяет, что 404 League Group считается отсутствием активной ЛВК."""
    clan = _make_clan(clan_id=1, tag="#MAIN", is_active=True)
    not_found_error = ClashNotFoundError(
        "Clash API returned HTTP 404 for GET clans/%23MAIN/currentwar/leaguegroup.",
        endpoint="clans/%23MAIN/currentwar/leaguegroup",
        method="GET",
        status_code=404,
        response_snippet='{"reason":"notFound"}',
    )
    repository = InMemorySyncCwlRepository([clan])
    clash_provider = FakeClashCwlProvider(
        league_groups={"#MAIN": not_found_error},
        wars={},
    )
    job = SyncCwlJob(repository=repository, clash_client=clash_provider)

    result = await job.run(_build_context())

    assert result.discovered_count == 1
    assert result.synced_group_count == 0
    assert result.no_cwl_count == 1
    assert result.failed_group_count == 0
    assert repository.cwl_seasons == []
    assert repository.cwl_wars == []
    assert repository.api_errors == []


async def test_sync_cwl_job_records_group_api_error_and_continues_next_clan() -> None:
    """Проверяет error path League Group: ApiError пишется, следующий клан работает."""
    broken_clan = _make_clan(clan_id=1, tag="#ERR", is_active=True)
    healthy_clan = _make_clan(clan_id=2, tag="#MAIN", is_active=True)
    clash_error = ClashForbiddenError(
        "Clash API returned HTTP 403 for GET clans/%23ERR/currentwar/leaguegroup.",
        endpoint="clans/%23ERR/currentwar/leaguegroup",
        method="GET",
        status_code=403,
        response_snippet='{"reason":"accessDenied"}',
    )
    repository = InMemorySyncCwlRepository([broken_clan, healthy_clan])
    clash_provider = FakeClashCwlProvider(
        league_groups={
            "#ERR": clash_error,
            "#MAIN": _make_league_group(state="inWar", rounds=(("#WAR1",),)),
        },
        wars={
            "#WAR1": _make_cwl_war(
                war_tag="#WAR1",
                our_clan_tag="#MAIN",
                opponent_clan_tag="#OPP",
            )
        },
    )
    job = SyncCwlJob(repository=repository, clash_client=clash_provider)

    result = await job.run(_build_context())

    assert result.discovered_count == 2
    assert result.synced_group_count == 1
    assert result.failed_group_count == 1
    assert len(repository.cwl_seasons) == 1
    assert len(repository.cwl_wars) == 1

    assert len(repository.api_errors) == 1
    api_error = repository.api_errors[0]
    assert api_error.endpoint == "clans/%23ERR/currentwar/leaguegroup"
    assert api_error.method == "GET"
    assert api_error.entity_type == "cwl_league_group"
    assert api_error.entity_tag == "#ERR"
    assert api_error.status_code == 403
    assert api_error.response_snippet == '{"reason":"accessDenied"}'
    assert api_error.exception_class == "ClashForbiddenError"
    assert api_error.worker_name == SYNC_CWL_JOB_NAME
    assert api_error.retry_count == 0
    assert api_error.status == "unresolved"


async def test_sync_cwl_job_updates_existing_season_and_war_without_duplicates() -> None:
    """Проверяет повторный запуск без дублей season/war и с ended_at."""
    first_seen_at = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    ended_seen_at = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    clan = _make_clan(clan_id=1, tag="#MAIN", is_active=True)
    repository = InMemorySyncCwlRepository([clan])
    clash_provider = FakeClashCwlProvider(
        league_groups={
            "#MAIN": _make_league_group(state="inWar", rounds=(("#WAR1",),)),
        },
        wars={
            "#WAR1": _make_cwl_war(
                war_tag="#WAR1",
                our_clan_tag="#MAIN",
                opponent_clan_tag="#OPP",
                state="inWar",
                our_stars=0,
            )
        },
    )
    first_job = SyncCwlJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: first_seen_at,
    )

    first_result = await first_job.run(_build_context())

    clash_provider.league_groups["#MAIN"] = _make_league_group(
        state="ended",
        rounds=(("#WAR1",),),
    )
    clash_provider.wars["#WAR1"] = _make_cwl_war(
        war_tag="#WAR1",
        our_clan_tag="#MAIN",
        opponent_clan_tag="#OPP",
        state="warEnded",
        our_stars=7,
    )
    second_job = SyncCwlJob(
        repository=repository,
        clash_client=clash_provider,
        clock=lambda: ended_seen_at,
    )
    second_result = await second_job.run(_build_context())

    assert first_result.created_season_count == 1
    assert first_result.created_war_count == 1
    assert second_result.created_season_count == 0
    assert second_result.updated_season_count == 1
    assert second_result.created_war_count == 0
    assert second_result.updated_war_count == 1

    assert len(repository.cwl_seasons) == 1
    assert len(repository.cwl_wars) == 1

    season = repository.cwl_seasons[0]
    assert season.state == "ended"
    assert season.started_at == first_seen_at
    assert season.ended_at == ended_seen_at

    war = repository.cwl_wars[0]
    assert war.state == "warEnded"
    assert war.our_stars == 7


def test_default_worker_registry_registers_sync_cwl_job() -> None:
    """Проверяет, что default worker registry подключает sync cwl job."""
    registry = create_default_worker_registry(default_interval_seconds=900)

    job = registry.get(SYNC_CWL_JOB_NAME)

    assert job.name == SYNC_CWL_JOB_NAME
    assert job.run_in_transaction is True
    assert job.run_on_start is True
    assert registry.resolve_interval_seconds(job) == 900


def _make_clan(*, clan_id: int, tag: str, is_active: bool) -> Clan:
    """Создаёт клан для unit-тестов.

    Args:
        clan_id: DB id клана.
        tag: Нормализованный тег клана.
        is_active: Флаг активного мониторинга.

    Returns:
        Модель клана.
    """
    return Clan(
        id=clan_id,
        tag=tag,
        name="Bestiary",
        type=ClanType.MAIN.value,
        is_active=is_active,
    )


def _make_league_group(
    *,
    state: str,
    rounds: tuple[tuple[str, ...], ...],
) -> ClashCwlLeagueGroup:
    """Создаёт DTO League Group.

    Args:
        state: Состояние League Group.
        rounds: Раунды с warTags.

    Returns:
        DTO League Group.
    """
    return ClashCwlLeagueGroup(
        tag="#GROUP",
        state=state,
        season="2026-05",
        clan_tags=("#MAIN", "#OPP"),
        rounds=rounds,
    )


def _make_cwl_war(
    *,
    war_tag: str,
    our_clan_tag: str,
    opponent_clan_tag: str,
    state: str = "inWar",
    our_stars: int = 0,
) -> ClashCwlWar:
    """Создаёт DTO конкретной CWL-war.

    Args:
        war_tag: War tag.
        our_clan_tag: Тег первой стороны.
        opponent_clan_tag: Тег второй стороны.
        state: Состояние войны.
        our_stars: Звёзды первой стороны.

    Returns:
        DTO войны ЛВК.
    """
    return ClashCwlWar.from_payload(
        {
            "tag": war_tag,
            "state": state,
            "season": "2026-05",
            "startTime": "20260501T220000.000Z",
            "endTime": "20260502T220000.000Z",
            "clans": [
                {"tag": our_clan_tag, "name": "Bestiary"},
                {"tag": opponent_clan_tag, "name": "Enemy"},
            ],
            "clan": {
                "tag": our_clan_tag,
                "name": "Bestiary",
                "stars": our_stars,
                "destructionPercentage": 88.5,
                "attacks": 10,
            },
            "opponent": {
                "tag": opponent_clan_tag,
                "name": "Enemy",
                "stars": 0,
                "destructionPercentage": 42.5,
                "attacks": 8,
            },
        }
    )


def _build_context() -> WorkerJobContext:
    """Создаёт runtime-контекст для unit-тестов job.

    Returns:
        Контекст worker job без DB session.
    """
    return WorkerJobContext(
        job_name=SYNC_CWL_JOB_NAME,
        started_at=datetime(2026, 5, 1, tzinfo=UTC),
        stop_event=asyncio.Event(),
        session=None,
    )
