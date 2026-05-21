"""DTO и typed results Clash API."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Self, cast

from app.domain.tags import normalize_clan_tag, normalize_player_tag


@dataclass(frozen=True, slots=True)
class ClashWarAttack:
    """Атака в обычной войне из Clash API."""

    order: int | None
    attacker_tag: str
    defender_tag: str
    stars: int | None
    destruction_percentage: float | None
    duration: int | None

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        """Создаёт DTO атаки войны из JSON payload.

        Args:
            payload: JSON object атаки.

        Returns:
            DTO атаки войны.
        """
        return cls(
            order=_optional_int_field(payload, "order"),
            attacker_tag=normalize_player_tag(_required_str_field(payload, "attackerTag")),
            defender_tag=normalize_player_tag(_required_str_field(payload, "defenderTag")),
            stars=_optional_int_field(payload, "stars"),
            destruction_percentage=_optional_float_field(payload, "destructionPercentage"),
            duration=_optional_int_field(payload, "duration"),
        )


@dataclass(frozen=True, slots=True)
class ClashWarMember:
    """Участник обычной войны из Clash API."""

    player_tag: str
    name: str
    town_hall_level: int | None
    map_position: int | None
    attacks: tuple[ClashWarAttack, ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        """Создаёт DTO участника войны из JSON payload.

        Args:
            payload: JSON object участника войны.

        Returns:
            DTO участника войны.
        """
        town_hall_level = _optional_int_field(payload, "townHallLevel")
        if town_hall_level is None:
            town_hall_level = _optional_int_field(payload, "townhallLevel")

        return cls(
            player_tag=normalize_player_tag(_required_str_field(payload, "tag")),
            name=_required_str_field(payload, "name"),
            town_hall_level=town_hall_level,
            map_position=_optional_int_field(payload, "mapPosition"),
            attacks=_extract_war_attacks(payload),
        )


@dataclass(frozen=True, slots=True)
class ClashWarSideSummary:
    """Краткая сводка стороны обычной войны."""

    tag: str | None
    name: str | None
    stars: int | None
    destruction_percentage: float | None
    attacks: int | None
    members: tuple[ClashWarMember, ...] = ()

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        """Создаёт сводку стороны войны из JSON payload."""
        return cls(
            tag=_optional_normalized_tag(payload, "tag", tag_kind="clan"),
            name=_optional_str_field(payload, "name"),
            stars=_optional_int_field(payload, "stars"),
            destruction_percentage=_optional_float_field(payload, "destructionPercentage"),
            attacks=_optional_int_field(payload, "attacks"),
            members=_extract_war_members(payload),
        )


@dataclass(frozen=True, slots=True)
class ClashCurrentWar:
    """Минимальные данные текущей обычной войны."""

    state: str
    team_size: int | None
    attacks_per_member: int | None
    preparation_start_time: str | None
    start_time: str | None
    end_time: str | None
    clan: ClashWarSideSummary | None
    opponent: ClashWarSideSummary | None

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        """Создаёт DTO текущей войны из JSON payload."""
        return cls(
            state=_required_str_field(payload, "state"),
            team_size=_optional_int_field(payload, "teamSize"),
            attacks_per_member=_optional_int_field(payload, "attacksPerMember"),
            preparation_start_time=_optional_str_field(payload, "preparationStartTime"),
            start_time=_optional_str_field(payload, "startTime"),
            end_time=_optional_str_field(payload, "endTime"),
            clan=_optional_war_side(payload, "clan"),
            opponent=_optional_war_side(payload, "opponent"),
        )


@dataclass(frozen=True, slots=True)
class ClashWarLogEntry:
    """Минимальная запись журнала обычных войн."""

    result: str | None
    team_size: int | None
    attacks_per_member: int | None
    end_time: str | None
    clan: ClashWarSideSummary | None
    opponent: ClashWarSideSummary | None

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        """Создаёт DTO записи warlog из JSON payload."""
        return cls(
            result=_optional_str_field(payload, "result"),
            team_size=_optional_int_field(payload, "teamSize"),
            attacks_per_member=_optional_int_field(payload, "attacksPerMember"),
            end_time=_optional_str_field(payload, "endTime"),
            clan=_optional_war_side(payload, "clan"),
            opponent=_optional_war_side(payload, "opponent"),
        )


@dataclass(frozen=True, slots=True)
class ClashCwlLeagueGroup:
    """Минимальные данные текущей группы ЛВК."""

    tag: str | None
    state: str
    season: str
    clan_tags: tuple[str, ...]
    rounds: tuple[tuple[str, ...], ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        """Создаёт DTO группы ЛВК из JSON payload."""
        return cls(
            tag=_optional_normalized_tag(payload, "tag", tag_kind="clan"),
            state=_required_str_field(payload, "state"),
            season=_required_str_field(payload, "season"),
            clan_tags=_extract_clan_tags(payload),
            rounds=_extract_rounds(payload),
        )


@dataclass(frozen=True, slots=True)
class ClashCwlWar:
    """Минимальные данные конкретной войны ЛВК."""

    tag: str | None
    state: str
    season: str | None
    clan_tags: tuple[str, ...]
    rounds: tuple[tuple[str, ...], ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        """Создаёт DTO войны ЛВК из JSON payload."""
        return cls(
            tag=_optional_normalized_tag(payload, "tag", tag_kind="clan"),
            state=_required_str_field(payload, "state"),
            season=_optional_str_field(payload, "season"),
            clan_tags=_extract_clan_tags(payload),
            rounds=_extract_rounds(payload),
        )


@dataclass(frozen=True, slots=True)
class ClashRaidMember:
    """Минимальные данные участника рейдового сезона."""

    player_tag: str
    name: str
    attacks: int | None
    attack_limit: int | None
    bonus_attack_limit: int | None
    capital_resources_looted: int | None

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        """Создаёт DTO участника рейдового сезона из JSON payload."""
        return cls(
            player_tag=normalize_player_tag(_required_str_field(payload, "tag")),
            name=_required_str_field(payload, "name"),
            attacks=_optional_int_field(payload, "attacks"),
            attack_limit=_optional_int_field(payload, "attackLimit"),
            bonus_attack_limit=_optional_int_field(payload, "bonusAttackLimit"),
            capital_resources_looted=_optional_int_field(payload, "capitalResourcesLooted"),
        )


@dataclass(frozen=True, slots=True)
class ClashCapitalRaidSeason:
    """Минимальные данные рейдового сезона столицы клана."""

    state: str
    start_time: str | None
    end_time: str | None
    capital_total_loot: int | None
    raids_completed: int | None
    total_attacks: int | None
    enemy_districts_destroyed: int | None
    offensive_reward: int | None
    defensive_reward: int | None
    members: tuple[ClashRaidMember, ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        """Создаёт DTO рейдового сезона из JSON payload."""
        return cls(
            state=_required_str_field(payload, "state"),
            start_time=_optional_str_field(payload, "startTime"),
            end_time=_optional_str_field(payload, "endTime"),
            capital_total_loot=_optional_int_field(payload, "capitalTotalLoot"),
            raids_completed=_optional_int_field(payload, "raidsCompleted"),
            total_attacks=_optional_int_field(payload, "totalAttacks"),
            enemy_districts_destroyed=_optional_int_field(payload, "enemyDistrictsDestroyed"),
            offensive_reward=_optional_int_field(payload, "offensiveReward"),
            defensive_reward=_optional_int_field(payload, "defensiveReward"),
            members=_extract_raid_members(payload),
        )


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


def _optional_float_field(payload: Mapping[str, object], field_name: str) -> float | None:
    """Достаёт опциональное числовое поле как float."""
    value = payload.get(field_name)
    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Clash API response поле {field_name} должно быть числом.")

    return float(value)


def _optional_mapping_field(
    payload: Mapping[str, object],
    field_name: str,
) -> Mapping[str, object] | None:
    """Достаёт опциональное поле-объект."""
    value = payload.get(field_name)
    if value is None:
        return None

    if not isinstance(value, Mapping):
        raise ValueError(f"Clash API response поле {field_name} должно быть объектом.")

    return cast(Mapping[str, object], value)


def _optional_list_field(
    payload: Mapping[str, object],
    field_name: str,
) -> Sequence[object]:
    """Достаёт опциональное поле-список."""
    value = payload.get(field_name)
    if value is None:
        return ()

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"Clash API response поле {field_name} должно быть списком.")

    return value


def _optional_normalized_tag(
    payload: Mapping[str, object],
    field_name: str,
    *,
    tag_kind: str,
) -> str | None:
    """Достаёт и нормализует опциональный тег."""
    value = _optional_str_field(payload, field_name)
    if value is None:
        return None

    if tag_kind == "player":
        return normalize_player_tag(value)

    return normalize_clan_tag(value)


def _optional_war_side(
    payload: Mapping[str, object],
    field_name: str,
) -> ClashWarSideSummary | None:
    """Достаёт краткую сводку стороны войны."""
    side_payload = _optional_mapping_field(payload, field_name)
    if side_payload is None:
        return None

    return ClashWarSideSummary.from_payload(side_payload)


def _extract_war_members(payload: Mapping[str, object]) -> tuple[ClashWarMember, ...]:
    """Извлекает участников обычной войны."""
    members: list[ClashWarMember] = []

    for item in _optional_list_field(payload, "members"):
        if not isinstance(item, Mapping):
            raise ValueError("Clash API response поле members должно содержать объекты.")

        members.append(ClashWarMember.from_payload(cast(Mapping[str, object], item)))

    return tuple(members)


def _extract_war_attacks(payload: Mapping[str, object]) -> tuple[ClashWarAttack, ...]:
    """Извлекает атаки участника обычной войны."""
    attacks: list[ClashWarAttack] = []

    for item in _optional_list_field(payload, "attacks"):
        if not isinstance(item, Mapping):
            raise ValueError("Clash API response поле attacks должно содержать объекты.")

        attacks.append(ClashWarAttack.from_payload(cast(Mapping[str, object], item)))

    return tuple(attacks)


def _extract_clan_tags(payload: Mapping[str, object]) -> tuple[str, ...]:
    """Извлекает теги кланов из CWL payload."""
    clan_tags: list[str] = []

    for item in _optional_list_field(payload, "clans"):
        if not isinstance(item, Mapping):
            raise ValueError("Clash API response поле clans должно содержать объекты.")

        item_payload = cast(Mapping[str, object], item)
        clan_tags.append(normalize_clan_tag(_required_str_field(item_payload, "tag")))

    return tuple(clan_tags)


def _extract_rounds(payload: Mapping[str, object]) -> tuple[tuple[str, ...], ...]:
    """Извлекает warTags из раундов ЛВК."""
    rounds: list[tuple[str, ...]] = []

    for round_item in _optional_list_field(payload, "rounds"):
        if not isinstance(round_item, Mapping):
            raise ValueError("Clash API response поле rounds должно содержать объекты.")

        round_payload = cast(Mapping[str, object], round_item)
        war_tags = []

        for war_tag in _optional_list_field(round_payload, "warTags"):
            if not isinstance(war_tag, str):
                raise ValueError("Clash API response поле warTags должно содержать строки.")
            war_tags.append(normalize_clan_tag(war_tag))

        rounds.append(tuple(war_tags))

    return tuple(rounds)


def _extract_raid_members(payload: Mapping[str, object]) -> tuple[ClashRaidMember, ...]:
    """Извлекает участников рейдового сезона."""
    members: list[ClashRaidMember] = []

    for item in _optional_list_field(payload, "members"):
        if not isinstance(item, Mapping):
            raise ValueError("Clash API response поле members должно содержать объекты.")

        members.append(ClashRaidMember.from_payload(cast(Mapping[str, object], item)))

    return tuple(members)


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
    "ClashCapitalRaidSeason",
    "ClashClan",
    "ClashClanMember",
    "ClashCurrentWar",
    "ClashCwlLeagueGroup",
    "ClashCwlWar",
    "ClashRaidMember",
    "ClashWarAttack",
    "ClashWarLogEntry",
    "ClashWarMember",
    "ClashWarSideSummary",
    "VerifyPlayerTokenResult",
]
