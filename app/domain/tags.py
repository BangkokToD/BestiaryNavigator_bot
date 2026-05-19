"""Утилиты нормализации и кодирования Clash of Clans тегов."""

import re
from urllib.parse import quote

from app.domain.exceptions import TagValidationError

_TAG_ALLOWED_PATTERN = re.compile(r"^#[A-Z0-9]+$")
_URL_ENCODED_PREFIX = "%23"


def normalize_player_tag(value: str) -> str:
    """Нормализует тег игрока для хранения в БД.

    Args:
        value: Пользовательский ввод тега игрока.

    Returns:
        Нормализованный тег в формате `#ABC123`.

    Raises:
        TagValidationError: Если тег пустой, URL-encoded или содержит недопустимые символы.
    """
    return _normalize_tag(value, entity_name="Тег игрока")


def normalize_clan_tag(value: str) -> str:
    """Нормализует тег клана для хранения в БД.

    Args:
        value: Пользовательский ввод тега клана.

    Returns:
        Нормализованный тег в формате `#ABC123`.

    Raises:
        TagValidationError: Если тег пустой, URL-encoded или содержит недопустимые символы.
    """
    return _normalize_tag(value, entity_name="Тег клана")


def encode_tag_for_clash_url(value: str) -> str:
    """Кодирует нормализованный тег для URL Clash API.

    Args:
        value: Тег игрока или клана.

    Returns:
        URL-encoded тег, например `%232ABC`.

    Raises:
        TagValidationError: Если тег нельзя нормализовать.
    """
    normalized_tag = _normalize_tag(value, entity_name="Тег")
    return quote(normalized_tag, safe="")


def _normalize_tag(value: str, *, entity_name: str) -> str:
    """Нормализует Clash-тег по общим правилам проекта.

    Args:
        value: Сырой пользовательский ввод.
        entity_name: Название сущности для текста ошибки.

    Returns:
        Нормализованный тег.

    Raises:
        TagValidationError: Если значение не может быть безопасно сохранено как DB-value.
    """
    if not isinstance(value, str):
        raise TagValidationError(f"{entity_name} должен быть строкой.")

    raw_tag = value.strip()
    if not raw_tag:
        raise TagValidationError(f"{entity_name} не может быть пустым.")

    if raw_tag.lower().startswith(_URL_ENCODED_PREFIX):
        raise TagValidationError(
            f"{entity_name} не должен быть URL-encoded. Передайте тег в формате #ABC123."
        )

    if any(character.isspace() for character in raw_tag):
        raise TagValidationError(f"{entity_name} не должен содержать пробелы.")

    tag_with_prefix = raw_tag if raw_tag.startswith("#") else f"#{raw_tag}"
    normalized_tag = tag_with_prefix.upper()

    if normalized_tag == "#":
        raise TagValidationError(f"{entity_name} должен содержать символы после #.")

    if not _TAG_ALLOWED_PATTERN.fullmatch(normalized_tag):
        raise TagValidationError(
            f"{entity_name} должен содержать только символ # в начале, латинские буквы A-Z и цифры."
        )

    return normalized_tag
