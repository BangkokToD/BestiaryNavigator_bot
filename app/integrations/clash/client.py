"""Базовый HTTP-клиент Clash of Clans API."""

from collections.abc import Mapping
from types import TracebackType
from typing import Self, cast

import httpx
from pydantic import SecretStr

from app.core.settings import Settings, get_settings
from app.domain.tags import encode_tag_for_clash_url
from app.integrations.clash.dto import (
    ClashCapitalRaidSeason,
    ClashClan,
    ClashClanMember,
    ClashCurrentWar,
    ClashCwlLeagueGroup,
    ClashCwlWar,
    ClashWarLogEntry,
    VerifyPlayerTokenResult,
)
from app.integrations.clash.exceptions import (
    ClashApiError,
    ClashForbiddenError,
    ClashNotFoundError,
    ClashRateLimitError,
    ClashServerError,
    ClashTimeoutError,
)

_RESPONSE_SNIPPET_MAX_LENGTH = 2048
type JsonObject = dict[str, object]
type JsonObjectList = list[JsonObject]


class ClashApiClient:
    """Async HTTP-клиент Clash of Clans API.

    Клиент отвечает только за интеграционный HTTP-слой: base URL, Bearer token,
    timeout, общий request method, typed exceptions и закрытие соединений.
    Доменные сценарии и запись в БД добавляются отдельными слоями/коммитами.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_token: SecretStr | str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Инициализирует Clash API client.

        Args:
            base_url: Базовый URL Clash API.
            api_token: Bearer/JWT token Clash API.
            timeout_seconds: Timeout одного HTTP-запроса.
            transport: Опциональный transport для тестов через `httpx.MockTransport`.

        Raises:
            ValueError: Если base URL, token или timeout невалидны.
        """
        normalized_base_url = self._normalize_base_url(base_url)
        normalized_token = self._extract_token(api_token)
        timeout = self._build_timeout(timeout_seconds)

        self._api_token = normalized_token
        self._http_client = httpx.AsyncClient(
            base_url=normalized_base_url,
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> Self:
        """Создаёт клиент из runtime settings.

        Args:
            settings: Явный экземпляр settings для тестов. Если не передан,
                используется кешированный `get_settings()`.
            transport: Опциональный transport для тестов.

        Returns:
            Настроенный Clash API client.
        """
        loaded_settings = settings or get_settings()
        return cls(
            base_url=str(loaded_settings.clash_api_base_url),
            api_token=loaded_settings.clash_api_token,
            timeout_seconds=loaded_settings.clash_api_timeout_seconds,
            transport=transport,
        )

    @property
    def is_closed(self) -> bool:
        """Проверяет, закрыт ли внутренний HTTP client.

        Returns:
            `True`, если соединения закрыты.
        """
        return self._http_client.is_closed

    async def __aenter__(self) -> Self:
        """Возвращает client для async context manager.

        Returns:
            Текущий экземпляр клиента.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Закрывает HTTP client при выходе из async context manager.

        Args:
            exc_type: Тип исключения, если оно произошло.
            exc_value: Экземпляр исключения, если оно произошло.
            traceback: Traceback исключения, если оно произошло.
        """
        await self.aclose()

    async def aclose(self) -> None:
        """Закрывает HTTP-соединения клиента."""
        await self._http_client.aclose()

    async def get_current_war(self, clan_tag: str) -> ClashCurrentWar | None:
        """Получает текущую войну клана.

        Отсутствие текущей войны может приходить от Clash API как `404`.
        Для этого endpoint такое состояние возвращается как `None`, чтобы
        worker/service-слой мог отличить отсутствие войны от сбоя API.

        Args:
            clan_tag: Тег клана.

        Returns:
            DTO текущей войны или `None`, если активной войны нет.
        """
        encoded_clan_tag = encode_tag_for_clash_url(clan_tag)
        endpoint = f"clans/{encoded_clan_tag}/currentwar"

        try:
            response = await self._request("GET", endpoint)
        except ClashNotFoundError:
            return None

        return ClashCurrentWar.from_payload(self._response_json_object(response))

    async def get_war_log(
        self,
        clan_tag: str,
        *,
        limit: int | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> list[ClashWarLogEntry]:
        """Получает журнал обычных войн клана.

        Args:
            clan_tag: Тег клана.
            limit: Ограничение количества элементов.
            after: Pagination marker `after`.
            before: Pagination marker `before`.

        Returns:
            Список DTO записей журнала войн.
        """
        encoded_clan_tag = encode_tag_for_clash_url(clan_tag)
        response = await self._request(
            "GET",
            f"clans/{encoded_clan_tag}/warlog",
            params=self._build_paging_params(limit=limit, after=after, before=before),
        )

        return [
            ClashWarLogEntry.from_payload(entry_payload)
            for entry_payload in self._response_json_items(response)
        ]

    async def get_cwl_league_group(self, clan_tag: str) -> ClashCwlLeagueGroup:
        """Получает текущую группу ЛВК клана.

        Args:
            clan_tag: Тег клана.

        Returns:
            DTO группы ЛВК.
        """
        encoded_clan_tag = encode_tag_for_clash_url(clan_tag)
        response = await self._request(
            "GET",
            f"clans/{encoded_clan_tag}/currentwar/leaguegroup",
        )

        return ClashCwlLeagueGroup.from_payload(self._response_json_object(response))

    async def get_cwl_war(self, war_tag: str) -> ClashCwlWar:
        """Получает конкретную войну ЛВК по war tag.

        Args:
            war_tag: War tag из CWL API.

        Returns:
            DTO войны ЛВК.
        """
        encoded_war_tag = encode_tag_for_clash_url(war_tag)
        response = await self._request("GET", f"clanwarleagues/wars/{encoded_war_tag}")

        return ClashCwlWar.from_payload(self._response_json_object(response))

    async def get_capital_raid_seasons(
        self,
        clan_tag: str,
        *,
        limit: int | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> list[ClashCapitalRaidSeason]:
        """Получает рейдовые сезоны столицы клана.

        Args:
            clan_tag: Тег клана.
            limit: Ограничение количества элементов.
            after: Pagination marker `after`.
            before: Pagination marker `before`.

        Returns:
            Список DTO рейдовых сезонов.
        """
        encoded_clan_tag = encode_tag_for_clash_url(clan_tag)
        response = await self._request(
            "GET",
            f"clans/{encoded_clan_tag}/capitalraidseasons",
            params=self._build_paging_params(limit=limit, after=after, before=before),
        )

        return [
            ClashCapitalRaidSeason.from_payload(season_payload)
            for season_payload in self._response_json_items(response)
        ]

    async def get_clan(self, clan_tag: str) -> ClashClan:
        """Получает минимальные данные клана по clan tag.

        URL-encoding тега выполняется только внутри клиента. Внешние слои
        передают обычный тег в формате `2ABC` или `#2ABC`.

        Args:
            clan_tag: Тег клана.

        Returns:
            Typed DTO клана с минимально нужными полями.
        """
        encoded_clan_tag = encode_tag_for_clash_url(clan_tag)
        response = await self._request("GET", f"clans/{encoded_clan_tag}")

        return ClashClan.from_payload(self._response_json_object(response))

    async def get_clan_members(self, clan_tag: str) -> list[ClashClanMember]:
        """Получает список участников клана.

        URL-encoding тега выполняется только внутри клиента. Метод поддерживает
        оба распространённых формата list-response: массив и объект с `items`.

        Args:
            clan_tag: Тег клана.

        Returns:
            Список typed DTO участников клана.
        """
        encoded_clan_tag = encode_tag_for_clash_url(clan_tag)
        response = await self._request("GET", f"clans/{encoded_clan_tag}/members")

        return [
            ClashClanMember.from_payload(member_payload)
            for member_payload in self._response_json_items(response)
        ]

    async def get_player(self, player_tag: str) -> JsonObject:
        """Получает профиль игрока по player tag.

        URL-encoding тега выполняется только внутри клиента. Внешние слои
        передают обычный тег в формате `2ABC` или `#2ABC`.

        Args:
            player_tag: Тег игрока.

        Returns:
            JSON-объект профиля игрока.
        """
        encoded_player_tag = encode_tag_for_clash_url(player_tag)
        response = await self._request("GET", f"players/{encoded_player_tag}")

        return self._response_json_object(response)

    async def verify_player_token(
        self,
        player_tag: str,
        token: str,
    ) -> VerifyPlayerTokenResult:
        """Проверяет одноразовый API token игрока через Clash API.

        Failed verifytoken с HTTP 200 и статусом `invalid` возвращается как
        контролируемый typed result. HTTP-ошибки остаются typed exceptions
        уровня Clash API.

        Args:
            player_tag: Тег игрока.
            token: Одноразовый API token из игры.

        Returns:
            Typed result проверки владения аккаунтом.
        """
        encoded_player_tag = encode_tag_for_clash_url(player_tag)
        normalized_token = self._normalize_verify_token(token)
        response = await self._request(
            "POST",
            f"players/{encoded_player_tag}/verifytoken",
            json_payload={"token": normalized_token},
        )

        return VerifyPlayerTokenResult.from_payload(self._response_json_object(response))

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Mapping[str, object] | None = None,
        json_payload: object | None = None,
    ) -> httpx.Response:
        """Выполняет HTTP-запрос к Clash API.

        Все будущие публичные методы клиента должны ходить через этот method,
        чтобы авторизация, timeout и error-handling оставались централизованными.

        Args:
            method: HTTP-метод.
            endpoint: Относительный endpoint без base URL.
            params: Query parameters.
            json_payload: JSON body для POST/PUT/PATCH запросов.

        Returns:
            HTTP response от Clash API.

        Raises:
            ValueError: Если method или endpoint пустые.
            ClashApiError: Если API вернул 4xx/5xx.
            ClashTimeoutError: Если запрос превысил timeout.
        """
        normalized_method = self._normalize_method(method)
        normalized_endpoint = self._normalize_endpoint(endpoint)

        try:
            response = await self._http_client.request(
                normalized_method,
                normalized_endpoint,
                headers=self._build_headers(),
                params=params,
                json=json_payload,
            )
        except httpx.TimeoutException as exc:
            raise ClashTimeoutError(
                "Clash API request timed out.",
                endpoint=normalized_endpoint,
                method=normalized_method,
                status_code=None,
                response_snippet=None,
            ) from exc

        if response.is_error:
            raise self._build_api_error(
                method=normalized_method,
                endpoint=normalized_endpoint,
                response=response,
            )

        return response

    def _build_headers(self) -> dict[str, str]:
        """Собирает HTTP headers без логирования секретного token.

        Returns:
            Headers для Clash API request.
        """
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_token}",
        }

    @classmethod
    def _build_api_error(
        cls,
        *,
        method: str,
        endpoint: str,
        response: httpx.Response,
    ) -> ClashApiError:
        """Создаёт typed exception по HTTP-статусу Clash API.

        Args:
            method: Нормализованный HTTP method.
            endpoint: Нормализованный относительный endpoint.
            response: HTTP response с ошибочным статусом.

        Returns:
            Typed exception, пригодный для будущей записи в `api_errors`.
        """
        status_code = response.status_code
        response_snippet = cls._extract_response_snippet(response)
        message = f"Clash API returned HTTP {status_code} for {method} {endpoint}."

        if status_code == 403:
            error_class: type[ClashApiError] = ClashForbiddenError
        elif status_code == 404:
            error_class = ClashNotFoundError
        elif status_code == 429:
            error_class = ClashRateLimitError
        elif 500 <= status_code <= 599:
            error_class = ClashServerError
        else:
            error_class = ClashApiError

        return error_class(
            message,
            endpoint=endpoint,
            method=method,
            status_code=status_code,
            response_snippet=response_snippet,
        )

    @staticmethod
    def _extract_response_snippet(response: httpx.Response) -> str | None:
        """Извлекает короткий snippet response body без сохранения полного body.

        Args:
            response: HTTP response Clash API.

        Returns:
            Непустой snippet длиной до `_RESPONSE_SNIPPET_MAX_LENGTH` или `None`.
        """
        text = response.text.strip()
        if not text:
            return None

        return text[:_RESPONSE_SNIPPET_MAX_LENGTH]

    @staticmethod
    def _response_json_object(response: httpx.Response) -> JsonObject:
        """Возвращает JSON body как объект.

        Args:
            response: HTTP response Clash API.

        Returns:
            JSON body как словарь.

        Raises:
            ValueError: Если API вернул не JSON object.
        """
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Clash API response должен быть JSON object.")

        return cast(JsonObject, payload)

    @staticmethod
    def _response_json_items(response: httpx.Response) -> JsonObjectList:
        """Возвращает JSON list-response как список объектов.

        Clash API list-endpoints могут возвращать либо массив, либо объект с
        ключом `items`. Метод принимает оба варианта, но элементы списка должны
        быть JSON object.

        Args:
            response: HTTP response Clash API.

        Returns:
            Список JSON-объектов.

        Raises:
            ValueError: Если API вернул неподдерживаемый формат.
        """
        payload = response.json()
        if isinstance(payload, dict):
            payload = payload.get("items")

        if not isinstance(payload, list):
            raise ValueError("Clash API list response должен быть JSON array или object.items.")

        if not all(isinstance(item, dict) for item in payload):
            raise ValueError("Clash API list response должен содержать только JSON objects.")

        return cast(JsonObjectList, payload)

    @staticmethod
    def _build_paging_params(
        *,
        limit: int | None,
        after: str | None,
        before: str | None,
    ) -> dict[str, object] | None:
        """Собирает query params для list-endpoints Clash API.

        Args:
            limit: Ограничение количества элементов.
            after: Pagination marker `after`.
            before: Pagination marker `before`.

        Returns:
            Query params или `None`, если параметры не переданы.

        Raises:
            ValueError: Если limit некорректный или одновременно переданы
                `after` и `before`.
        """
        if after is not None and before is not None:
            raise ValueError("Нельзя одновременно передавать after и before.")

        params: dict[str, object] = {}

        if limit is not None:
            if isinstance(limit, bool) or limit <= 0:
                raise ValueError("limit должен быть положительным целым числом.")
            params["limit"] = limit

        if after is not None:
            normalized_after = after.strip()
            if not normalized_after:
                raise ValueError("after не может быть пустым.")
            params["after"] = normalized_after

        if before is not None:
            normalized_before = before.strip()
            if not normalized_before:
                raise ValueError("before не может быть пустым.")
            params["before"] = normalized_before

        return params or None

    @staticmethod
    def _normalize_base_url(value: str) -> str:
        """Нормализует base URL для `httpx.AsyncClient`.

        Args:
            value: Сырой base URL.

        Returns:
            Base URL с завершающим `/`.

        Raises:
            ValueError: Если URL пустой.
        """
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError("Clash API base URL не может быть пустым.")

        return f"{normalized}/"

    @staticmethod
    def _extract_token(value: SecretStr | str) -> str:
        """Достаёт секретный token без вывода в лог.

        Args:
            value: `SecretStr` из settings или строка для тестов.

        Returns:
            Token без пробелов по краям.

        Raises:
            ValueError: Если token пустой.
        """
        raw_token = value.get_secret_value() if isinstance(value, SecretStr) else value
        normalized_token = raw_token.strip()

        if not normalized_token:
            raise ValueError("Clash API token не может быть пустым.")

        return normalized_token

    @staticmethod
    def _build_timeout(value: float) -> httpx.Timeout:
        """Создаёт явный timeout для HTTP-запросов.

        Args:
            value: Timeout в секундах.

        Returns:
            `httpx.Timeout`.

        Raises:
            ValueError: Если timeout не положительный.
        """
        if value <= 0:
            raise ValueError("Clash API timeout должен быть больше 0.")

        return httpx.Timeout(value)

    @staticmethod
    def _normalize_verify_token(value: str) -> str:
        """Нормализует одноразовый verifytoken без сохранения.

        Args:
            value: Сырой одноразовый token из игры.

        Returns:
            Token без пробелов по краям.

        Raises:
            ValueError: Если token пустой.
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("Verifytoken не может быть пустым.")

        return normalized

    @staticmethod
    def _normalize_method(value: str) -> str:
        """Нормализует HTTP method.

        Args:
            value: Сырой HTTP method.

        Returns:
            Method в uppercase.

        Raises:
            ValueError: Если method пустой.
        """
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("HTTP method не может быть пустым.")

        return normalized

    @staticmethod
    def _normalize_endpoint(value: str) -> str:
        """Нормализует относительный endpoint.

        Args:
            value: Endpoint с ведущим `/` или без него.

        Returns:
            Endpoint без ведущего `/`.

        Raises:
            ValueError: Если endpoint пустой.
        """
        normalized = value.strip().lstrip("/")
        if not normalized:
            raise ValueError("Clash API endpoint не может быть пустым.")

        return normalized
