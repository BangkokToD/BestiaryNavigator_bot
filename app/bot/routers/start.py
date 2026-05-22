"""Router команды `/start` Telegram bot."""

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

_START_MESSAGE = "\n".join(
    (
        "Привет. Это BestiaryNavigator_bot.",
        "",
        "Бот помогает связать твой Telegram с аккаунтом Clash of Clans и участвовать "
        "в клановой системе без ручной путаницы.",
        "",
        "Для привязки аккаунта используй команду /link_account.",
        "Понадобится API-код из игры: профиль игрока → настройки → API-код.",
        "",
        "После привязки аккаунт будет учитываться в списках клана, напоминаниях "
        "и решениях офицеров.",
    )
)


async def handle_start_command(message: Message) -> None:
    """Отвечает на команду `/start` в личке.

    TelegramUser создаётся и обновляется не здесь, а в `TelegramUserMiddleware`.
    Handler отвечает только за пользовательскую инструкцию.

    Args:
        message: Входящее Telegram-сообщение.
    """
    await message.answer(_START_MESSAGE)


def create_start_router() -> Router:
    """Создаёт router команды `/start`.

    Returns:
        Router с обработчиком `/start` только для личных чатов.
    """
    router = Router(name="start")
    router.message.register(
        handle_start_command,
        CommandStart(),
        F.chat.type == "private",
    )

    return router


__all__ = [
    "create_start_router",
    "handle_start_command",
]
