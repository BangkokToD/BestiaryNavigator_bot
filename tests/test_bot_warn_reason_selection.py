"""Тесты выбора причины ручного `/warn`."""

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.bot.routers.warn import (
    WarnFlowStates,
    handle_warn_other_comment,
    handle_warn_reason_choice,
)
from app.db.models import TelegramUser, Warning
from app.domain import WarningReasonCode, WarningSource, WarningStatus
from app.services import WarningCreationResult


@dataclass(slots=True)
class FakeMessage:
    """Fake message для reason-flow тестов."""

    text: str | None = None
    answers: list[dict[str, Any]] = field(default_factory=list)

    async def answer(self, text: str, **kwargs: object) -> None:
        """Фиксирует ответ пользователю.

        Args:
            text: Текст ответа.
            kwargs: Дополнительные параметры отправки.
        """
        self.answers.append({"text": text, **kwargs})


class FakeCallbackQuery:
    """Fake callback query выбора причины."""

    def __init__(self, *, data: str, message: FakeMessage | None = None) -> None:
        """Инициализирует fake callback query.

        Args:
            data: Callback data.
            message: Сообщение, к которому привязана inline-кнопка.
        """
        self.data = data
        self.message = message
        self.answers: list[dict[str, object]] = []

    async def answer(self, text: str, **kwargs: object) -> None:
        """Фиксирует callback answer.

        Args:
            text: Текст callback-ответа.
            kwargs: Дополнительные параметры.
        """
        self.answers.append({"text": text, **kwargs})


class FakeState:
    """Fake FSM context."""

    def __init__(self, data: dict[str, object] | None = None) -> None:
        """Инициализирует fake state.

        Args:
            data: Начальные данные FSM.
        """
        self.data = data or {}
        self.current_state: object | None = WarnFlowStates.waiting_for_reason
        self.clear_count = 0

    async def clear(self) -> None:
        """Очищает fake state."""
        self.data.clear()
        self.current_state = None
        self.clear_count += 1

    async def get_data(self) -> dict[str, object]:
        """Возвращает копию FSM data."""
        return dict(self.data)

    async def update_data(self, **kwargs: object) -> None:
        """Обновляет FSM data."""
        self.data.update(kwargs)

    async def set_state(self, state: object) -> None:
        """Устанавливает FSM state."""
        self.current_state = state


class FakeWarningCreationService:
    """Fake service создания manual warning."""

    def __init__(self) -> None:
        """Инициализирует fake service."""
        self.calls: list[dict[str, object]] = []

    async def create_manual_warning_for_telegram_user_id(
        self,
        *,
        telegram_user_id: int,
        reason_code: WarningReasonCode | str,
        author_telegram_user: TelegramUser | None = None,
        affected_accounts: list[object] | None = None,
        comment: str | None = None,
    ) -> WarningCreationResult:
        """Фиксирует создание manual warning."""
        self.calls.append(
            {
                "telegram_user_id": telegram_user_id,
                "reason_code": reason_code,
                "author_telegram_user": author_telegram_user,
                "affected_accounts": affected_accounts or [],
                "comment": comment,
            }
        )
        warning = Warning(
            telegram_user_id=telegram_user_id,
            source=WarningSource.MANUAL.value,
            status=WarningStatus.ACTIVE.value,
            reason_code=str(reason_code),
            category="discipline",
            is_impactful=False,
            affected_player_tags_json=[account.player_tag for account in affected_accounts or []],
            affected_player_names_json=[account.player_name for account in affected_accounts or []],
            comment=comment,
        )
        return WarningCreationResult(warning=warning, created=True)


@pytest.mark.asyncio
async def test_warn_reason_choice_creates_warning_after_inline_reason() -> None:
    """Проверяет создание warning после inline-выбора причины."""
    state = FakeState(_pending_state_data())
    message = FakeMessage()
    callback_query = FakeCallbackQuery(data="warn_reason:303:toxicity", message=message)
    service = FakeWarningCreationService()
    author = _make_author()

    await handle_warn_reason_choice(
        callback_query,  # type: ignore[arg-type]
        state,  # type: ignore[arg-type]
        author,
        service,
    )

    assert len(service.calls) == 1
    assert service.calls[0]["telegram_user_id"] == 101
    assert service.calls[0]["reason_code"] == WarningReasonCode.TOXICITY
    assert service.calls[0]["author_telegram_user"] is author
    assert service.calls[0]["comment"] is None
    assert service.calls[0]["affected_accounts"][0].player_tag == "#2ABC"
    assert state.clear_count == 1
    assert callback_query.answers == [{"text": "Warn создан."}]
    assert "Warn создан" in message.answers[0]["text"]
    assert "Manual warn не влияет" in message.answers[0]["text"]


@pytest.mark.asyncio
async def test_warn_reason_choice_rejects_foreign_click() -> None:
    """Проверяет, что чужой пользователь не может выбрать причину."""
    state = FakeState(_pending_state_data())
    message = FakeMessage()
    callback_query = FakeCallbackQuery(data="warn_reason:303:spam", message=message)
    service = FakeWarningCreationService()

    await handle_warn_reason_choice(
        callback_query,  # type: ignore[arg-type]
        state,  # type: ignore[arg-type]
        TelegramUser(id=999, telegram_id=999),
        service,
    )

    assert service.calls == []
    assert state.clear_count == 0
    assert callback_query.answers == [
        {
            "text": "Выбрать причину может только автор команды /warn.",
            "show_alert": True,
        }
    ]


@pytest.mark.asyncio
async def test_warn_other_reason_waits_for_required_comment() -> None:
    """Проверяет, что `other` переводит FSM к ожиданию комментария."""
    state = FakeState(_pending_state_data())
    message = FakeMessage()
    callback_query = FakeCallbackQuery(data="warn_reason:303:other", message=message)
    service = FakeWarningCreationService()

    await handle_warn_reason_choice(
        callback_query,  # type: ignore[arg-type]
        state,  # type: ignore[arg-type]
        _make_author(),
        service,
    )

    assert service.calls == []
    assert state.clear_count == 0
    assert state.current_state == WarnFlowStates.waiting_for_other_comment
    assert state.data["warn_reason"] == WarningReasonCode.OTHER.value
    assert "нужен комментарий" in message.answers[0]["text"].lower()


@pytest.mark.asyncio
async def test_warn_other_empty_comment_does_not_create_warning() -> None:
    """Проверяет, что пустой комментарий для `other` не создаёт warning."""
    state = FakeState(
        _pending_state_data()
        | {
            "warn_reason": WarningReasonCode.OTHER.value,
        }
    )
    state.current_state = WarnFlowStates.waiting_for_other_comment
    message = FakeMessage(text="   ")
    service = FakeWarningCreationService()

    await handle_warn_other_comment(
        message,  # type: ignore[arg-type]
        state,  # type: ignore[arg-type]
        _make_author(),
        service,
    )

    assert service.calls == []
    assert state.clear_count == 0
    assert "обязателен" in message.answers[0]["text"]


@pytest.mark.asyncio
async def test_warn_other_comment_creates_warning() -> None:
    """Проверяет создание warning после комментария для `other`."""
    state = FakeState(
        _pending_state_data()
        | {
            "warn_reason": WarningReasonCode.OTHER.value,
        }
    )
    state.current_state = WarnFlowStates.waiting_for_other_comment
    message = FakeMessage(text="Комментарий офицера")
    service = FakeWarningCreationService()
    author = _make_author()

    await handle_warn_other_comment(
        message,  # type: ignore[arg-type]
        state,  # type: ignore[arg-type]
        author,
        service,
    )

    assert len(service.calls) == 1
    assert service.calls[0]["telegram_user_id"] == 101
    assert service.calls[0]["reason_code"] == WarningReasonCode.OTHER
    assert service.calls[0]["comment"] == "Комментарий офицера"
    assert state.clear_count == 1
    assert "Warn создан" in message.answers[0]["text"]


def _pending_state_data() -> dict[str, object]:
    """Возвращает pending target FSM data."""
    return {
        "warn_author_telegram_user_id": 303,
        "warn_target": {
            "telegram_user_id": 101,
            "telegram_id": 42,
            "label": "Bangkok",
            "accounts": [
                {
                    "player_tag": "#2ABC",
                    "player_name": "Bangkok",
                },
                {
                    "player_tag": "#9XYZ",
                    "player_name": "Phoenix",
                },
            ],
        },
    }


def _make_author() -> TelegramUser:
    """Создаёт автора warn-сценария."""
    return TelegramUser(
        id=303,
        telegram_id=777,
        username="officer",
        display_name="Officer",
    )
