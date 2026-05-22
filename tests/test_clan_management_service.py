"""Тесты сервиса управления отслеживаемыми кланами."""

import pytest

from app.db.models import Clan
from app.domain import ClanType, DomainValidationError
from app.integrations.clash import ClashClan, ClashNotFoundError
from app.services import ClanManagementService
from app.services import ClanNotFoundError as ServiceClanNotFoundError


class InMemoryClanRepository:
    """In-memory repository для unit-тестов clan management service."""

    def __init__(self, clans: list[Clan] | None = None) -> None:
        """Инициализирует repository.

        Args:
            clans: Начальный набор моделей кланов.
        """
        self.clans = {clan.tag: clan for clan in clans or []}
        self.added_clans: list[Clan] = []
        self.flush_count = 0

    async def get_by_tag(self, clan_tag: str) -> Clan | None:
        """Возвращает клан по тегу.

        Args:
            clan_tag: Нормализованный тег клана.

        Returns:
            Модель клана или `None`.
        """
        return self.clans.get(clan_tag)

    async def list_all(self) -> tuple[Clan, ...]:
        """Возвращает все кланы из in-memory storage."""
        return tuple(
            sorted(self.clans.values(), key=lambda clan: (not clan.is_active, clan.name, clan.tag))
        )

    def add(self, clan: Clan) -> None:
        """Добавляет клан в in-memory storage.

        Args:
            clan: Модель клана.
        """
        self.added_clans.append(clan)
        self.clans[clan.tag] = clan

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


class FakeClashClanProvider:
    """Fake provider Clash API для unit-тестов."""

    def __init__(self, result: ClashClan | BaseException) -> None:
        """Инициализирует provider.

        Args:
            result: DTO клана или исключение, которое нужно выбросить.
        """
        self.result = result
        self.calls: list[str] = []

    async def get_clan(self, clan_tag: str) -> ClashClan:
        """Возвращает заранее заданный результат.

        Args:
            clan_tag: Тег клана, переданный сервисом.

        Returns:
            DTO клана.

        Raises:
            BaseException: Если fake настроен на ошибку.
        """
        self.calls.append(clan_tag)

        if isinstance(self.result, BaseException):
            raise self.result

        return self.result


@pytest.mark.asyncio
async def test_clan_management_service_lists_clans_without_clash_api_call() -> None:
    """Проверяет чтение списка кланов без обращения к Clash API."""
    active_clan = Clan(
        tag="#2ABC",
        name="Bestiary",
        type=ClanType.MAIN.value,
        is_active=True,
    )
    inactive_clan = Clan(
        tag="#9XYZ",
        name="Archive",
        type=ClanType.FREEZER.value,
        is_active=False,
    )
    repository = InMemoryClanRepository([inactive_clan, active_clan])
    clash_provider = FakeClashClanProvider(
        ClashClan(
            tag="#2ABC",
            name="Bestiary",
            level=17,
            badge_url=None,
            members_count=44,
        )
    )
    service = ClanManagementService(repository=repository, clash_client=clash_provider)

    clans = await service.list_clans()

    assert clans == (active_clan, inactive_clan)
    assert clash_provider.calls == []
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_clan_management_service_checks_clan_without_saving() -> None:
    """Проверяет Clash API check без сохранения клана."""
    repository = InMemoryClanRepository()
    verified_clan = ClashClan(
        tag="#2ABC",
        name="Bestiary",
        level=17,
        badge_url="https://example.test/badge.png",
        members_count=44,
    )
    clash_provider = FakeClashClanProvider(verified_clan)
    service = ClanManagementService(repository=repository, clash_client=clash_provider)

    result = await service.check_clan(clan_tag="2abc")

    assert result == verified_clan
    assert clash_provider.calls == ["2abc"]
    assert repository.clans == {}
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_clan_management_service_adds_clan_after_clash_verification() -> None:
    """Проверяет добавление клана только после проверки через Clash API."""
    repository = InMemoryClanRepository()
    clash_provider = FakeClashClanProvider(
        ClashClan(
            tag="#2ABC",
            name="Bestiary",
            level=17,
            badge_url="https://example.test/badge.png",
            members_count=44,
        )
    )
    service = ClanManagementService(repository=repository, clash_client=clash_provider)

    clan = await service.add_clan(clan_tag="2abc", clan_type="main")

    assert clash_provider.calls == ["2abc"]
    assert repository.added_clans == [clan]
    assert repository.flush_count == 1
    assert repository.clans == {"#2ABC": clan}
    assert clan.tag == "#2ABC"
    assert clan.name == "Bestiary"
    assert clan.type == ClanType.MAIN.value
    assert clan.level == 17
    assert clan.badge_url == "https://example.test/badge.png"
    assert clan.is_active is True
    assert clan.last_sync_at is not None
    assert clan.sync_status == "ok"


@pytest.mark.asyncio
async def test_clan_management_service_does_not_create_clan_if_clash_check_fails() -> None:
    """Проверяет, что без успешной проверки Clash API клан не сохраняется."""
    repository = InMemoryClanRepository()
    clash_provider = FakeClashClanProvider(
        ClashNotFoundError(
            "Clash API returned HTTP 404 for GET clans/%23BAD.",
            endpoint="clans/%23BAD",
            method="GET",
            status_code=404,
            response_snippet=None,
        )
    )
    service = ClanManagementService(repository=repository, clash_client=clash_provider)

    with pytest.raises(ClashNotFoundError):
        await service.add_clan(clan_tag="#bad", clan_type=ClanType.MAIN)

    assert clash_provider.calls == ["#bad"]
    assert repository.clans == {}
    assert repository.added_clans == []
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_clan_management_service_reactivates_existing_inactive_clan() -> None:
    """Проверяет повторное добавление inactive-клана без дубля."""
    existing_clan = Clan(
        tag="#2ABC",
        name="Old name",
        type=ClanType.ACADEMY.value,
        level=1,
        badge_url=None,
        is_active=False,
        sync_status=None,
    )
    repository = InMemoryClanRepository([existing_clan])
    clash_provider = FakeClashClanProvider(
        ClashClan(
            tag="#2ABC",
            name="Bestiary",
            level=18,
            badge_url="https://example.test/new-badge.png",
            members_count=50,
        )
    )
    service = ClanManagementService(repository=repository, clash_client=clash_provider)

    clan = await service.add_clan(clan_tag="2abc", clan_type=ClanType.MAIN)

    assert clan is existing_clan
    assert repository.added_clans == []
    assert len(repository.clans) == 1
    assert repository.flush_count == 1
    assert clan.name == "Bestiary"
    assert clan.type == ClanType.MAIN.value
    assert clan.level == 18
    assert clan.badge_url == "https://example.test/new-badge.png"
    assert clan.is_active is True
    assert clan.sync_status == "ok"


@pytest.mark.asyncio
async def test_clan_management_service_refreshes_existing_clan() -> None:
    """Проверяет обновление имени, уровня и badge из Clash API."""
    existing_clan = Clan(
        tag="#2ABC",
        name="Old name",
        type=ClanType.MAIN.value,
        level=1,
        badge_url=None,
        is_active=True,
        sync_status=None,
    )
    repository = InMemoryClanRepository([existing_clan])
    clash_provider = FakeClashClanProvider(
        ClashClan(
            tag="#2ABC",
            name="Fresh name",
            level=20,
            badge_url="https://example.test/fresh-badge.png",
            members_count=48,
        )
    )
    service = ClanManagementService(repository=repository, clash_client=clash_provider)

    clan = await service.refresh_clan(clan_tag="2abc")

    assert clan is existing_clan
    assert clash_provider.calls == ["#2ABC"]
    assert repository.flush_count == 1
    assert clan.name == "Fresh name"
    assert clan.level == 20
    assert clan.badge_url == "https://example.test/fresh-badge.png"
    assert clan.sync_status == "ok"
    assert clan.last_sync_at is not None


@pytest.mark.asyncio
async def test_clan_management_service_updates_clan_type() -> None:
    """Проверяет смену типа клана через доменный enum."""
    existing_clan = Clan(
        tag="#2ABC",
        name="Bestiary",
        type=ClanType.MAIN.value,
        is_active=True,
    )
    repository = InMemoryClanRepository([existing_clan])
    clash_provider = FakeClashClanProvider(
        ClashClan(
            tag="#2ABC",
            name="Bestiary",
            level=17,
            badge_url=None,
            members_count=44,
        )
    )
    service = ClanManagementService(repository=repository, clash_client=clash_provider)

    clan = await service.update_clan_type(clan_tag="2abc", clan_type="academy")

    assert clan is existing_clan
    assert clan.type == ClanType.ACADEMY.value
    assert repository.flush_count == 1
    assert clash_provider.calls == []


@pytest.mark.asyncio
async def test_clan_management_service_rejects_invalid_clan_type() -> None:
    """Проверяет запрет неизвестного типа клана."""
    existing_clan = Clan(
        tag="#2ABC",
        name="Bestiary",
        type=ClanType.MAIN.value,
        is_active=True,
    )
    repository = InMemoryClanRepository([existing_clan])
    clash_provider = FakeClashClanProvider(
        ClashClan(
            tag="#2ABC",
            name="Bestiary",
            level=17,
            badge_url=None,
            members_count=44,
        )
    )
    service = ClanManagementService(repository=repository, clash_client=clash_provider)

    with pytest.raises(DomainValidationError):
        await service.update_clan_type(clan_tag="2abc", clan_type="unknown")

    assert existing_clan.type == ClanType.MAIN.value
    assert repository.flush_count == 0
    assert clash_provider.calls == []


@pytest.mark.asyncio
async def test_clan_management_service_deactivates_clan() -> None:
    """Проверяет отключение мониторинга без удаления клана."""
    existing_clan = Clan(
        tag="#2ABC",
        name="Bestiary",
        type=ClanType.MAIN.value,
        is_active=True,
    )
    repository = InMemoryClanRepository([existing_clan])
    clash_provider = FakeClashClanProvider(
        ClashClan(
            tag="#2ABC",
            name="Bestiary",
            level=17,
            badge_url=None,
            members_count=44,
        )
    )
    service = ClanManagementService(repository=repository, clash_client=clash_provider)

    clan = await service.deactivate_clan(clan_tag="2abc")

    assert clan is existing_clan
    assert clan.is_active is False
    assert repository.flush_count == 1
    assert clash_provider.calls == []


@pytest.mark.asyncio
async def test_clan_management_service_raises_for_missing_clan() -> None:
    """Проверяет ошибку при действии над отсутствующим кланом."""
    repository = InMemoryClanRepository()
    clash_provider = FakeClashClanProvider(
        ClashClan(
            tag="#2ABC",
            name="Bestiary",
            level=17,
            badge_url=None,
            members_count=44,
        )
    )
    service = ClanManagementService(repository=repository, clash_client=clash_provider)

    with pytest.raises(ServiceClanNotFoundError):
        await service.deactivate_clan(clan_tag="2abc")

    assert repository.flush_count == 0
    assert clash_provider.calls == []
