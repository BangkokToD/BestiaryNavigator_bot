"""Тесты доменных Telegram helpers."""

import pytest

from app.domain import DomainValidationError, normalize_message_thread_id


def test_normalize_message_thread_id_accepts_none() -> None:
    """Проверяет обычный чат без topic/thread."""
    assert normalize_message_thread_id(None) is None


def test_normalize_message_thread_id_accepts_positive_integer() -> None:
    """Проверяет положительный Telegram topic/thread id."""
    assert normalize_message_thread_id(12345) == 12345


@pytest.mark.parametrize("value", [0, -1, True, "123"])
def test_normalize_message_thread_id_rejects_invalid_values(value: object) -> None:
    """Проверяет запрет sentinel, отрицательных и нечисловых значений."""
    with pytest.raises(DomainValidationError):
        normalize_message_thread_id(value)  # type: ignore[arg-type]
