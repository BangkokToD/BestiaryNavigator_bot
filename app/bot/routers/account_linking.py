"""Router сценария `/link_account` Telegram bot."""

from dataclasses import dataclass
from typing import Protocol

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app.db.models import PlayerAccount, TelegramUser
from app.domain import TagValidationError, normalize_player_tag
from app.integrations.clash import ClashApiError, ClashNotFoundError
from app.services import AccountLinkingError, AccountLinkingResult

_LINK_ACCOUNT_INTRO = "\n".join(
    (
        "Начинаем привязку аккаунта Clash of Clans.",
        "",
        "Сначала отправь тег игрока. Пример: #2ABC или 2ABC.",
        "Для отмены напиши /cancel.",
    )
)
_TOKEN_REQUEST_MESSAGE = "\n".join(
    (
        "Тег принят.",
        "",
        "Теперь отправь API-код из игры:",
        "профиль игрока → настройки → API-код.",
        "",
        "Для отмены напиши /cancel.",
    )
)
_CANCEL_MESSAGE = "Привязка аккаунта отменена."
_EMPTY_TOKEN_MESSAGE = "API-код не может быть пустым. Отправь API-код или напиши /cancel."
_FAILED_VERIFY_MESSAGE = "\n".join(
    (
        "API-код не подошёл.",
        "Проверь тег игрока и одноразовый API-код из игры, затем начни заново: /link_account.",
    )
)
_MISSING_USER_MESSAGE = (
    "Не удалось определить Telegram-пользователя. Напиши /start и попробуй снова."
)
_PLAYER_NOT_FOUND_MESSAGE = "Игрок с таким тегом не найден в Clash of Clans API."
_CLASH_TEMPORARY_ERROR_MESSAGE = "Clash API временно не выполнил проверку. Попробуй позже."
_GENERIC_LINKING_ERROR_MESSAGE = "Не удалось привязать аккаунт. Проверь тег и попробуй снова."


class AccountLinkingFlowStates(StatesGroup):
    """FSM states сценария привязки игрового аккаунта."""

    waiting_for_player_tag = State()
    waiting_for_api_token = State()


class AccountLinkingFlowService(Protocol):
    """Contract сервиса привязки аккаунта для bot handler-а."""

    async def link_account(
        self,
        *,
        telegram_user: TelegramUser,
        player_tag: str,
        api_token: str,
    ) -> AccountLinkingResult:
        """Привязывает игровой аккаунт к Telegram-пользователю.

        Args:
            telegram_user: Telegram-пользователь.
            player_tag: Тег игрового аккаунта.
            api_token: One-time API token из игры.

        Returns:
            Результат привязки.
        """


@dataclass(frozen=True, slots=True)
class _LinkingSuccessView:
    """Данные успешной привязки для пользовательского ответа."""

    player_tag: str
    player_name: str


async def handle_link_account_command(message: Message, state: FSMContext) -> None:
    """Запускает FSM привязки аккаунта.

    Args:
        message: Входящее сообщение с `/link_account`.
        state: FSM context пользователя.
    """
    await state.clear()
    await state.set_state(AccountLinkingFlowStates.waiting_for_player_tag)
    await message.answer(_LINK_ACCOUNT_INTRO)


async def handle_link_account_cancel(message: Message, state: FSMContext) -> None:
    """Отменяет текущий FSM-сценарий привязки аккаунта.

    Args:
        message: Входящее сообщение с `/cancel`.
        state: FSM context пользователя.
    """
    await state.clear()
    await message.answer(_CANCEL_MESSAGE)


async def handle_player_tag_step(message: Message, state: FSMContext) -> None:
    """Принимает тег игрока и переводит FSM к ожиданию API token.

    Args:
        message: Сообщение с тегом игрока.
        state: FSM context пользователя.
    """
    try:
        player_tag = normalize_player_tag(_message_text(message))
    except TagValidationError as exc:
        await message.answer(f"{exc} Отправь тег игрока ещё раз или напиши /cancel.")
        return

    await state.update_data(player_tag=player_tag)
    await state.set_state(AccountLinkingFlowStates.waiting_for_api_token)
    await message.answer(_TOKEN_REQUEST_MESSAGE)


async def handle_api_token_step(
    message: Message,
    state: FSMContext,
    telegram_user: TelegramUser | None,
    account_linking_service: AccountLinkingFlowService,
) -> None:
    """Принимает API token и завершает сценарий привязки.

    API token намеренно не логируется и не добавляется в ответы пользователю.

    Args:
        message: Сообщение с API token.
        state: FSM context пользователя.
        telegram_user: TelegramUser из middleware.
        account_linking_service: Сервис привязки аккаунта.
    """
    api_token = _message_text(message)
    if not api_token:
        await message.answer(_EMPTY_TOKEN_MESSAGE)
        return

    state_data = await state.get_data()
    player_tag = str(state_data.get("player_tag") or "")
    if not player_tag:
        await state.clear()
        await message.answer("Тег игрока потерян. Начни заново: /link_account.")
        return

    if telegram_user is None:
        await state.clear()
        await message.answer(_MISSING_USER_MESSAGE)
        return

    try:
        result = await account_linking_service.link_account(
            telegram_user=telegram_user,
            player_tag=player_tag,
            api_token=api_token,
        )
    except ClashNotFoundError:
        await state.clear()
        await message.answer(_PLAYER_NOT_FOUND_MESSAGE)
        return
    except ClashApiError:
        await state.clear()
        await message.answer(_CLASH_TEMPORARY_ERROR_MESSAGE)
        return
    except (AccountLinkingError, TagValidationError, ValueError):
        await state.clear()
        await message.answer(_GENERIC_LINKING_ERROR_MESSAGE)
        return

    await state.clear()

    if not result.success:
        await message.answer(_FAILED_VERIFY_MESSAGE)
        return

    success_view = _build_success_view(result.player_account)
    await message.answer(
        "\n".join(
            (
                "Аккаунт привязан.",
                f"{success_view.player_name} · {success_view.player_tag}",
            )
        )
    )


def create_account_linking_router() -> Router:
    """Создаёт router сценария `/link_account`.

    Returns:
        Router с FSM handlers привязки аккаунта.
    """
    router = Router(name="account_linking")
    private_chat_filter = F.chat.type == "private"

    router.message.register(
        handle_link_account_cancel,
        Command("cancel"),
        StateFilter(
            AccountLinkingFlowStates.waiting_for_player_tag,
            AccountLinkingFlowStates.waiting_for_api_token,
        ),
        private_chat_filter,
    )
    router.message.register(
        handle_link_account_command,
        Command("link_account"),
        private_chat_filter,
    )
    router.message.register(
        handle_player_tag_step,
        StateFilter(AccountLinkingFlowStates.waiting_for_player_tag),
        private_chat_filter,
    )
    router.message.register(
        handle_api_token_step,
        StateFilter(AccountLinkingFlowStates.waiting_for_api_token),
        private_chat_filter,
    )

    return router


def _message_text(message: Message) -> str:
    """Достаёт текст сообщения без логирования содержимого.

    Args:
        message: Входящее сообщение.

    Returns:
        Текст без пробелов по краям.
    """
    return (message.text or "").strip()


def _build_success_view(player_account: PlayerAccount | None) -> _LinkingSuccessView:
    """Создаёт данные success-ответа.

    Args:
        player_account: Привязанный аккаунт.

    Returns:
        View данных аккаунта.

    Raises:
        ValueError: Если сервис вернул success без аккаунта.
    """
    if player_account is None:
        raise ValueError("AccountLinkingService вернул success без PlayerAccount.")

    return _LinkingSuccessView(
        player_tag=player_account.player_tag,
        player_name=player_account.name,
    )


__all__ = [
    "AccountLinkingFlowService",
    "AccountLinkingFlowStates",
    "create_account_linking_router",
    "handle_api_token_step",
    "handle_link_account_cancel",
    "handle_link_account_command",
    "handle_player_tag_step",
]
