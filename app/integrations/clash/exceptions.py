"""Typed exceptions Clash of Clans API."""


class ClashApiError(RuntimeError):
    """Базовая ошибка интеграции с Clash API.

    Исключение хранит только короткий snippet ответа, а не полный response body.
    Этого достаточно для будущей записи в `api_errors` без утечки лишних данных.
    """

    def __init__(
        self,
        message: str,
        *,
        endpoint: str,
        method: str,
        status_code: int | None,
        response_snippet: str | None,
    ) -> None:
        """Инициализирует ошибку Clash API.

        Args:
            message: Краткое сообщение об ошибке.
            endpoint: Относительный endpoint Clash API.
            method: HTTP method.
            status_code: HTTP status code или `None` для сетевых ошибок.
            response_snippet: Короткий snippet response body или `None`.
        """
        super().__init__(message)
        self.endpoint = endpoint
        self.method = method
        self.status_code = status_code
        self.response_snippet = response_snippet


class ClashForbiddenError(ClashApiError):
    """Ошибка 403 от Clash API."""


class ClashNotFoundError(ClashApiError):
    """Ошибка 404 от Clash API."""


class ClashRateLimitError(ClashApiError):
    """Ошибка 429 от Clash API."""


class ClashServerError(ClashApiError):
    """Ошибка 5xx от Clash API."""


class ClashTimeoutError(ClashApiError):
    """Timeout при запросе к Clash API."""


__all__ = [
    "ClashApiError",
    "ClashForbiddenError",
    "ClashNotFoundError",
    "ClashRateLimitError",
    "ClashServerError",
    "ClashTimeoutError",
]
