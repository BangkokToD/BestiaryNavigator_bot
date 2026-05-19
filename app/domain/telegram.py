"""Доменные helpers для Telegram-значений."""

from app.domain.exceptions import DomainValidationError


def normalize_message_thread_id(value: int | None) -> int | None:
    """Проверяет Telegram message thread id.

    `None` означает обычный чат без topic. Значение `0` запрещено, потому что
    оно используется как sentinel в PostgreSQL expression index для
    дедупликации маршрутов с `NULL message_thread_id`.

    Args:
        value: Telegram topic/thread id или `None`.

    Returns:
        Проверенное значение без преобразования.

    Raises:
        DomainValidationError: Если значение не является положительным int или `None`.
    """
    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainValidationError("message_thread_id должен быть положительным числом или None.")

    if value <= 0:
        raise DomainValidationError("message_thread_id должен быть больше 0 или None.")

    return value
