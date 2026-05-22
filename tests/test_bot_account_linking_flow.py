"""Тесты bot-сценария `/link_account`."""

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.bot.routers.account_linking import (
    AccountLinkingFlowStates,
    create_account_linking_router,
    handle_api_token_step,
    handle_link_account_cancel,
    handle_link_account_command,
    handle_player_tag_step,
)
from app.db.models import PlayerAccount, TelegramUser
from app.integrations.clash import ClashApiError
from app.services import AccountLinkingResult


@dataclass(slots=True)
class FakeMessage:
    """Fake message для handler-тестов."""

    text: str | None = None
    answers: list[str] = field(default_factory=list)

    async def answer(self, text: str) -> None:
        """Фиксирует ответ handler-а.

        Args:
            text: Текст ответа.
        """
        self.answers.append(text)


class FakeState:
    """Fake FSM context для проверки flow."""

    def __init__(self, data: dict[str, object] | None = None) -> None:
        """Инициализирует fake state.

        Args:
            data: Начальные FSM данные.
        """
        self.data = data or {}
        self.current_state: object | None = None
        self.clear_count = 0

    async def clear(self) -> None:
        """Очищает fake state."""
        self.data.clear()
        self.current_state = None
        self.clear_count += 1

    async def set_state(self, state: object) -> None:
        """Устанавливает fake state.

        Args:
            state: State object.
        """
        self.current_state = state

    async def update_data(self, **kwargs: object) -> None:
        """Обновляет fake state data."""
        self.data.update(kwargs)

    async def get_data(self) -> dict[str, object]:
        """Возвращает fake state data."""
        return dict(self.data)


class FakeAccountLinkingService:
    """Fake AccountLinkingService для bot-flow тестов."""

    def __init__(
        self,
        *,
        result: AccountLinkingResult | None = None,
        error: Exception | None = None,
    ) -> None:
        """Инициализирует fake service.

        Args:
            result: Результат привязки.
            error: Исключение, которое нужно выбросить.
        """
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def link_account(
        self,
        *,
        telegram_user: TelegramUser,
        player_tag: str,
        api_token: str,
    ) -> AccountLinkingResult:
        """Фиксирует вызов привязки."""
        self.calls.append(
            {
                "telegram_user": telegram_user,
                "player_tag": player_tag,
                "api_token": api_token,
            }
        )

        if self.error is not None:
            raise self.error

        if self.result is None:
            raise AssertionError("FakeAccountLinkingService.result не задан.")

        return self.result


@pytest.mark.asyncio
async def test_link_account_command_starts_fsm() -> None:
    """Проверяет старт сценария привязки."""
    message = FakeMessage(text="/link_account")
    state = FakeState()

    await handle_link_account_command(message, state)  # type: ignore[arg-type]

    assert state.clear_count == 1
    assert state.current_state == AccountLinkingFlowStates.waiting_for_player_tag
    assert "отправь тег игрока" in message.answers[0].lower()
    assert "/cancel" in message.answers[0]


@pytest.mark.asyncio
async def test_link_account_player_tag_step_requests_api_token() -> None:
    """Проверяет шаг ввода player tag."""
    message = FakeMessage(text="2abc")
    state = FakeState()

    await handle_player_tag_step(message, state)  # type: ignore[arg-type]

    assert state.data == {"player_tag": "#2ABC"}
    assert state.current_state == AccountLinkingFlowStates.waiting_for_api_token
    assert "API-код" in message.answers[0]


@pytest.mark.asyncio
async def test_link_account_happy_path_clears_state_and_does_not_echo_token() -> None:
    """Проверяет happy path привязки и отсутствие token в ответе."""
    telegram_user = TelegramUser(id=101, telegram_id=42, username="bangkok")
    account = PlayerAccount(
        telegram_user_id=101,
        player_tag="#2ABC",
        name="Bangkok",
        is_active=True,
    )
    service = FakeAccountLinkingService(
        result=AccountLinkingResult(
            success=True,
            reason=None,
            verification_status="ok",
            player_account=account,
            event=None,
        )
    )
    message = FakeMessage(text="secret-api-token")
    state = FakeState(data={"player_tag": "#2ABC"})

    await handle_api_token_step(
        message,  # type: ignore[arg-type]
        state,  # type: ignore[arg-type]
        telegram_user,
        service,
    )

    assert service.calls == [
        {
            "telegram_user": telegram_user,
            "player_tag": "#2ABC",
            "api_token": "secret-api-token",
        }
    ]
    assert state.clear_count == 1
    assert "Аккаунт привязан" in message.answers[0]
    assert "Bangkok" in message.answers[0]
    assert "#2ABC" in message.answers[0]
    assert "secret-api-token" not in message.answers[0]


@pytest.mark.asyncio
async def test_link_account_failed_verifytoken_clears_state() -> None:
    """Проверяет failed verifytoken response и очистку state."""
    telegram_user = TelegramUser(id=101, telegram_id=42, username="bangkok")
    service = FakeAccountLinkingService(
        result=AccountLinkingResult(
            success=False,
            reason="verification_failed",
            verification_status="invalid",
            player_account=None,
            event=None,
        )
    )
    message = FakeMessage(text="wrong-token")
    state = FakeState(data={"player_tag": "#2ABC"})

    await handle_api_token_step(
        message,  # type: ignore[arg-type]
        state,  # type: ignore[arg-type]
        telegram_user,
        service,
    )

    assert state.clear_count == 1
    assert "API-код не подошёл" in message.answers[0]
    assert "wrong-token" not in message.answers[0]


@pytest.mark.asyncio
async def test_link_account_clash_error_clears_state_without_token_echo() -> None:
    """Проверяет понятный ответ при ошибке Clash API."""
    telegram_user = TelegramUser(id=101, telegram_id=42, username="bangkok")
    service = FakeAccountLinkingService(
        error=ClashApiError(
            "boom",
            endpoint="players/%232ABC/verifytoken",
            method="POST",
            status_code=500,
            response_snippet="server error",
        )
    )
    message = FakeMessage(text="secret-api-token")
    state = FakeState(data={"player_tag": "#2ABC"})

    await handle_api_token_step(
        message,  # type: ignore[arg-type]
        state,  # type: ignore[arg-type]
        telegram_user,
        service,
    )

    assert state.clear_count == 1
    assert "Clash API временно" in message.answers[0]
    assert "secret-api-token" not in message.answers[0]


@pytest.mark.asyncio
async def test_link_account_cancel_clears_state() -> None:
    """Проверяет отмену FSM через `/cancel`."""
    message = FakeMessage(text="/cancel")
    state = FakeState(data={"player_tag": "#2ABC"})
    state.current_state = AccountLinkingFlowStates.waiting_for_api_token

    await handle_link_account_cancel(message, state)  # type: ignore[arg-type]

    assert state.clear_count == 1
    assert state.data == {}
    assert state.current_state is None
    assert "отменена" in message.answers[0].lower()


def test_create_account_linking_router_registers_handlers() -> None:
    """Проверяет регистрацию handlers account linking router-а."""
    router = create_account_linking_router()

    assert router.name == "account_linking"
    assert len(router.message.handlers) == 4
