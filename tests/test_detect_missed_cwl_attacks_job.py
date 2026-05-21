"""Тесты worker job обнаружения пропущенных атак ЛВК."""

import asyncio
from datetime import UTC, datetime, timedelta

from app.db.models import ApiError, Clan, CwlSeason, CwlWar, TelegramUser, Warning
from app.domain import ClanType, WarningReasonCode, WarningSource, WarningStatus
from app.integrations.clash import ClashCwlWar, ClashForbiddenError
from app.services import WarningCreationService
from app.worker.jobs import DETECT_MISSED_CWL_ATTACKS_JOB_NAME, DetectMissedCwlAttacksJob
from app.worker.jobs.detect_missed_cwl_attacks import (
    CwlWarDetectionCandidate,
    LinkedCwlMember,
)
from app.worker.scheduler import WorkerJobContext, create_default_worker_registry


class InMemoryDetectMissedCwlAttacksRepository:
    """In-memory repository для unit-тестов detect missed cwl attacks job."""

    def __init__(
        self,
        *,
        candidates: list[CwlWarDetectionCandidate],
        linked_members: list[LinkedCwlMember],
    ) -> None:
        """Инициализирует repository.

        Args:
            candidates: CWL wars для проверки.
            linked_members: Telegram-привязки игроков.
        """
        self.candidates = candidates
        self.linked_members = linked_members
        self.api_errors: list[ApiError] = []
        self.flush_count = 0
        self.observed_at_calls: list[datetime] = []
        self.linked_member_calls: list[tuple[str, ...]] = []

    async def list_cwl_war_detection_candidates(
        self,
        *,
        observed_at: datetime,
    ) -> tuple[CwlWarDetectionCandidate, ...]:
        """Возвращает CWL wars для проверки.

        Args:
            observed_at: Время текущей проверки.

        Returns:
            Tuple CWL wars.
        """
        self.observed_at_calls.append(observed_at)
        return tuple(self.candidates)

    async def list_linked_members(
        self,
        *,
        player_tags: tuple[str, ...],
    ) -> tuple[LinkedCwlMember, ...]:
        """Возвращает linked members по tags.

        Args:
            player_tags: Теги игроков.

        Returns:
            Tuple linked members.
        """
        self.linked_member_calls.append(player_tags)
        requested_tags = set(player_tags)
        return tuple(
            member for member in self.linked_members if member.player_tag in requested_tags
        )

    def add_api_error(self, api_error: ApiError) -> None:
        """Добавляет ошибку API в память.

        Args:
            api_error: Модель ошибки API.
        """
        self.api_errors.append(api_error)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


class InMemoryWarningRepository:
    """In-memory repository для WarningCreationService."""

    def __init__(self, warnings: list[Warning] | None = None) -> None:
        """Инициализирует repository.

        Args:
            warnings: Начальный набор warn-записей.
        """
        self.warnings = warnings or []
        self.added_warnings: list[Warning] = []
        self.flush_count = 0

    async def get_by_event_key(self, event_key: str) -> Warning | None:
        """Возвращает warn по event key.

        Args:
            event_key: Идемпотентный ключ события.

        Returns:
            Warn или `None`.
        """
        for warning in self.warnings:
            if warning.event_key == event_key:
                return warning

        return None

    def add(self, warning: Warning) -> None:
        """Добавляет warn в память.

        Args:
            warning: Новая warn-запись.
        """
        self.added_warnings.append(warning)
        self.warnings.append(warning)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


class FakeClashCwlWarProvider:
    """Fake Clash API provider для detect missed cwl attacks job."""

    def __init__(self, results: dict[str, ClashCwlWar | BaseException]) -> None:
        """Инициализирует provider.

        Args:
            results: Mapping `war_tag -> DTO или exception`.
        """
        self.results = results
        self.calls: list[str] = []

    async def get_cwl_war(self, war_tag: str) -> ClashCwlWar:
        """Возвращает CWL-war или выбрасывает ошибку.

        Args:
            war_tag: War tag.

        Returns:
            DTO CWL-war.

        Raises:
            BaseException: Если fake настроен на ошибку.
        """
        self.calls.append(war_tag)
        result = self.results[war_tag]
        if isinstance(result, BaseException):
            raise result

        return result


async def test_detect_missed_cwl_attacks_job_creates_warn_after_round_war_end() -> None:
    """Проверяет создание warn после окончания конкретной CWL-war."""
    observed_at = datetime(2026, 5, 2, 22, 5, tzinfo=UTC)
    telegram_user = _make_telegram_user(user_id=101)
    clan = _make_clan(clan_type=ClanType.MAIN)
    cwl_season = _make_cwl_season(clan=clan)
    cwl_war = _make_cwl_war_model(
        cwl_season=cwl_season,
        end_time=observed_at - timedelta(minutes=5),
    )
    repository = InMemoryDetectMissedCwlAttacksRepository(
        candidates=[
            CwlWarDetectionCandidate(
                clan=clan,
                cwl_season=cwl_season,
                cwl_war=cwl_war,
            )
        ],
        linked_members=[
            LinkedCwlMember(
                player_tag="#P1",
                player_name="Bangkok",
                telegram_user=telegram_user,
            )
        ],
    )
    warning_repository = InMemoryWarningRepository()
    clash_provider = FakeClashCwlWarProvider(
        {
            "#WAR1": _make_cwl_war_payload(
                our_clan_tag="#MAIN",
                missed_player_tags=("#P1",),
                attacked_player_tags=("#P2",),
            )
        }
    )
    job = DetectMissedCwlAttacksJob(
        repository=repository,
        clash_client=clash_provider,
        warning_creator=WarningCreationService(repository=warning_repository),
        clock=lambda: observed_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 1
    assert result.checked_war_count == 1
    assert result.created_count == 1
    assert result.existing_count == 0
    assert clash_provider.calls == ["#WAR1"]
    assert repository.linked_member_calls == [("#P1",)]
    assert len(warning_repository.added_warnings) == 1

    warning = warning_repository.added_warnings[0]
    assert warning.telegram_user_id == 101
    assert warning.source == WarningSource.SYSTEM.value
    assert warning.status == WarningStatus.ACTIVE.value
    assert warning.reason_code == WarningReasonCode.CWL_ATTACK_MISSED.value
    assert warning.category == "cwl"
    assert warning.is_impactful is True
    assert warning.event_key == "warn:auto:cwl_attack_missed:#MAIN:#WAR1:101"
    assert warning.affected_player_tags_json == ["#P1"]
    assert warning.affected_player_names_json == ["Bangkok"]
    assert warning.created_cwl_season_key == "2026-05"


async def test_detect_missed_cwl_attacks_job_groups_multi_account_user_into_one_warn() -> None:
    """Проверяет один warn на TelegramUser за одну CWL-war с несколькими аккаунтами."""
    observed_at = datetime(2026, 5, 2, 22, 5, tzinfo=UTC)
    telegram_user = _make_telegram_user(user_id=101)
    clan = _make_clan(clan_type=ClanType.MAIN)
    cwl_season = _make_cwl_season(clan=clan)
    cwl_war = _make_cwl_war_model(
        cwl_season=cwl_season,
        end_time=observed_at - timedelta(minutes=5),
    )
    repository = InMemoryDetectMissedCwlAttacksRepository(
        candidates=[
            CwlWarDetectionCandidate(
                clan=clan,
                cwl_season=cwl_season,
                cwl_war=cwl_war,
            )
        ],
        linked_members=[
            LinkedCwlMember("#P1", "Bangkok", telegram_user),
            LinkedCwlMember("#P2", "Phoenix", telegram_user),
        ],
    )
    warning_repository = InMemoryWarningRepository()
    clash_provider = FakeClashCwlWarProvider(
        {
            "#WAR1": _make_cwl_war_payload(
                our_clan_tag="#MAIN",
                missed_player_tags=("#P1", "#P2"),
            )
        }
    )
    job = DetectMissedCwlAttacksJob(
        repository=repository,
        clash_client=clash_provider,
        warning_creator=WarningCreationService(repository=warning_repository),
        clock=lambda: observed_at,
    )

    result = await job.run(_build_context())

    assert result.created_count == 1
    assert len(warning_repository.added_warnings) == 1

    warning = warning_repository.added_warnings[0]
    assert warning.affected_player_tags_json == ["#P1", "#P2"]
    assert warning.affected_player_names_json == ["Bangkok", "Phoenix"]


async def test_detect_missed_cwl_attacks_job_waits_until_war_end_plus_5_minutes() -> None:
    """Проверяет, что warn не создаётся раньше end_time + 5 минут."""
    observed_at = datetime(2026, 5, 2, 22, 4, tzinfo=UTC)
    clan = _make_clan(clan_type=ClanType.MAIN)
    cwl_season = _make_cwl_season(clan=clan)
    cwl_war = _make_cwl_war_model(
        cwl_season=cwl_season,
        end_time=datetime(2026, 5, 2, 22, 0, tzinfo=UTC),
    )
    repository = InMemoryDetectMissedCwlAttacksRepository(
        candidates=[
            CwlWarDetectionCandidate(
                clan=clan,
                cwl_season=cwl_season,
                cwl_war=cwl_war,
            )
        ],
        linked_members=[],
    )
    warning_repository = InMemoryWarningRepository()
    clash_provider = FakeClashCwlWarProvider(
        {
            "#WAR1": _make_cwl_war_payload(
                our_clan_tag="#MAIN",
                missed_player_tags=("#P1",),
            )
        }
    )
    job = DetectMissedCwlAttacksJob(
        repository=repository,
        clash_client=clash_provider,
        warning_creator=WarningCreationService(repository=warning_repository),
        clock=lambda: observed_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 1
    assert result.checked_war_count == 0
    assert result.skipped_not_ready_count == 1
    assert clash_provider.calls == []
    assert warning_repository.added_warnings == []


async def test_detect_missed_cwl_attacks_job_skips_academy() -> None:
    """Проверяет, что academy не получает автоматический CWL-warn."""
    observed_at = datetime(2026, 5, 2, 22, 5, tzinfo=UTC)
    clan = _make_clan(clan_type=ClanType.ACADEMY)
    cwl_season = _make_cwl_season(clan=clan)
    cwl_war = _make_cwl_war_model(
        cwl_season=cwl_season,
        end_time=observed_at - timedelta(minutes=5),
    )
    repository = InMemoryDetectMissedCwlAttacksRepository(
        candidates=[
            CwlWarDetectionCandidate(
                clan=clan,
                cwl_season=cwl_season,
                cwl_war=cwl_war,
            )
        ],
        linked_members=[],
    )
    warning_repository = InMemoryWarningRepository()
    clash_provider = FakeClashCwlWarProvider(
        {
            "#WAR1": _make_cwl_war_payload(
                our_clan_tag="#MAIN",
                missed_player_tags=("#P1",),
            )
        }
    )
    job = DetectMissedCwlAttacksJob(
        repository=repository,
        clash_client=clash_provider,
        warning_creator=WarningCreationService(repository=warning_repository),
        clock=lambda: observed_at,
    )

    result = await job.run(_build_context())

    assert result.discovered_count == 1
    assert result.skipped_non_main_count == 1
    assert result.created_count == 0
    assert clash_provider.calls == []
    assert warning_repository.added_warnings == []


async def test_detect_missed_cwl_attacks_job_deduplicates_repeated_run() -> None:
    """Проверяет повторный worker-run без дубля warn."""
    observed_at = datetime(2026, 5, 2, 22, 5, tzinfo=UTC)
    telegram_user = _make_telegram_user(user_id=101)
    clan = _make_clan(clan_type=ClanType.MAIN)
    cwl_season = _make_cwl_season(clan=clan)
    cwl_war = _make_cwl_war_model(
        cwl_season=cwl_season,
        end_time=observed_at - timedelta(minutes=5),
    )
    repository = InMemoryDetectMissedCwlAttacksRepository(
        candidates=[
            CwlWarDetectionCandidate(
                clan=clan,
                cwl_season=cwl_season,
                cwl_war=cwl_war,
            )
        ],
        linked_members=[
            LinkedCwlMember(
                player_tag="#P1",
                player_name="Bangkok",
                telegram_user=telegram_user,
            )
        ],
    )
    warning_repository = InMemoryWarningRepository()
    clash_provider = FakeClashCwlWarProvider(
        {
            "#WAR1": _make_cwl_war_payload(
                our_clan_tag="#MAIN",
                missed_player_tags=("#P1",),
            )
        }
    )
    job = DetectMissedCwlAttacksJob(
        repository=repository,
        clash_client=clash_provider,
        warning_creator=WarningCreationService(repository=warning_repository),
        clock=lambda: observed_at,
    )

    first_result = await job.run(_build_context())
    second_result = await job.run(_build_context())

    assert first_result.created_count == 1
    assert first_result.existing_count == 0
    assert second_result.created_count == 0
    assert second_result.existing_count == 1
    assert len(warning_repository.warnings) == 1


async def test_detect_missed_cwl_attacks_job_records_api_error() -> None:
    """Проверяет запись ApiError при ошибке загрузки конкретной CWL-war."""
    observed_at = datetime(2026, 5, 2, 22, 5, tzinfo=UTC)
    clan = _make_clan(clan_type=ClanType.MAIN)
    cwl_season = _make_cwl_season(clan=clan)
    cwl_war = _make_cwl_war_model(
        cwl_season=cwl_season,
        end_time=observed_at - timedelta(minutes=5),
    )
    clash_error = ClashForbiddenError(
        "Clash API returned HTTP 403 for GET clanwarleagues/wars/%23WAR1.",
        endpoint="clanwarleagues/wars/%23WAR1",
        method="GET",
        status_code=403,
        response_snippet='{"reason":"accessDenied"}',
    )
    repository = InMemoryDetectMissedCwlAttacksRepository(
        candidates=[
            CwlWarDetectionCandidate(
                clan=clan,
                cwl_season=cwl_season,
                cwl_war=cwl_war,
            )
        ],
        linked_members=[],
    )
    warning_repository = InMemoryWarningRepository()
    clash_provider = FakeClashCwlWarProvider({"#WAR1": clash_error})
    job = DetectMissedCwlAttacksJob(
        repository=repository,
        clash_client=clash_provider,
        warning_creator=WarningCreationService(repository=warning_repository),
        clock=lambda: observed_at,
    )

    result = await job.run(_build_context())

    assert result.failed_api_count == 1
    assert result.created_count == 0
    assert len(repository.api_errors) == 1

    api_error = repository.api_errors[0]
    assert api_error.endpoint == "clanwarleagues/wars/%23WAR1"
    assert api_error.method == "GET"
    assert api_error.entity_type == "cwl_war"
    assert api_error.entity_tag == "#WAR1"
    assert api_error.status_code == 403
    assert api_error.response_snippet == '{"reason":"accessDenied"}'
    assert api_error.exception_class == "ClashForbiddenError"
    assert api_error.worker_name == DETECT_MISSED_CWL_ATTACKS_JOB_NAME
    assert api_error.retry_count == 0
    assert api_error.status == "unresolved"


def test_default_worker_registry_registers_detect_missed_cwl_attacks_job() -> None:
    """Проверяет, что default worker registry подключает detect missed cwl attacks job."""
    registry = create_default_worker_registry(default_interval_seconds=900)

    job = registry.get(DETECT_MISSED_CWL_ATTACKS_JOB_NAME)

    assert job.name == DETECT_MISSED_CWL_ATTACKS_JOB_NAME
    assert job.run_in_transaction is True
    assert job.run_on_start is True
    assert registry.resolve_interval_seconds(job) == 900


def _make_clan(
    *,
    clan_id: int = 1,
    clan_type: ClanType,
) -> Clan:
    """Создаёт клан для unit-тестов.

    Args:
        clan_id: DB ID клана.
        clan_type: Тип клана.

    Returns:
        Модель Clan.
    """
    return Clan(
        id=clan_id,
        tag="#MAIN",
        name="Bestiary",
        type=clan_type.value,
        is_active=True,
    )


def _make_cwl_season(*, clan: Clan) -> CwlSeason:
    """Создаёт CWL season для unit-тестов.

    Args:
        clan: Клан сезона.

    Returns:
        Модель CwlSeason.
    """
    return CwlSeason(
        id=301,
        clan_id=clan.id,
        season="2026-05",
        state="inWar",
        started_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
    )


def _make_cwl_war_model(*, cwl_season: CwlSeason, end_time: datetime) -> CwlWar:
    """Создаёт модель CWL-war для unit-тестов.

    Args:
        cwl_season: Сезон ЛВК.
        end_time: Время окончания войны.

    Returns:
        Модель CwlWar.
    """
    return CwlWar(
        id=401,
        cwl_season_id=cwl_season.id,
        cwl_season=cwl_season,
        round_number=1,
        war_tag="#WAR1",
        state="warEnded",
        our_clan_tag="#MAIN",
        opponent_clan_tag="#OPP",
        start_time=datetime(2026, 5, 1, 22, 0, tzinfo=UTC),
        end_time=end_time,
        our_stars=10,
        opponent_stars=9,
        our_destruction=88.5,
        opponent_destruction=77.25,
    )


def _make_telegram_user(*, user_id: int, telegram_id: int = 42) -> TelegramUser:
    """Создаёт TelegramUser для unit-тестов.

    Args:
        user_id: DB ID пользователя.
        telegram_id: Внешний Telegram ID.

    Returns:
        Модель TelegramUser.
    """
    return TelegramUser(
        id=user_id,
        telegram_id=telegram_id,
        username="bangkok",
        display_name="Bangkok",
    )


def _make_cwl_war_payload(
    *,
    our_clan_tag: str,
    missed_player_tags: tuple[str, ...],
    attacked_player_tags: tuple[str, ...] = (),
) -> ClashCwlWar:
    """Создаёт DTO конкретной CWL-war.

    Args:
        our_clan_tag: Тег нашего клана.
        missed_player_tags: Игроки без атак.
        attacked_player_tags: Игроки с атакой.

    Returns:
        DTO CWL-war.
    """
    members = []
    for index, player_tag in enumerate(missed_player_tags, start=1):
        members.append(
            {
                "tag": player_tag,
                "name": f"Missed {index}",
                "townhallLevel": 16,
                "mapPosition": index,
                "attacks": [],
            }
        )

    for index, player_tag in enumerate(attacked_player_tags, start=len(members) + 1):
        members.append(
            {
                "tag": player_tag,
                "name": f"Attacked {index}",
                "townhallLevel": 16,
                "mapPosition": index,
                "attacks": [
                    {
                        "order": index,
                        "attackerTag": player_tag,
                        "defenderTag": "#ENEMY",
                        "stars": 3,
                        "destructionPercentage": 100,
                        "duration": 120,
                    }
                ],
            }
        )

    return ClashCwlWar.from_payload(
        {
            "tag": "#WAR1",
            "state": "warEnded",
            "season": "2026-05",
            "startTime": "20260501T220000.000Z",
            "endTime": "20260502T220000.000Z",
            "clans": [
                {"tag": our_clan_tag, "name": "Bestiary"},
                {"tag": "#OPP", "name": "Enemy"},
            ],
            "clan": {
                "tag": our_clan_tag,
                "name": "Bestiary",
                "stars": 10,
                "destructionPercentage": 88.5,
                "attacks": len(attacked_player_tags),
                "members": members,
            },
            "opponent": {
                "tag": "#OPP",
                "name": "Enemy",
                "stars": 9,
                "destructionPercentage": 77.25,
                "attacks": 15,
                "members": [],
            },
        }
    )


def _build_context() -> WorkerJobContext:
    """Создаёт runtime-контекст для unit-тестов job.

    Returns:
        Контекст worker job без DB session.
    """
    return WorkerJobContext(
        job_name=DETECT_MISSED_CWL_ATTACKS_JOB_NAME,
        started_at=datetime(2026, 5, 2, tzinfo=UTC),
        stop_event=asyncio.Event(),
        session=None,
    )
