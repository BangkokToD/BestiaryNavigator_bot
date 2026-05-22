"""Тесты команды `/start` Telegram bot."""

from dataclasses import dataclass, field

import pytest

from app.bot.routers.start import create_start_router, handle_start_command


@dataclass(slots=True)
class FakeMessage:
    """Fake message для проверки bot handler-а."""

    answers: list[str] = field(default_factory=list)

    async def answer(self, text: str) -> None:
        """Фиксирует ответ handler-а.

        Args:
            text: Текст ответа.
        """
        self.answers.append(text)


@pytest.mark.asyncio
async def test_start_command_sends_short_user_instruction() -> None:
    """Проверяет пользовательский текст `/start` без технических деталей."""
    message = FakeMessage()

    await handle_start_command(message)  # type: ignore[arg-type]

    assert len(message.answers) == 1

    answer = message.answers[0]
    assert "BestiaryNavigator_bot" in answer
    assert "/link_account" in answer
    assert "API-код" in answer
    assert "Clash of Clans" in answer
    assert "TelegramUser" not in answer
    assert "middleware" not in answer.lower()
    assert "session" not in answer.lower()
    assert "database" not in answer.lower()
    assert "db" not in answer.lower()


def test_create_start_router_registers_private_start_router() -> None:
    """Проверяет модульное создание start-router-а."""
    router = create_start_router()

    assert router.name == "start"
    assert len(router.message.handlers) == 1
