"""Сервис привязки игровых аккаунтов к Telegram-пользователям."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PlayerAccount, PlayerEvent, TelegramUser
from app.domain import normalize_player_tag
from app.integrations.clash import VerifyPlayerTokenResult

_ACCOUNT_LINKED_EVENT_TYPE = "account_linked"
_VERIFICATION_FAILED_REASON = "verification_failed"


class AccountLinkingError(RuntimeError):
    """Базовая ошибка сервиса привязки игровых аккаунтов."""


@dataclass(frozen=True, slots=True)
class AccountLinkingResult:
    """Результат сценария привязки игрового аккаунта."""

    success: bool
    reason: str | None
    verification_status: str
    player_account: PlayerAccount | None
    event: PlayerEvent | None


class ClashAccountProvider(Protocol):
    """Минимальный contract Clash API client для account linking service."""

    async def verify_player_token(
        self,
        player_tag: str,
        token: str,
    ) -> VerifyPlayerTokenResult:
        """Проверяет one-time API token игрока.

        Args:
            player_tag: Нормализованный тег игрока.
            token: One-time API token из игры.

        Returns:
            Typed result проверки владения аккаунтом.
        """

    async def get_player(self, player_tag: str) -> Mapping[str, object]:
        """Получает профиль игрока.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            JSON object профиля игрока.
        """


class AccountRepository(Protocol):
    """Repository contract для управления `PlayerAccount`."""

    async def get_by_player_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает аккаунт по нормализованному тегу.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            Модель аккаунта или `None`.
        """

    def add(self, player_account: PlayerAccount) -> None:
        """Добавляет аккаунт в unit of work.

        Args:
            player_account: Новая модель аккаунта.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class PlayerEventRepository(Protocol):
    """Repository contract для истории событий игрока."""

    def add(self, player_event: PlayerEvent) -> None:
        """Добавляет событие игрока в unit of work.

        Args:
            player_event: Новая модель события.
        """


class SqlAlchemyAccountRepository:
    """SQLAlchemy-реализация repository для игровых аккаунтов."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_player_tag(self, player_tag: str) -> PlayerAccount | None:
        """Возвращает аккаунт по нормализованному тегу.

        Args:
            player_tag: Нормализованный тег игрока.

        Returns:
            Модель аккаунта или `None`.
        """
        result = await self._session.execute(
            select(PlayerAccount).where(PlayerAccount.player_tag == player_tag)
        )
        return result.scalar_one_or_none()

    def add(self, player_account: PlayerAccount) -> None:
        """Добавляет аккаунт в текущую session.

        Args:
            player_account: Новая модель аккаунта.
        """
        self._session.add(player_account)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class SqlAlchemyPlayerEventRepository:
    """SQLAlchemy-реализация repository для событий игрока."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    def add(self, player_event: PlayerEvent) -> None:
        """Добавляет событие игрока в текущую session.

        Args:
            player_event: Новая модель события.
        """
        self._session.add(player_event)


class AccountLinkingService:
    """Сервис привязки игрового аккаунта к Telegram-пользователю.

    Сервис не хранит one-time API token. Token используется только для вызова
    `verifytoken`, после чего в БД сохраняются только подтверждённый player tag,
    имя игрока, связь с Telegram-пользователем и событие истории.
    """

    def __init__(
        self,
        *,
        account_repository: AccountRepository,
        event_repository: PlayerEventRepository,
        clash_client: ClashAccountProvider,
    ) -> None:
        """Инициализирует service.

        Args:
            account_repository: Repository игровых аккаунтов.
            event_repository: Repository событий игрока.
            clash_client: Клиент Clash API или совместимый provider.
        """
        self._account_repository = account_repository
        self._event_repository = event_repository
        self._clash_client = clash_client

    @classmethod
    def from_session(
        cls,
        *,
        session: AsyncSession,
        clash_client: ClashAccountProvider,
    ) -> "AccountLinkingService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            clash_client: Клиент Clash API или совместимый provider.

        Returns:
            Настроенный service.
        """
        return cls(
            account_repository=SqlAlchemyAccountRepository(session),
            event_repository=SqlAlchemyPlayerEventRepository(session),
            clash_client=clash_client,
        )

    async def link_account(
        self,
        *,
        telegram_user: TelegramUser,
        player_tag: str,
        api_token: str,
    ) -> AccountLinkingResult:
        """Привязывает игровой аккаунт к Telegram-пользователю.

        Args:
            telegram_user: Telegram-пользователь, к которому привязывается аккаунт.
            player_tag: Тег игрового аккаунта.
            api_token: One-time API token из игры. Не сохраняется.

        Returns:
            Результат привязки. При failed verifytoken аккаунт и событие не создаются.

        Raises:
            AccountLinkingError: Если Clash API вернул tag, отличный от запрошенного.
            ValueError: Если профиль игрока не содержит имя.
        """
        normalized_player_tag = normalize_player_tag(player_tag)
        verification_result = await self._clash_client.verify_player_token(
            normalized_player_tag,
            api_token,
        )

        if not verification_result.is_successful:
            return AccountLinkingResult(
                success=False,
                reason=_VERIFICATION_FAILED_REASON,
                verification_status=verification_result.status,
                player_account=None,
                event=None,
            )

        if verification_result.player_tag != normalized_player_tag:
            raise AccountLinkingError(
                "Clash API verifytoken вернул другой player_tag, привязка остановлена."
            )

        player_payload = await self._clash_client.get_player(verification_result.player_tag)
        player_name = _extract_player_name(player_payload)

        account = await self._account_repository.get_by_player_tag(verification_result.player_tag)
        previous_telegram_user_id = _model_id(account) if account is not None else None
        linked_at = _utc_now()

        if account is None:
            account = PlayerAccount(
                telegram_user_id=_model_id(telegram_user),
                player_tag=verification_result.player_tag,
                name=player_name,
                is_active=True,
                linked_at=linked_at,
                unlinked_at=None,
            )
            account.telegram_user = telegram_user
            self._account_repository.add(account)
        else:
            previous_telegram_user_id = account.telegram_user_id
            _apply_account_linkage(
                account,
                telegram_user=telegram_user,
                player_name=player_name,
                linked_at=linked_at,
            )

        event = _build_account_linked_event(
            telegram_user=telegram_user,
            player_account=account,
            previous_telegram_user_id=previous_telegram_user_id,
            created_at=linked_at,
        )
        self._event_repository.add(event)

        await self._account_repository.flush()

        return AccountLinkingResult(
            success=True,
            reason=None,
            verification_status=verification_result.status,
            player_account=account,
            event=event,
        )


def _apply_account_linkage(
    account: PlayerAccount,
    *,
    telegram_user: TelegramUser,
    player_name: str,
    linked_at: datetime,
) -> None:
    """Обновляет связь существующего игрового аккаунта.

    Args:
        account: Существующая модель игрового аккаунта.
        telegram_user: Новый владелец Telegram.
        player_name: Актуальное имя игрока из Clash API.
        linked_at: Время успешной привязки.
    """
    account.telegram_user_id = _model_id(telegram_user)
    account.telegram_user = telegram_user
    account.name = player_name
    account.is_active = True
    account.linked_at = linked_at
    account.unlinked_at = None


def _build_account_linked_event(
    *,
    telegram_user: TelegramUser,
    player_account: PlayerAccount,
    previous_telegram_user_id: int | None,
    created_at: datetime,
) -> PlayerEvent:
    """Создаёт событие истории о привязке аккаунта.

    Args:
        telegram_user: Telegram-пользователь.
        player_account: Привязанный игровой аккаунт.
        previous_telegram_user_id: Предыдущий TelegramUser ID, если был перенос.
        created_at: Время события.

    Returns:
        Модель события игрока.
    """
    return PlayerEvent(
        telegram_user_id=_model_id(telegram_user),
        player_tag=player_account.player_tag,
        event_type=_ACCOUNT_LINKED_EVENT_TYPE,
        title="Игровой аккаунт привязан",
        description=f"Аккаунт {player_account.name} привязан к Telegram-пользователю.",
        metadata_json={
            "player_tag": player_account.player_tag,
            "player_name": player_account.name,
            "telegram_id": telegram_user.telegram_id,
            "previous_telegram_user_id": previous_telegram_user_id,
        },
        created_at=created_at,
    )


def _extract_player_name(payload: Mapping[str, object]) -> str:
    """Извлекает имя игрока из profile payload.

    Args:
        payload: JSON object профиля игрока.

    Returns:
        Непустое имя игрока.

    Raises:
        ValueError: Если поле `name` отсутствует или пустое.
    """
    name = payload.get("name")
    if not isinstance(name, str):
        raise ValueError("Профиль игрока должен содержать строковое поле name.")

    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Профиль игрока содержит пустое имя.")

    return normalized_name


def _model_id(model: object | None) -> int | None:
    """Достаёт DB id из SQLAlchemy model, если он уже назначен.

    Args:
        model: SQLAlchemy model или `None`.

    Returns:
        Положительный DB id или `None`.
    """
    if model is None:
        return None

    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    return None


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "AccountLinkingError",
    "AccountLinkingResult",
    "AccountLinkingService",
    "AccountRepository",
    "ClashAccountProvider",
    "PlayerEventRepository",
    "SqlAlchemyAccountRepository",
    "SqlAlchemyPlayerEventRepository",
]
