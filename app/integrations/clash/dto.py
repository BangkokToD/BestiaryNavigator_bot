"""DTO и typed results Clash API."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

from app.domain.tags import normalize_clan_tag, normalize_player_tag


@dataclass(frozen=True, slots=True)
class ClashClan:
    """Минимальные данные клана из Clash API.

    DTO намеренно содержит только поля, которые нужны ближайшим слоям:
    проверка клана перед сохранением, обновление имени, уровня, badge и
    количества участников.
    """

    tag: str
    name: str
    level: int | None
    badge_url: str | None
    members_count: int | None

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        """Создаёт DTO клана из JSON payload.

        Args:
            payload: JSON object ответа `GET /clans/{clanTag}`.

        Returns:
            DTO клана с нормализованным тегом.

        Raises:
            ValueError: Если обязательные поля отсутствуют или имеют неверный тип.
        """
        return cls(
            tag=normalize_clan_tag(_required_str_field(payload, "tag")),
            name=_required_str_field(payload, "name"),
            level=_optional_int_field(payload, "clanLevel"),
            badge_url=_select_badge_url(payload),
            members_count=_optional_int_field(payload, "members"),
        )


@dataclass(frozen=True, slots=True)
class ClashClanMember:
    """Минимальные данные участника клана из Clash API."""

    player_tag: str
    name: str
    role: str | None
    town_hall_level: int | None
    exp_level: int | None
    trophies: int | None
    donations: int | None
    donations_received: int | None

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        """Создаёт DTO участника клана из JSON payload.

        Args:
            payload: JSON object элемента `GET /clans/{clanTag}/members`.

        Returns:
            DTO участника с нормализованным player tag.

        Raises:
            ValueError: Если обязательные поля отсутствуют или имеют неверный тип.
        """
        town_hall_level = _optional_int_field(payload, "townHallLevel")
        if town_hall_level is None:
            town_hall_level = _optional_int_field(payload, "townhallLevel")

        return cls(
            player_tag=normalize_player_tag(_required_str_field(payload, "tag")),
            name=_required_str_field(payload, "name"),
            role=_optional_str_field(payload, "role"),
            town_hall_level=town_hall_level,
            exp_level=_optional_int_field(payload, "expLevel"),
            trophies=_optional_int_field(payload, "trophies"),
            donations=_optional_int_field(payload, "donations"),
            donations_received=_optional_int_field(payload, "donationsReceived"),
        )


@dataclass(frozen=True, slots=True)
class VerifyPlayerTokenResult:
    """Результат проверки владения игровым аккаунтом.

    Поле `token` из ответа Clash API намеренно не сохраняется в result: для
    доменного сценария достаточно тега и статуса, а одноразовый token не должен
    жить дольше проверки.
    """

    player_tag: str
    status: str

    @property
    def is_successful(self) -> bool:
        """Проверяет успешность verifytoken.

        Returns:
            `True`, если Clash API вернул статус `ok`.
        """
        return self.status == "ok"

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        """Создаёт result из JSON payload Clash API.

        Args:
            payload: JSON object ответа `verifytoken`.

        Returns:
            Typed result проверки владения аккаунтом.

        Raises:
            ValueError: Если payload не содержит обязательных строковых полей.
        """
        raw_tag = payload.get("tag")
        raw_status = payload.get("status")

        if not isinstance(raw_tag, str):
            raise ValueError("Verifytoken response должен содержать строковое поле tag.")

        if not isinstance(raw_status, str):
            raise ValueError("Verifytoken response должен содержать строковое поле status.")

        normalized_status = raw_status.strip().lower()
        if not normalized_status:
            raise ValueError("Verifytoken response содержит пустой status.")

        return cls(
            player_tag=normalize_player_tag(raw_tag),
            status=normalized_status,
        )


def _required_str_field(payload: Mapping[str, object], field_name: str) -> str:
    """Достаёт обязательное строковое поле.

    Args:
        payload: JSON object.
        field_name: Название поля.

    Returns:
        Непустая строка без пробелов по краям.

    Raises:
        ValueError: Если поле отсутствует, пустое или не строковое.
    """
    value = payload.get(field_name)
    if not isinstance(value, str):
        raise ValueError(f"Clash API response должен содержать строковое поле {field_name}.")

    normalized = value.strip()
    if not normalized:
        raise ValueError(f"Clash API response содержит пустое поле {field_name}.")

    return normalized


def _optional_str_field(payload: Mapping[str, object], field_name: str) -> str | None:
    """Достаёт опциональное строковое поле."""
    value = payload.get(field_name)
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValueError(f"Clash API response поле {field_name} должно быть строкой.")

    normalized = value.strip()
    return normalized or None


def _optional_int_field(payload: Mapping[str, object], field_name: str) -> int | None:
    """Достаёт опциональное целочисленное поле."""
    value = payload.get(field_name)
    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Clash API response поле {field_name} должно быть целым числом.")

    return value


def _select_badge_url(payload: Mapping[str, object]) -> str | None:
    """Выбирает URL badge клана по приоритету качества."""
    badge_urls = payload.get("badgeUrls")
    if not isinstance(badge_urls, Mapping):
        return None

    for key in ("medium", "large", "small"):
        value = badge_urls.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


__all__ = [
    "ClashClan",
    "ClashClanMember",
    "VerifyPlayerTokenResult",
]
