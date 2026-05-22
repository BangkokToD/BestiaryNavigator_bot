"""Security helpers для Telegram Login и admin cookie.

Модуль не зависит от FastAPI routes, БД и шаблонов. Здесь находятся только
чистые функции проверки Telegram Login payload и подписи admin-cookie.
"""

import hmac
import time
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from pydantic import SecretStr

_TELEGRAM_LOGIN_MAX_AUTH_AGE_SECONDS = 24 * 60 * 60


class TelegramLoginVerificationError(ValueError):
    """Ошибка проверки Telegram Login payload."""


@dataclass(frozen=True, slots=True)
class TelegramLoginPayload:
    """Проверенный payload Telegram Login.

    Attributes:
        telegram_id: Telegram ID пользователя.
        auth_date: Unix timestamp авторизации.
        first_name: Имя из Telegram Login payload.
        last_name: Фамилия из Telegram Login payload.
        username: Username без обязательного `@`.
        photo_url: URL аватара, если Telegram его передал.
    """

    telegram_id: int
    auth_date: int
    first_name: str | None
    last_name: str | None
    username: str | None
    photo_url: str | None

    @property
    def display_name(self) -> str | None:
        """Собирает отображаемое имя пользователя.

        Returns:
            Имя из first/last name или username, если имени нет.
        """
        name_parts = [
            part
            for part in (
                self.first_name,
                self.last_name,
            )
            if part
        ]
        if name_parts:
            return " ".join(name_parts)

        return self.username


def verify_telegram_login_payload(
    payload: Mapping[str, object],
    *,
    bot_token: SecretStr | str,
    max_auth_age_seconds: int | None = _TELEGRAM_LOGIN_MAX_AUTH_AGE_SECONDS,
    now_ts: int | None = None,
) -> TelegramLoginPayload:
    """Проверяет подпись Telegram Login payload.

    Алгоритм соответствует Telegram Login Widget: из payload исключается
    `hash`, оставшиеся пары сортируются, склеиваются через `\\n`, затем
    проверяются HMAC-SHA256 с ключом `sha256(bot_token)`.

    Args:
        payload: Query/body поля Telegram Login.
        bot_token: Токен Telegram-бота из settings.
        max_auth_age_seconds: Максимальный возраст payload. `None` отключает
            проверку устаревания.
        now_ts: Текущий Unix timestamp для тестов.

    Returns:
        Проверенный и нормализованный Telegram Login payload.

    Raises:
        TelegramLoginVerificationError: Если подпись, обязательные поля или
            timestamp невалидны.
    """
    if not payload:
        raise TelegramLoginVerificationError("Telegram Login payload пустой.")

    actual_hash = _required_text(payload, "hash").lower()
    signed_fields = _build_signed_fields(payload)
    expected_hash = _build_telegram_login_hash(
        signed_fields,
        bot_token=bot_token,
    )

    if not hmac.compare_digest(actual_hash, expected_hash):
        raise TelegramLoginVerificationError("Подпись Telegram Login payload невалидна.")

    telegram_id = _required_positive_int(payload, "id")
    auth_date = _required_positive_int(payload, "auth_date")
    _validate_auth_date(
        auth_date,
        max_auth_age_seconds=max_auth_age_seconds,
        now_ts=now_ts,
    )

    return TelegramLoginPayload(
        telegram_id=telegram_id,
        auth_date=auth_date,
        first_name=_optional_text(payload.get("first_name")),
        last_name=_optional_text(payload.get("last_name")),
        username=_normalize_username(payload.get("username")),
        photo_url=_optional_text(payload.get("photo_url")),
    )


def build_admin_cookie_value(
    *,
    telegram_id: int,
    secret: SecretStr | str,
) -> str:
    """Создаёт подписанное значение admin-cookie.

    Args:
        telegram_id: Telegram ID администратора.
        secret: `WEB_SESSION_SECRET` из settings.

    Returns:
        Значение cookie в формате `{telegram_id}.{signature}`.

    Raises:
        ValueError: Если Telegram ID или secret невалидны.
    """
    normalized_telegram_id = _validate_positive_int(telegram_id, field_name="telegram_id")
    payload = str(normalized_telegram_id)
    signature = _build_admin_cookie_signature(payload, secret=secret)

    return f"{payload}.{signature}"


def verify_admin_cookie_value(
    value: str,
    *,
    secret: SecretStr | str,
) -> int | None:
    """Проверяет подписанное значение admin-cookie.

    Args:
        value: Значение cookie.
        secret: `WEB_SESSION_SECRET` из settings.

    Returns:
        Telegram ID из cookie или `None`, если cookie невалидна.
    """
    if not isinstance(value, str):
        return None

    payload, separator, signature = value.partition(".")
    if not separator or not payload or not signature:
        return None

    expected_signature = _build_admin_cookie_signature(payload, secret=secret)
    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        return _validate_positive_int(int(payload), field_name="telegram_id")
    except ValueError:
        return None


def _build_telegram_login_hash(
    signed_fields: Mapping[str, str],
    *,
    bot_token: SecretStr | str,
) -> str:
    """Строит ожидаемый hash Telegram Login payload."""
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(signed_fields.items()))
    secret_key = sha256(_secret_value(bot_token, field_name="bot_token").encode("utf-8")).digest()

    return hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        sha256,
    ).hexdigest()


def _build_admin_cookie_signature(payload: str, *, secret: SecretStr | str) -> str:
    """Строит HMAC-подпись admin-cookie payload."""
    normalized_payload = payload.strip()
    if not normalized_payload:
        raise ValueError("Admin cookie payload не может быть пустым.")

    return hmac.new(
        _secret_value(secret, field_name="web_session_secret").encode("utf-8"),
        normalized_payload.encode("utf-8"),
        sha256,
    ).hexdigest()


def _build_signed_fields(payload: Mapping[str, object]) -> dict[str, str]:
    """Возвращает поля, участвующие в Telegram Login подписи."""
    fields: dict[str, str] = {}

    for raw_key, raw_value in payload.items():
        key = str(raw_key).strip()
        if not key or key == "hash" or raw_value is None:
            continue

        fields[key] = str(raw_value)

    if not fields:
        raise TelegramLoginVerificationError("Telegram Login payload не содержит signed fields.")

    return fields


def _validate_auth_date(
    auth_date: int,
    *,
    max_auth_age_seconds: int | None,
    now_ts: int | None,
) -> None:
    """Проверяет свежесть Telegram Login payload."""
    if max_auth_age_seconds is None:
        return

    if isinstance(max_auth_age_seconds, bool) or max_auth_age_seconds <= 0:
        raise TelegramLoginVerificationError(
            "max_auth_age_seconds должен быть положительным числом или None."
        )

    current_ts = int(time.time()) if now_ts is None else now_ts
    if current_ts - auth_date > max_auth_age_seconds:
        raise TelegramLoginVerificationError("Telegram Login payload устарел.")


def _required_text(payload: Mapping[str, object], field_name: str) -> str:
    """Достаёт обязательное текстовое поле."""
    value = payload.get(field_name)
    if value is None:
        raise TelegramLoginVerificationError(f"Поле {field_name} обязательно.")

    normalized = str(value).strip()
    if not normalized:
        raise TelegramLoginVerificationError(f"Поле {field_name} не может быть пустым.")

    return normalized


def _required_positive_int(payload: Mapping[str, object], field_name: str) -> int:
    """Достаёт обязательное положительное целое число."""
    raw_value = _required_text(payload, field_name)

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise TelegramLoginVerificationError(f"Поле {field_name} должно быть числом.") from exc

    try:
        return _validate_positive_int(value, field_name=field_name)
    except ValueError as exc:
        raise TelegramLoginVerificationError(str(exc)) from exc


def _validate_positive_int(value: int, *, field_name: str) -> int:
    """Проверяет положительное целое число."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} должен быть целым числом.")

    if value <= 0:
        raise ValueError(f"{field_name} должен быть положительным числом.")

    return value


def _optional_text(value: object) -> str | None:
    """Нормализует опциональное текстовое значение."""
    if value is None:
        return None

    normalized = str(value).strip()
    return normalized or None


def _normalize_username(value: object) -> str | None:
    """Нормализует Telegram username."""
    normalized = _optional_text(value)
    if normalized is None:
        return None

    return normalized.removeprefix("@") or None


def _secret_value(value: SecretStr | str, *, field_name: str) -> str:
    """Достаёт secret value без логирования."""
    raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
    normalized = raw_value.strip()

    if not normalized:
        raise ValueError(f"{field_name} не может быть пустым.")

    return normalized


__all__ = [
    "TelegramLoginPayload",
    "TelegramLoginVerificationError",
    "build_admin_cookie_value",
    "verify_admin_cookie_value",
    "verify_telegram_login_payload",
]
