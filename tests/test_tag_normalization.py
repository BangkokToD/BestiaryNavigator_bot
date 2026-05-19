"""Тесты нормализации Clash of Clans тегов."""

import pytest

from app.domain import (
    TagValidationError,
    encode_tag_for_clash_url,
    normalize_clan_tag,
    normalize_player_tag,
)


@pytest.mark.parametrize(
    ("raw_tag", "expected_tag"),
    [
        ("2abc", "#2ABC"),
        ("#2abc", "#2ABC"),
        ("  2abc  ", "#2ABC"),
        ("  #2abc  ", "#2ABC"),
        ("#2ABC", "#2ABC"),
        ("2ABC", "#2ABC"),
        ("abc123", "#ABC123"),
        ("#ABC123", "#ABC123"),
    ],
)
def test_normalize_player_tag_accepts_supported_input(raw_tag: str, expected_tag: str) -> None:
    """Проверяет нормализацию тега игрока."""
    assert normalize_player_tag(raw_tag) == expected_tag


@pytest.mark.parametrize(
    ("raw_tag", "expected_tag"),
    [
        ("2abc", "#2ABC"),
        ("#2abc", "#2ABC"),
        ("  2abc  ", "#2ABC"),
        ("  #2abc  ", "#2ABC"),
        ("#2ABC", "#2ABC"),
        ("2ABC", "#2ABC"),
        ("abc123", "#ABC123"),
        ("#ABC123", "#ABC123"),
    ],
)
def test_normalize_clan_tag_accepts_supported_input(raw_tag: str, expected_tag: str) -> None:
    """Проверяет нормализацию тега клана."""
    assert normalize_clan_tag(raw_tag) == expected_tag


@pytest.mark.parametrize(
    "raw_tag",
    [
        "",
        " ",
        "\n",
        "#",
        "2 ABC",
        "#2 ABC",
        "2\tABC",
        "%232ABC",
        "%23ABC123",
        "https://example.com/#2ABC",
        "@2ABC",
        "#2ABC!",
        "#2ABC/",
        "#2ABC?",
        "#2ABC%",
        "№2ABC",
    ],
)
def test_normalize_player_tag_rejects_invalid_input(raw_tag: str) -> None:
    """Проверяет негативные сценарии нормализации тега игрока."""
    with pytest.raises(TagValidationError):
        normalize_player_tag(raw_tag)


@pytest.mark.parametrize(
    "raw_tag",
    [
        "",
        " ",
        "\n",
        "#",
        "2 ABC",
        "#2 ABC",
        "2\tABC",
        "%232ABC",
        "%23ABC123",
        "https://example.com/#2ABC",
        "@2ABC",
        "#2ABC!",
        "#2ABC/",
        "#2ABC?",
        "#2ABC%",
        "№2ABC",
    ],
)
def test_normalize_clan_tag_rejects_invalid_input(raw_tag: str) -> None:
    """Проверяет негативные сценарии нормализации тега клана."""
    with pytest.raises(TagValidationError):
        normalize_clan_tag(raw_tag)


@pytest.mark.parametrize(
    ("raw_tag", "expected_encoded_tag"),
    [
        ("2abc", "%232ABC"),
        ("#2abc", "%232ABC"),
        ("  #2abc  ", "%232ABC"),
        ("ABC123", "%23ABC123"),
        ("#ABC123", "%23ABC123"),
    ],
)
def test_encode_tag_for_clash_url_encodes_normalized_tag(
    raw_tag: str,
    expected_encoded_tag: str,
) -> None:
    """Проверяет URL-encoding нормализованного тега для Clash API."""
    assert encode_tag_for_clash_url(raw_tag) == expected_encoded_tag


def test_url_encoded_tag_is_not_saved_as_database_value() -> None:
    """Проверяет, что URL-encoded тег не принимается как DB-value."""
    with pytest.raises(TagValidationError) as exc_info:
        normalize_player_tag("%232ABC")

    assert "URL-encoded" in str(exc_info.value)


def test_validation_error_message_is_clear_for_empty_tag() -> None:
    """Проверяет понятное сообщение ошибки для пустого тега."""
    with pytest.raises(TagValidationError) as exc_info:
        normalize_clan_tag(" ")

    assert "не может быть пустым" in str(exc_info.value)


def test_validation_error_message_is_clear_for_invalid_characters() -> None:
    """Проверяет понятное сообщение ошибки для недопустимых символов."""
    with pytest.raises(TagValidationError) as exc_info:
        normalize_player_tag("#2ABC!")

    assert "латинские буквы A-Z и цифры" in str(exc_info.value)
