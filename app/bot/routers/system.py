"""Системный router Telegram bot.

В этом коммите router намеренно не содержит бизнес-команд. Команды `/start`,
`/link_account`, `/warn` и `/register` добавляются отдельными commit-ами PR-13.
"""

from aiogram import Router


def create_system_router() -> Router:
    """Создаёт router системного bot-модуля.

    Router создаётся фабрикой, а не module-level singleton, потому что aiogram
    запрещает повторно подключать один и тот же router к разным parent router.

    Returns:
        Новый router системного bot-модуля.
    """
    return Router(name="system")


__all__ = [
    "create_system_router",
]
