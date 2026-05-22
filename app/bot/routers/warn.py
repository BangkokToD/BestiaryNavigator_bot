"""Router target resolution команды `/warn`."""

import re
from dataclasses import asdict
from typing import Protocol

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.services import (
    WarningTargetCandidate,
    WarningTargetResolution,
    WarningTargetResolutionKind,
)

_PLAYER_TAG_PATTERN = re.compile(r"#[A-Za-z0-9]+")
_USERNAME_PATTERN = re.compile(r"@[A-Za-z0-9_]{3,32}")
_WARN_TARGET_CALLBACK_PREFIX = "warn_target:"


class WarnFlowStates(StatesGroup):
    """FSM states ручного warn-сценария."""

    waiting_for_reason = State()


class WarnTargetResolver(Protocol):
    """Contract resolver-а цели warn для bot handler-а."""

    async def resolve_by_reply_telegram_id(self, telegram_id: int) -> WarningTargetResolution:
        """Ищет цель по Telegram ID reply."""

    async def resolve_by_player_tag(self, player_tag: str) -> WarningTargetResolution:
        """Ищет цель по player tag."""

    async def resolve_by_username(self, username: str) -> WarningTargetResolution:
        """Ищет цель по Telegram username."""

    async def resolve_by_telegram_user_id(self, telegram_user_id: int) -> WarningTargetResolution:
        """Ищет цель по DB ID TelegramUser."""


async def handle_warn_command(
    message: Message,
    state: FSMContext,
    warning_target_resolver: WarnTargetResolver,
) -> None:
    """Определяет цель команды `/warn` без создания Warning.

    Args:
        message: Сообщение с командой `/warn`.
        state: FSM context.
        warning_target_resolver: Сервис поиска цели warn.
    """
    await state.clear()

    hint = _extract_warn_target_hint(message)
    if hint is None:
        await message.answer(
            "Укажи цель: ответом на сообщение, тегом игрока #PLAYER_TAG или @username."
        )
        return

    resolution = await _resolve_hint(
        hint=hint,
        warning_target_resolver=warning_target_resolver,
    )
    await _handle_resolution(message=message, state=state, resolution=resolution)


async def handle_warn_target_choice(
    callback_query: CallbackQuery,
    state: FSMContext,
    warning_target_resolver: WarnTargetResolver,
) -> None:
    """Обрабатывает выбор цели warn из inline-кнопок.

    Args:
        callback_query: Callback query с выбранным TelegramUser ID.
        state: FSM context.
        warning_target_resolver: Сервис поиска цели warn.
    """
    telegram_user_id = _parse_warn_target_callback_data(callback_query.data or "")
    if telegram_user_id is None:
        await callback_query.answer("Цель не найдена.", show_alert=True)
        return

    resolution = await warning_target_resolver.resolve_by_telegram_user_id(telegram_user_id)
    if resolution.kind != WarningTargetResolutionKind.RESOLVED:
        await callback_query.answer("Цель больше недоступна.", show_alert=True)
        return

    candidate = resolution.candidates[0]
    await _save_pending_target(state=state, candidate=candidate)
    await callback_query.answer("Цель выбрана.")

    if callback_query.message is not None:
        await callback_query.message.answer(_format_resolved_message(candidate))


def create_warn_router() -> Router:
    """Создаёт router target resolution команды `/warn`.

    Returns:
        Router warn-сценария.
    """
    router = Router(name="warn")
    router.message.register(handle_warn_command, Command("warn"))
    router.callback_query.register(
        handle_warn_target_choice,
        F.data.startswith(_WARN_TARGET_CALLBACK_PREFIX),
    )

    return router


async def _resolve_hint(
    *,
    hint: tuple[str, str | int],
    warning_target_resolver: WarnTargetResolver,
) -> WarningTargetResolution:
    """Резолвит target hint по приоритету команды.

    Args:
        hint: Пара `(kind, value)`.
        warning_target_resolver: Сервис поиска цели warn.

    Returns:
        Результат поиска цели.
    """
    kind, value = hint

    if kind == "reply":
        return await warning_target_resolver.resolve_by_reply_telegram_id(int(value))

    if kind == "tag":
        return await warning_target_resolver.resolve_by_player_tag(str(value))

    return await warning_target_resolver.resolve_by_username(str(value))


async def _handle_resolution(
    *,
    message: Message,
    state: FSMContext,
    resolution: WarningTargetResolution,
) -> None:
    """Обрабатывает результат поиска цели warn.

    Args:
        message: Входящее сообщение.
        state: FSM context.
        resolution: Результат поиска цели.
    """
    if resolution.kind == WarningTargetResolutionKind.NOT_FOUND:
        await message.answer("Цель warn не найдена. Проверь reply, тег игрока или @username.")
        return

    if resolution.kind == WarningTargetResolutionKind.UNLINKED:
        player_tag_text = f" {resolution.player_tag}" if resolution.player_tag else ""
        await message.answer(f"Аккаунт{player_tag_text} не привязан к Telegram. Warn не создан.")
        return

    if resolution.kind == WarningTargetResolutionKind.AMBIGUOUS:
        await message.answer(
            "Найдено несколько целей. Выбери нужную:",
            reply_markup=_build_ambiguity_keyboard(resolution.candidates),
        )
        return

    candidate = resolution.candidates[0]
    await _save_pending_target(state=state, candidate=candidate)
    await message.answer(_format_resolved_message(candidate))


async def _save_pending_target(
    *,
    state: FSMContext,
    candidate: WarningTargetCandidate,
) -> None:
    """Сохраняет выбранную цель warn в FSM без создания Warning.

    Args:
        state: FSM context.
        candidate: Выбранная цель.
    """
    await state.update_data(
        warn_target={
            "telegram_user_id": candidate.telegram_user_id,
            "telegram_id": candidate.telegram_id,
            "label": candidate.label,
            "accounts": [asdict(account) for account in candidate.accounts],
        }
    )
    await state.set_state(WarnFlowStates.waiting_for_reason)


def _extract_warn_target_hint(message: Message) -> tuple[str, str | int] | None:
    """Извлекает target hint с приоритетом reply -> #tag -> @username.

    Args:
        message: Сообщение `/warn`.

    Returns:
        Target hint или `None`.
    """
    reply_to_message = getattr(message, "reply_to_message", None)
    reply_user = getattr(reply_to_message, "from_user", None)
    reply_user_id = getattr(reply_user, "id", None)
    if isinstance(reply_user_id, int) and reply_user_id > 0:
        return ("reply", reply_user_id)

    text = message.text or ""

    tag_match = _PLAYER_TAG_PATTERN.search(text)
    if tag_match is not None:
        return ("tag", tag_match.group(0))

    username_match = _USERNAME_PATTERN.search(text)
    if username_match is not None:
        return ("username", username_match.group(0))

    return None


def _build_ambiguity_keyboard(
    candidates: tuple[WarningTargetCandidate, ...],
) -> InlineKeyboardMarkup:
    """Создаёт inline-кнопки выбора цели.

    Args:
        candidates: Кандидаты цели warn.

    Returns:
        Inline keyboard.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_candidate_button_text(candidate),
                    callback_data=f"{_WARN_TARGET_CALLBACK_PREFIX}{candidate.telegram_user_id}",
                )
            ]
            for candidate in candidates
        ]
    )


def _candidate_button_text(candidate: WarningTargetCandidate) -> str:
    """Создаёт текст кнопки цели.

    Args:
        candidate: Кандидат цели.

    Returns:
        Текст inline-кнопки.
    """
    first_account = candidate.accounts[0]
    suffix = f" +{len(candidate.accounts) - 1}" if len(candidate.accounts) > 1 else ""

    return f"{candidate.label} · {first_account.player_name} {first_account.player_tag}{suffix}"


def _format_resolved_message(candidate: WarningTargetCandidate) -> str:
    """Форматирует сообщение найденной цели.

    Args:
        candidate: Найденная цель.

    Returns:
        Текст ответа.
    """
    accounts = ", ".join(
        f"{account.player_name} {account.player_tag}" for account in candidate.accounts
    )

    return "\n".join(
        (
            f"Цель найдена: {candidate.label}.",
            f"Аккаунты: {accounts}.",
            "Теперь нужно выбрать причину warn. До выбора причины запись warn не создана.",
        )
    )


def _parse_warn_target_callback_data(value: str) -> int | None:
    """Парсит callback data выбора цели warn.

    Args:
        value: Callback data.

    Returns:
        DB ID TelegramUser или `None`.
    """
    if not value.startswith(_WARN_TARGET_CALLBACK_PREFIX):
        return None

    raw_id = value.removeprefix(_WARN_TARGET_CALLBACK_PREFIX)
    try:
        telegram_user_id = int(raw_id)
    except ValueError:
        return None

    return telegram_user_id if telegram_user_id > 0 else None


__all__ = [
    "WarnFlowStates",
    "WarnTargetResolver",
    "create_warn_router",
    "handle_warn_command",
    "handle_warn_target_choice",
]
