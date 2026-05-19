"""Базовый HTTP-клиент Clash of Clans API."""

from collections.abc import Mapping
from types import TracebackType
from typing import Self

import httpx
from pydantic import SecretStr

from app.core.settings import Settings, get_settings


class ClashApiClient:
    """Async HTTP-клиент Clash of Clans API.

    Клиент отвечает только за интеграционный HTTP-слой: base URL, Bearer token,
    timeout, общий request method и закрытие соединений. Доменные сценарии,
    запись в БД и typed exception mapping добавляются отдельными слоями/коммитами.
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
            httpx.HTTPStatusError: Если API вернул 4xx/5xx. Typed mapping будет
                добавлен отдельным коммитом.
            httpx.HTTPError: Для сетевых ошибок уровня httpx.
        """
        normalized_method = self._normalize_method(method)
        normalized_endpoint = self._normalize_endpoint(endpoint)

        response = await self._http_client.request(
            normalized_method,
            normalized_endpoint,
            headers=self._build_headers(),
            params=params,
            json=json_payload,
        )
        response.raise_for_status()

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
