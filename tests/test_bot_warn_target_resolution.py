"""Тесты bot target resolution команды `/warn`."""

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.bot.routers.warn import (
    WarnFlowStates,
    create_warn_router,
    handle_warn_command,
    handle_warn_target_choice,
)
from app.services import (
    WarningTargetAccount,
    WarningTargetCandidate,
    WarningTargetResolution,
)


@dataclass(slots=True)
class FakeTelegramUser:
    """Fake Telegram user."""

    id: int


@dataclass(slots=True)
class FakeReplyMessage:
    """Fake reply message."""

    from_user: FakeTelegramUser | None = None


@dataclass(slots=True)
class FakeMessage:
    """Fake message для warn handler tests."""

    text: str | None = None
    reply_to_message: FakeReplyMessage | None = None
    answers: list[dict[str, Any]] = field(default_factory=list)

    async def answer(self, text: str, **kwargs: object) -> None:
        """Фиксирует answer call."""
        self.answers.append({"text": text, **kwargs})


class FakeCallbackQuery:
    """Fake callback query выбора цели."""

    def __init__(self, *, data: str, message: FakeMessage | None = None) -> None:
        """Инициализирует callback query.

        Args:
            data: Callback data.
            message: Сообщение callback-а.
        """
        self.data = data
        self.message = message
        self.answers: list[dict[str, object]] = []

    async def answer(self, text: str, **kwargs: object) -> None:
        """Фиксирует callback answer."""
        self.answers.append({"text": text, **kwargs})


class FakeState:
    """Fake FSM context."""

    def __init__(self) -> None:
        """Инициализирует state."""
        self.data: dict[str, object] = {}
        self.current_state: object | None = None
        self.clear_count = 0

    async def clear(self) -> None:
        """Очищает state."""
        self.data.clear()
        self.current_state = None
        self.clear_count += 1

    async def update_data(self, **kwargs: object) -> None:
        """Обновляет state data."""
        self.data.update(kwargs)

    async def set_state(self, state: object) -> None:
        """Устанавливает state."""
        self.current_state = state


class FakeWarnTargetResolver:
    """Fake resolver цели warn."""

    def __init__(self, resolution: WarningTargetResolution) -> None:
        """Инициализирует fake resolver."""
        self.resolution = resolution
        self.calls: list[tuple[str, object]] = []

    async def resolve_by_reply_telegram_id(self, telegram_id: int) -> WarningTargetResolution:
        """Фиксирует lookup по reply."""
        self.calls.append(("reply", telegram_id))
        return self.resolution

    async def resolve_by_player_tag(self, player_tag: str) -> WarningTargetResolution:
        """Фиксирует lookup по player tag."""
        self.calls.append(("tag", player_tag))
        return self.resolution

    async def resolve_by_username(self, username: str) -> WarningTargetResolution:
        """Фиксирует lookup по username."""
        self.calls.append(("username", username))
        return self.resolution

    async def resolve_by_telegram_user_id(self, telegram_user_id: int) -> WarningTargetResolution:
        """Фиксирует lookup по TelegramUser DB ID."""
        self.calls.append(("telegram_user_id", telegram_user_id))
        return self.resolution


@pytest.mark.asyncio
async def test_warn_command_resolves_reply_target_first() -> None:
    """Проверяет приоритет reply перед тегом и username."""
    candidate = _make_candidate()
    resolver = FakeWarnTargetResolver(WarningTargetResolution.resolved(candidate))
    state = FakeState()
    message = FakeMessage(
        text="/warn #9XYZ @someone",
        reply_to_message=FakeReplyMessage(from_user=FakeTelegramUser(id=777)),
    )

    await handle_warn_command(message, state, resolver)  # type: ignore[arg-type]

    assert resolver.calls == [("reply", 777)]
    assert state.current_state == WarnFlowStates.waiting_for_reason
    assert state.data["warn_target"]["telegram_user_id"] == 101
    assert "Цель найдена" in message.answers[0]["text"]


@pytest.mark.asyncio
async def test_warn_command_resolves_player_tag() -> None:
    """Проверяет поиск по #PLAYER_TAG."""
    candidate = _make_candidate()
    resolver = FakeWarnTargetResolver(WarningTargetResolution.resolved(candidate))
    state = FakeState()
    message = FakeMessage(text="/warn #2ABC")

    await handle_warn_command(message, state, resolver)  # type: ignore[arg-type]

    assert resolver.calls == [("tag", "#2ABC")]
    assert state.current_state == WarnFlowStates.waiting_for_reason
    assert "Bangkok #2ABC" in message.answers[0]["text"]


@pytest.mark.asyncio
async def test_warn_command_resolves_username_ambiguity_with_inline_buttons() -> None:
    """Проверяет ambiguity handling через inline-кнопки."""
    resolver = FakeWarnTargetResolver(
        WarningTargetResolution.ambiguous(
            (
                _make_candidate(telegram_user_id=101, player_name="Bangkok"),
                _make_candidate(telegram_user_id=202, player_name="Phoenix"),
            )
        )
    )
    state = FakeState()
    message = FakeMessage(text="/warn @same")

    await handle_warn_command(message, state, resolver)  # type: ignore[arg-type]

    assert resolver.calls == [("username", "@same")]
    assert state.current_state is None
    assert "несколько целей" in message.answers[0]["text"]
    assert message.answers[0]["reply_markup"] is not None


@pytest.mark.asyncio
async def test_warn_command_rejects_unlinked_account() -> None:
    """Проверяет отказ для непривязанного аккаунта."""
    resolver = FakeWarnTargetResolver(WarningTargetResolution.unlinked(player_tag="#2ABC"))
    state = FakeState()
    message = FakeMessage(text="/warn #2ABC")

    await handle_warn_command(message, state, resolver)  # type: ignore[arg-type]

    assert resolver.calls == [("tag", "#2ABC")]
    assert state.current_state is None
    assert "не привязан" in message.answers[0]["text"]
    assert "Warn не создан" in message.answers[0]["text"]


@pytest.mark.asyncio
async def test_warn_target_choice_saves_pending_target() -> None:
    """Проверяет выбор цели из inline-кнопки."""
    candidate = _make_candidate()
    resolver = FakeWarnTargetResolver(WarningTargetResolution.resolved(candidate))
    state = FakeState()
    message = FakeMessage()
    callback_query = FakeCallbackQuery(data="warn_target:101", message=message)

    await handle_warn_target_choice(callback_query, state, resolver)  # type: ignore[arg-type]

    assert resolver.calls == [("telegram_user_id", 101)]
    assert callback_query.answers == [{"text": "Цель выбрана."}]
    assert state.current_state == WarnFlowStates.waiting_for_reason
    assert state.data["warn_target"]["telegram_user_id"] == 101
    assert "Цель найдена" in message.answers[0]["text"]


def test_create_warn_router_registers_handlers() -> None:
    """Проверяет регистрацию handlers warn router-а."""
    router = create_warn_router()

    assert router.name == "warn"
    assert len(router.message.handlers) == 1
    assert len(router.callback_query.handlers) == 1


def _make_candidate(
    *,
    telegram_user_id: int = 101,
    player_name: str = "Bangkok",
) -> WarningTargetCandidate:
    """Создаёт candidate цели warn."""
    return WarningTargetCandidate(
        telegram_user_id=telegram_user_id,
        telegram_id=42,
        username="bangkok",
        display_name="Bangkok",
        accounts=(
            WarningTargetAccount(
                player_tag="#2ABC",
                player_name=player_name,
            ),
        ),
    )
