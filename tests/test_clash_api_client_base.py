"""Тесты базового Clash API HTTP client."""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

import app.integrations.clash.client as clash_client_module
import app.integrations.clash.dto as clash_dto_module
from app.core.settings import Settings
from app.integrations.clash import (
    ClashApiClient,
    ClashApiError,
    ClashCapitalRaidSeason,
    ClashClan,
    ClashClanMember,
    ClashCurrentWar,
    ClashCwlLeagueGroup,
    ClashCwlWar,
    ClashForbiddenError,
    ClashNotFoundError,
    ClashRaidMember,
    ClashRateLimitError,
    ClashServerError,
    ClashTimeoutError,
    ClashWarLogEntry,
    VerifyPlayerTokenResult,
)

VALID_SETTINGS = {
    "APP_ENV": "local",
    "APP_BASE_URL": "http://localhost:8000",
    "DATABASE_URL": "postgresql+asyncpg://bn:bn@postgres:5432/bestiary",
    "CLASH_API_BASE_URL": "https://api.clashofclans.com/v1",
    "CLASH_API_TOKEN": "test-clash-token-123",
    "CLASH_API_TIMEOUT_SECONDS": 7,
    "TELEGRAM_BOT_TOKEN": "test-telegram-token-123",
    "TELEGRAM_ADMIN_ID": 123456789,
    "WEB_SESSION_SECRET": "test-session-secret-value-1234567890",
    "WEB_ADMIN_COOKIE_NAME": "bn_admin_session",
    "SYNC_DEFAULT_INTERVAL_SECONDS": 900,
    "ROLE_SNAPSHOT_MAX_AGE_MINUTES": 30,
}


@pytest.mark.asyncio
async def test_clash_api_client_sends_request_with_base_url_and_bearer_token() -> None:
    """Проверяет base URL, private request method и Bearer token."""
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        """Сохраняет request и возвращает тестовый JSON response."""
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json={"ok": True})

    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token=SecretStr("secret-clash-token"),
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    try:
        response = await client._request(
            "get",
            "/clans/%232ABC",
            params={"limit": 1},
        )
    finally:
        await client.aclose()

    assert response.json() == {"ok": True}
    assert captured_request is not None
    assert str(captured_request.url) == "https://api.clashofclans.com/v1/clans/%232ABC?limit=1"
    assert captured_request.method == "GET"
    assert captured_request.headers["accept"] == "application/json"
    assert captured_request.headers["authorization"] == "Bearer secret-clash-token"


@pytest.mark.asyncio
async def test_clash_api_client_from_settings_uses_runtime_configuration() -> None:
    """Проверяет создание клиента из settings без прямого чтения env."""
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        """Сохраняет request и возвращает успешный response."""
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json={"ok": True})

    settings = Settings(**VALID_SETTINGS)

    async with ClashApiClient.from_settings(
        settings,
        transport=httpx.MockTransport(handler),
    ) as client:
        response = await client._request("GET", "players/%232ABC")

    assert response.json() == {"ok": True}
    assert captured_request is not None
    assert str(captured_request.url) == "https://api.clashofclans.com/v1/players/%232ABC"
    assert captured_request.headers["authorization"] == "Bearer test-clash-token-123"
    assert client.is_closed is True


@pytest.mark.asyncio
async def test_clash_api_client_get_player_encodes_player_tag_inside_client() -> None:
    """Проверяет get_player и URL-encoding тега внутри клиента."""
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        """Сохраняет request и возвращает payload игрока."""
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json={"tag": "#2ABC", "name": "Bangkok"})

    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    try:
        payload = await client.get_player("2abc")
    finally:
        await client.aclose()

    assert payload == {"tag": "#2ABC", "name": "Bangkok"}
    assert captured_request is not None
    assert str(captured_request.url) == "https://api.clashofclans.com/v1/players/%232ABC"
    assert captured_request.method == "GET"


@pytest.mark.asyncio
async def test_clash_api_client_verify_player_token_returns_success_result() -> None:
    """Проверяет successful verifytoken как typed result."""
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        """Сохраняет request и возвращает успешный verifytoken response."""
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "tag": "#2ABC",
                "token": "one-time-token",
                "status": "ok",
            },
        )

    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    try:
        result = await client.verify_player_token("#2abc", " one-time-token ")
    finally:
        await client.aclose()

    assert result == VerifyPlayerTokenResult(player_tag="#2ABC", status="ok")
    assert result.is_successful is True
    assert captured_request is not None
    assert str(captured_request.url) == (
        "https://api.clashofclans.com/v1/players/%232ABC/verifytoken"
    )
    assert captured_request.method == "POST"
    assert json.loads(captured_request.content.decode("utf-8")) == {"token": "one-time-token"}


@pytest.mark.asyncio
async def test_clash_api_client_verify_player_token_returns_failed_result() -> None:
    """Проверяет failed verifytoken как контролируемый typed result."""
    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "tag": "#2ABC",
                    "token": "wrong-token",
                    "status": "invalid",
                },
            )
        ),
    )

    try:
        result = await client.verify_player_token("2abc", "wrong-token")
    finally:
        await client.aclose()

    assert result == VerifyPlayerTokenResult(player_tag="#2ABC", status="invalid")
    assert result.is_successful is False


@pytest.mark.asyncio
async def test_clash_api_client_verify_player_token_rejects_empty_token() -> None:
    """Проверяет запрет пустого verifytoken."""
    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True})),
    )

    try:
        with pytest.raises(ValueError):
            await client.verify_player_token("2abc", "   ")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_clash_api_client_get_clan_returns_typed_dto_and_encodes_tag() -> None:
    """Проверяет get_clan, typed DTO и URL-encoding clan tag внутри клиента."""
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        """Сохраняет request и возвращает payload клана."""
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "tag": "#2ABC",
                "name": "Bestiary",
                "clanLevel": 17,
                "members": 44,
                "badgeUrls": {
                    "small": "https://example.test/small.png",
                    "medium": "https://example.test/medium.png",
                    "large": "https://example.test/large.png",
                },
                "description": "ignored",
            },
        )

    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    try:
        clan = await client.get_clan("2abc")
    finally:
        await client.aclose()

    assert clan == ClashClan(
        tag="#2ABC",
        name="Bestiary",
        level=17,
        badge_url="https://example.test/medium.png",
        members_count=44,
    )
    assert captured_request is not None
    assert str(captured_request.url) == "https://api.clashofclans.com/v1/clans/%232ABC"
    assert captured_request.method == "GET"


@pytest.mark.asyncio
async def test_clash_api_client_get_clan_members_returns_typed_dto_and_encodes_tag() -> None:
    """Проверяет get_clan_members, typed DTO и URL-encoding clan tag."""
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        """Сохраняет request и возвращает list-response участников."""
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "tag": "#2ABC",
                        "name": "Bangkok",
                        "role": "leader",
                        "townHallLevel": 16,
                        "expLevel": 233,
                        "trophies": 5200,
                        "donations": 1000,
                        "donationsReceived": 700,
                        "builderBaseTrophies": 4000,
                    },
                    {
                        "tag": "#9XYZ",
                        "name": "Phoenix",
                        "role": "coLeader",
                        "townhallLevel": 15,
                        "expLevel": 180,
                        "trophies": 4800,
                        "donations": 800,
                        "donationsReceived": 600,
                    },
                ]
            },
        )

    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    try:
        members = await client.get_clan_members("#2abc")
    finally:
        await client.aclose()

    assert members == [
        ClashClanMember(
            player_tag="#2ABC",
            name="Bangkok",
            role="leader",
            town_hall_level=16,
            exp_level=233,
            trophies=5200,
            donations=1000,
            donations_received=700,
        ),
        ClashClanMember(
            player_tag="#9XYZ",
            name="Phoenix",
            role="coLeader",
            town_hall_level=15,
            exp_level=180,
            trophies=4800,
            donations=800,
            donations_received=600,
        ),
    ]
    assert captured_request is not None
    assert str(captured_request.url) == "https://api.clashofclans.com/v1/clans/%232ABC/members"
    assert captured_request.method == "GET"


@pytest.mark.asyncio
async def test_clash_api_client_get_current_war_returns_typed_dto_and_encodes_tag() -> None:
    """Проверяет current war endpoint, DTO и URL-encoding clan tag."""
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        """Сохраняет request и возвращает payload текущей войны."""
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "state": "inWar",
                "teamSize": 15,
                "attacksPerMember": 2,
                "preparationStartTime": "20260519T100000.000Z",
                "startTime": "20260519T220000.000Z",
                "endTime": "20260520T220000.000Z",
                "clan": {
                    "tag": "#2ABC",
                    "name": "Bestiary",
                    "stars": 12,
                    "destructionPercentage": 88.5,
                    "attacks": 10,
                },
                "opponent": {
                    "tag": "#9XYZ",
                    "name": "Enemy",
                    "stars": 10,
                    "destructionPercentage": 81,
                    "attacks": 9,
                },
            },
        )

    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    try:
        current_war = await client.get_current_war("#2abc")
    finally:
        await client.aclose()

    assert current_war == ClashCurrentWar(
        state="inWar",
        team_size=15,
        attacks_per_member=2,
        preparation_start_time="20260519T100000.000Z",
        start_time="20260519T220000.000Z",
        end_time="20260520T220000.000Z",
        clan=current_war.clan,
        opponent=current_war.opponent,
    )
    assert current_war.clan is not None
    assert current_war.clan.tag == "#2ABC"
    assert current_war.clan.destruction_percentage == 88.5
    assert current_war.opponent is not None
    assert current_war.opponent.tag == "#9XYZ"
    assert captured_request is not None
    assert str(captured_request.url) == "https://api.clashofclans.com/v1/clans/%232ABC/currentwar"


@pytest.mark.asyncio
async def test_clash_api_client_get_current_war_returns_none_on_404() -> None:
    """Проверяет специальную обработку 404 для отсутствующей текущей войны."""
    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(lambda _: httpx.Response(404, json={"reason": "notFound"})),
    )

    try:
        current_war = await client.get_current_war("2abc")
    finally:
        await client.aclose()

    assert current_war is None


@pytest.mark.asyncio
async def test_clash_api_client_get_war_log_returns_typed_dto() -> None:
    """Проверяет warlog endpoint, paging params и DTO."""
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        """Сохраняет request и возвращает payload журнала войн."""
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "result": "win",
                        "teamSize": 15,
                        "attacksPerMember": 2,
                        "endTime": "20260520T220000.000Z",
                        "clan": {
                            "tag": "#2ABC",
                            "name": "Bestiary",
                            "stars": 30,
                            "destructionPercentage": 98.5,
                            "attacks": 29,
                        },
                        "opponent": {
                            "tag": "#9XYZ",
                            "name": "Enemy",
                            "stars": 24,
                            "destructionPercentage": 91,
                            "attacks": 28,
                        },
                    }
                ]
            },
        )

    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    try:
        war_log = await client.get_war_log("2abc", limit=1, after="cursor")
    finally:
        await client.aclose()

    assert len(war_log) == 1
    assert war_log[0] == ClashWarLogEntry(
        result="win",
        team_size=15,
        attacks_per_member=2,
        end_time="20260520T220000.000Z",
        clan=war_log[0].clan,
        opponent=war_log[0].opponent,
    )
    assert war_log[0].clan is not None
    assert war_log[0].clan.tag == "#2ABC"
    assert captured_request is not None
    assert str(captured_request.url) == (
        "https://api.clashofclans.com/v1/clans/%232ABC/warlog?limit=1&after=cursor"
    )


@pytest.mark.asyncio
async def test_clash_api_client_get_cwl_league_group_returns_typed_dto() -> None:
    """Проверяет CWL league group endpoint и DTO."""
    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "tag": "#GROUP1",
                    "state": "inWar",
                    "season": "2026-05",
                    "clans": [
                        {"tag": "#2ABC", "name": "Bestiary"},
                        {"tag": "#9XYZ", "name": "Enemy"},
                    ],
                    "rounds": [{"warTags": ["#WAR1", "#WAR2"]}],
                },
            )
        ),
    )

    try:
        league_group = await client.get_cwl_league_group("2abc")
    finally:
        await client.aclose()

    assert league_group == ClashCwlLeagueGroup(
        tag="#GROUP1",
        state="inWar",
        season="2026-05",
        clan_tags=("#2ABC", "#9XYZ"),
        rounds=(("#WAR1", "#WAR2"),),
    )


@pytest.mark.asyncio
async def test_clash_api_client_get_cwl_war_returns_typed_dto_and_encodes_war_tag() -> None:
    """Проверяет CWL war endpoint, DTO и URL-encoding war tag."""
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        """Сохраняет request и возвращает payload CWL war."""
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "tag": "#WAR1",
                "state": "warEnded",
                "season": "2026-05",
                "clans": [
                    {"tag": "#2ABC", "name": "Bestiary"},
                    {"tag": "#9XYZ", "name": "Enemy"},
                ],
            },
        )

    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    try:
        cwl_war = await client.get_cwl_war("#war1")
    finally:
        await client.aclose()

    assert cwl_war == ClashCwlWar(
        tag="#WAR1",
        state="warEnded",
        season="2026-05",
        clan_tags=("#2ABC", "#9XYZ"),
        rounds=(),
    )
    assert captured_request is not None
    assert (
        str(captured_request.url) == "https://api.clashofclans.com/v1/clanwarleagues/wars/%23WAR1"
    )


@pytest.mark.asyncio
async def test_clash_api_client_get_capital_raid_seasons_returns_typed_dto() -> None:
    """Проверяет capital raid seasons endpoint и DTO."""
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        """Сохраняет request и возвращает payload рейдового сезона."""
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "state": "ended",
                        "startTime": "20260517T070000.000Z",
                        "endTime": "20260520T070000.000Z",
                        "capitalTotalLoot": 123456,
                        "raidsCompleted": 12,
                        "totalAttacks": 240,
                        "enemyDistrictsDestroyed": 44,
                        "offensiveReward": 1200,
                        "defensiveReward": 500,
                        "members": [
                            {
                                "tag": "#2ABC",
                                "name": "Bangkok",
                                "attacks": 6,
                                "attackLimit": 5,
                                "bonusAttackLimit": 1,
                                "capitalResourcesLooted": 30000,
                            }
                        ],
                    }
                ]
            },
        )

    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    try:
        raid_seasons = await client.get_capital_raid_seasons("#2abc", limit=1)
    finally:
        await client.aclose()

    assert raid_seasons == [
        ClashCapitalRaidSeason(
            state="ended",
            start_time="20260517T070000.000Z",
            end_time="20260520T070000.000Z",
            capital_total_loot=123456,
            raids_completed=12,
            total_attacks=240,
            enemy_districts_destroyed=44,
            offensive_reward=1200,
            defensive_reward=500,
            members=(
                ClashRaidMember(
                    player_tag="#2ABC",
                    name="Bangkok",
                    attacks=6,
                    attack_limit=5,
                    bonus_attack_limit=1,
                    capital_resources_looted=30000,
                ),
            ),
        )
    ]
    assert captured_request is not None
    assert str(captured_request.url) == (
        "https://api.clashofclans.com/v1/clans/%232ABC/capitalraidseasons?limit=1"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_error_class"),
    [
        (400, ClashApiError),
        (403, ClashForbiddenError),
        (404, ClashNotFoundError),
        (429, ClashRateLimitError),
        (500, ClashServerError),
        (503, ClashServerError),
    ],
)
async def test_clash_api_client_maps_error_status_to_typed_exception(
    status_code: int,
    expected_error_class: type[ClashApiError],
) -> None:
    """Проверяет mapping HTTP-статусов в typed Clash API exceptions."""
    response_body = "x" * 3000
    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(lambda _: httpx.Response(status_code, text=response_body)),
    )

    try:
        with pytest.raises(expected_error_class) as exc_info:
            await client._request("GET", "clans/%232ABC")
    finally:
        await client.aclose()

    error = exc_info.value
    assert type(error) is expected_error_class
    assert error.endpoint == "clans/%232ABC"
    assert error.method == "GET"
    assert error.status_code == status_code
    assert error.response_snippet == response_body[:2048]
    assert len(error.response_snippet) == 2048
    assert "secret-clash-token" not in str(error)


@pytest.mark.asyncio
async def test_clash_api_client_maps_timeout_to_typed_exception() -> None:
    """Проверяет mapping timeout в ClashTimeoutError."""

    async def handler(request: httpx.Request) -> httpx.Response:
        """Имитирует timeout transport-уровня."""
        raise httpx.TimeoutException("Request timed out.", request=request)

    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    try:
        with pytest.raises(ClashTimeoutError) as exc_info:
            await client._request("GET", "clans/%232ABC")
    finally:
        await client.aclose()

    error = exc_info.value
    assert error.endpoint == "clans/%232ABC"
    assert error.method == "GET"
    assert error.status_code is None
    assert error.response_snippet is None


@pytest.mark.asyncio
async def test_clash_api_client_closes_connections() -> None:
    """Проверяет закрытие внутреннего HTTP client."""
    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True})),
    )

    assert client.is_closed is False

    await client.aclose()

    assert client.is_closed is True


def test_clash_api_client_repr_does_not_expose_token() -> None:
    """Проверяет, что token не попадает в стандартное представление объекта."""
    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True})),
    )

    assert "secret-clash-token" not in repr(client)


def test_clash_api_client_endpoint_methods_do_not_import_db_layer() -> None:
    """Проверяет, что Clash API client не зависит от DB-layer."""
    client_source = Path(clash_client_module.__file__).read_text(encoding="utf-8")
    dto_source = Path(clash_dto_module.__file__).read_text(encoding="utf-8")
    joined_source = f"{client_source}\n{dto_source}"

    assert "app.db" not in joined_source
