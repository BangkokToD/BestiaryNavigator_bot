"""Router target resolution команды `/warn`."""

import re
from dataclasses import asdict, dataclass
from typing import Protocol

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db.models import TelegramUser
from app.domain import MANUAL_WARNING_REASON_CODES, WarningReasonCode
from app.services import (
    WarningAffectedAccount,
    WarningCreationError,
    WarningCreationResult,
    WarningTargetCandidate,
    WarningTargetResolution,
    WarningTargetResolutionKind,
    WarnPermissionDecision,
    WarnPermissionStatus,
)

_PLAYER_TAG_PATTERN = re.compile(r"#[A-Za-z0-9]+")
_USERNAME_PATTERN = re.compile(r"@[A-Za-z0-9_]{3,32}")
_WARN_TARGET_CALLBACK_PREFIX = "warn_target:"
_WARN_REASON_CALLBACK_PREFIX = "warn_reason:"
_OTHER_COMMENT_REQUIRED_MESSAGE = (
    "Для причины other нужен комментарий. Отправь комментарий следующим сообщением."
)


class WarnFlowStates(StatesGroup):
    """FSM states ручного warn-сценария."""

    waiting_for_reason = State()
    waiting_for_other_comment = State()


@dataclass(frozen=True, slots=True)
class _WarnReasonCallbackData:
    """Данные callback-а выбора причины warn."""

    author_telegram_user_id: int
    reason_code: WarningReasonCode


@dataclass(frozen=True, slots=True)
class _PendingWarnTarget:
    """Цель warn, сохранённая в FSM."""

    telegram_user_id: int
    label: str
    accounts: tuple[WarningAffectedAccount, ...]


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


class WarnPermissionChecker(Protocol):
    """Contract сервиса проверки прав ручного warn."""

    async def check_initiator(
        self,
        *,
        telegram_user: TelegramUser | None,
    ) -> WarnPermissionDecision:
        """Проверяет право инициатора использовать `/warn`."""

    async def check_target(
        self,
        *,
        candidate: WarningTargetCandidate,
    ) -> WarnPermissionDecision:
        """Проверяет, что цель находится в main-клане."""


class ManualWarningCreationService(Protocol):
    """Contract сервиса создания manual warn для bot handler-а."""

    async def create_manual_warning_for_telegram_user_id(
        self,
        *,
        telegram_user_id: int,
        reason_code: WarningReasonCode | str,
        author_telegram_user: TelegramUser | None = None,
        affected_accounts: list[WarningAffectedAccount] | None = None,
        comment: str | None = None,
    ) -> WarningCreationResult:
        """Создаёт manual warn по DB ID TelegramUser."""


async def handle_warn_command(
    message: Message,
    state: FSMContext,
    telegram_user: TelegramUser | None,
    warning_target_resolver: WarnTargetResolver,
    warning_permission_service: WarnPermissionChecker,
) -> None:
    """Определяет цель команды `/warn` без создания Warning.

    Args:
        message: Сообщение с командой `/warn`.
        state: FSM context.
        telegram_user: Инициатор команды из middleware.
        warning_target_resolver: Сервис поиска цели warn.
        warning_permission_service: Сервис проверки прав warn.
    """
    await state.clear()

    initiator_decision = await warning_permission_service.check_initiator(
        telegram_user=telegram_user,
    )
    if not initiator_decision.allowed:
        await message.answer(_format_initiator_denial(initiator_decision))
        return

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
    await _handle_resolution(
        message=message,
        state=state,
        resolution=resolution,
        telegram_user=telegram_user,
        warning_permission_service=warning_permission_service,
    )


async def handle_warn_target_choice(
    callback_query: CallbackQuery,
    state: FSMContext,
    telegram_user: TelegramUser | None,
    warning_target_resolver: WarnTargetResolver,
    warning_permission_service: WarnPermissionChecker,
) -> None:
    """Обрабатывает выбор цели warn из inline-кнопок.

    Args:
        callback_query: Callback query с выбранным TelegramUser ID.
        state: FSM context.
        telegram_user: Пользователь, который нажал inline-кнопку.
        warning_target_resolver: Сервис поиска цели warn.
        warning_permission_service: Сервис проверки прав warn.
    """
    initiator_decision = await warning_permission_service.check_initiator(
        telegram_user=telegram_user,
    )
    if not initiator_decision.allowed:
        await callback_query.answer(
            _format_initiator_denial(initiator_decision),
            show_alert=True,
        )
        return

    telegram_user_id = _parse_warn_target_callback_data(callback_query.data or "")
    if telegram_user_id is None:
        await callback_query.answer("Цель не найдена.", show_alert=True)
        return

    resolution = await warning_target_resolver.resolve_by_telegram_user_id(telegram_user_id)
    if resolution.kind != WarningTargetResolutionKind.RESOLVED:
        await callback_query.answer("Цель больше недоступна.", show_alert=True)
        return

    candidate = resolution.candidates[0]
    target_decision = await warning_permission_service.check_target(candidate=candidate)
    if not target_decision.allowed:
        await callback_query.answer(
            _format_target_denial(target_decision),
            show_alert=True,
        )
        return

    author_telegram_user_id = _telegram_user_db_id(telegram_user)
    if author_telegram_user_id is None:
        await callback_query.answer(
            "Не удалось определить автора warn. Напиши /start и попробуй снова.",
            show_alert=True,
        )
        return

    await _save_pending_target(
        state=state,
        candidate=candidate,
        author_telegram_user_id=author_telegram_user_id,
    )
    await callback_query.answer("Цель выбрана.")

    if callback_query.message is not None:
        await callback_query.message.answer(
            _format_resolved_message(candidate),
            reply_markup=_build_reason_keyboard(author_telegram_user_id),
        )


async def handle_warn_reason_choice(
    callback_query: CallbackQuery,
    state: FSMContext,
    telegram_user: TelegramUser | None,
    warning_creation_service: ManualWarningCreationService,
) -> None:
    """Создаёт manual warn после inline-выбора причины.

    Args:
        callback_query: Callback query выбора reason code.
        state: FSM context.
        telegram_user: Пользователь, который нажал кнопку.
        warning_creation_service: Сервис создания warning.
    """
    reason_data = _parse_warn_reason_callback_data(callback_query.data or "")
    if reason_data is None:
        await callback_query.answer("Причина warn не найдена.", show_alert=True)
        return

    state_data = await state.get_data()
    if not _is_authorized_reason_actor(
        telegram_user=telegram_user,
        state_data=state_data,
        author_telegram_user_id=reason_data.author_telegram_user_id,
    ):
        await callback_query.answer(
            "Выбрать причину может только автор команды /warn.",
            show_alert=True,
        )
        return

    if reason_data.reason_code == WarningReasonCode.OTHER:
        await state.update_data(warn_reason=reason_data.reason_code.value)
        await state.set_state(WarnFlowStates.waiting_for_other_comment)
        await callback_query.answer("Нужен комментарий.")
        if callback_query.message is not None:
            await callback_query.message.answer(_OTHER_COMMENT_REQUIRED_MESSAGE)
        return

    try:
        result = await _create_manual_warning_from_state(
            state_data=state_data,
            reason_code=reason_data.reason_code,
            author_telegram_user=telegram_user,
            warning_creation_service=warning_creation_service,
            comment=None,
        )
    except (ValueError, WarningCreationError):
        await state.clear()
        await callback_query.answer("Warn не создан.", show_alert=True)
        return

    await state.clear()
    await callback_query.answer("Warn создан.")

    if callback_query.message is not None:
        await callback_query.message.answer(_format_warning_created_message(result))


async def handle_warn_other_comment(
    message: Message,
    state: FSMContext,
    telegram_user: TelegramUser | None,
    warning_creation_service: ManualWarningCreationService,
) -> None:
    """Создаёт warn с reason `other` после обязательного комментария.

    Args:
        message: Сообщение с комментарием.
        state: FSM context.
        telegram_user: Автор warn.
        warning_creation_service: Сервис создания warning.
    """
    state_data = await state.get_data()
    author_telegram_user_id = _state_author_telegram_user_id(state_data)
    if author_telegram_user_id is None or not _same_telegram_user(
        telegram_user,
        author_telegram_user_id,
    ):
        await message.answer("Комментарий может отправить только автор команды /warn.")
        return

    comment = (message.text or "").strip()
    if not comment:
        await message.answer("Комментарий для other обязателен. Отправь текст комментария.")
        return

    if state_data.get("warn_reason") != WarningReasonCode.OTHER.value:
        await state.clear()
        await message.answer("Сценарий warn устарел. Начни заново: /warn.")
        return

    try:
        result = await _create_manual_warning_from_state(
            state_data=state_data,
            reason_code=WarningReasonCode.OTHER,
            author_telegram_user=telegram_user,
            warning_creation_service=warning_creation_service,
            comment=comment,
        )
    except (ValueError, WarningCreationError):
        await state.clear()
        await message.answer("Warn не создан. Начни заново: /warn.")
        return

    await state.clear()
    await message.answer(_format_warning_created_message(result))


def create_warn_router() -> Router:
    """Создаёт router target resolution команды `/warn`.

    Returns:
        Router warn-сценария.
    """
    router = Router(name="warn")
    router.message.register(handle_warn_command, Command("warn"))
    router.message.register(
        handle_warn_other_comment,
        StateFilter(WarnFlowStates.waiting_for_other_comment),
    )
    router.callback_query.register(
        handle_warn_target_choice,
        F.data.startswith(_WARN_TARGET_CALLBACK_PREFIX),
    )
    router.callback_query.register(
        handle_warn_reason_choice,
        F.data.startswith(_WARN_REASON_CALLBACK_PREFIX),
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
    telegram_user: TelegramUser | None,
    warning_permission_service: WarnPermissionChecker,
) -> None:
    """Обрабатывает результат поиска цели warn.

    Args:
        message: Входящее сообщение.
        state: FSM context.
        resolution: Результат поиска цели.
        telegram_user: Автор warn из middleware.
        warning_permission_service: Сервис проверки прав warn.
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
    target_decision = await warning_permission_service.check_target(candidate=candidate)
    if not target_decision.allowed:
        await message.answer(_format_target_denial(target_decision))
        return

    author_telegram_user_id = _telegram_user_db_id(telegram_user)
    if author_telegram_user_id is None:
        await message.answer("Не удалось определить автора warn. Напиши /start и попробуй снова.")
        return

    await _save_pending_target(
        state=state,
        candidate=candidate,
        author_telegram_user_id=author_telegram_user_id,
    )
    await message.answer(
        _format_resolved_message(candidate),
        reply_markup=_build_reason_keyboard(author_telegram_user_id),
    )


async def _save_pending_target(
    *,
    state: FSMContext,
    candidate: WarningTargetCandidate,
    author_telegram_user_id: int,
) -> None:
    """Сохраняет выбранную цель warn в FSM без создания Warning.

    Args:
        state: FSM context.
        candidate: Выбранная цель.
    """
    await state.update_data(
        warn_author_telegram_user_id=author_telegram_user_id,
        warn_target={
            "telegram_user_id": candidate.telegram_user_id,
            "telegram_id": candidate.telegram_id,
            "label": candidate.label,
            "accounts": [asdict(account) for account in candidate.accounts],
        },
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


def _build_reason_keyboard(author_telegram_user_id: int) -> InlineKeyboardMarkup:
    """Создаёт inline-кнопки выбора manual reason code.

    Args:
        author_telegram_user_id: DB ID автора warn.

    Returns:
        Inline keyboard с manual reason codes.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=reason_code.value,
                    callback_data=(
                        f"{_WARN_REASON_CALLBACK_PREFIX}"
                        f"{author_telegram_user_id}:{reason_code.value}"
                    ),
                )
            ]
            for reason_code in _manual_reason_codes()
        ]
    )


def _manual_reason_codes() -> tuple[WarningReasonCode, ...]:
    """Возвращает manual reason codes в стабильном порядке."""
    return tuple(
        reason_code
        for reason_code in WarningReasonCode
        if reason_code in MANUAL_WARNING_REASON_CODES
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


def _format_warning_created_message(result: WarningCreationResult) -> str:
    """Форматирует ответ после создания warn.

    Args:
        result: Результат создания warning.

    Returns:
        Текст ответа.
    """
    warning = result.warning
    created_text = "создан" if result.created else "уже существовал"
    return "\n".join(
        (
            f"Warn {created_text}.",
            f"Причина: {warning.reason_code}.",
            "Manual warn не влияет на автоматические решения.",
        )
    )


def _format_initiator_denial(decision: WarnPermissionDecision) -> str:
    """Форматирует отказ инициатору `/warn`.

    Args:
        decision: Результат проверки прав.

    Returns:
        Текст отказа.
    """
    if decision.status == WarnPermissionStatus.NO_TELEGRAM_USER:
        return "Не удалось определить Telegram-пользователя. Напиши /start и попробуй снова."

    if decision.status == WarnPermissionStatus.NO_LINKED_MAIN_ACCOUNT:
        return "Команда /warn доступна только игрокам с привязанным аккаунтом в основном клане."

    if decision.status == WarnPermissionStatus.ROLE_UNCONFIRMED:
        return "Не удалось подтвердить актуальную роль в основном клане. Попробуй позже."

    return "Команда /warn доступна только leader, coLeader или elder основного клана."


def _format_target_denial(decision: WarnPermissionDecision) -> str:
    """Форматирует отказ по цели warn.

    Args:
        decision: Результат проверки цели.

    Returns:
        Текст отказа.
    """
    if decision.status == WarnPermissionStatus.TARGET_UNCONFIRMED:
        return "Не удалось подтвердить, что цель сейчас находится в основном клане. Warn не создан."

    return (
        "Цель не подтверждена в основном клане. Academy/freezer не участвуют "
        "в ручной warn-системе. Warn не создан."
    )


async def _create_manual_warning_from_state(
    *,
    state_data: dict[str, object],
    reason_code: WarningReasonCode,
    author_telegram_user: TelegramUser | None,
    warning_creation_service: ManualWarningCreationService,
    comment: str | None,
) -> WarningCreationResult:
    """Создаёт manual warning на основе pending FSM state.

    Args:
        state_data: FSM data.
        reason_code: Выбранная причина warn.
        author_telegram_user: Автор warning.
        warning_creation_service: Сервис создания warning.
        comment: Комментарий.

    Returns:
        Результат создания warning.
    """
    if author_telegram_user is None:
        raise ValueError("Автор warn не найден.")

    target = _pending_warn_target_from_state(state_data)
    if target is None:
        raise ValueError("Цель warn не найдена в FSM.")

    return await warning_creation_service.create_manual_warning_for_telegram_user_id(
        telegram_user_id=target.telegram_user_id,
        reason_code=reason_code,
        author_telegram_user=author_telegram_user,
        affected_accounts=list(target.accounts),
        comment=comment,
    )


def _pending_warn_target_from_state(
    state_data: dict[str, object],
) -> _PendingWarnTarget | None:
    """Достаёт pending target из FSM data.

    Args:
        state_data: FSM data.

    Returns:
        Pending target или `None`.
    """
    raw_target = state_data.get("warn_target")
    if not isinstance(raw_target, dict):
        return None

    telegram_user_id = raw_target.get("telegram_user_id")
    label = raw_target.get("label")
    raw_accounts = raw_target.get("accounts")

    if not isinstance(telegram_user_id, int) or telegram_user_id <= 0:
        return None

    if not isinstance(label, str) or not label.strip():
        return None

    if not isinstance(raw_accounts, list):
        return None

    accounts: list[WarningAffectedAccount] = []
    for raw_account in raw_accounts:
        if not isinstance(raw_account, dict):
            return None

        player_tag = raw_account.get("player_tag")
        player_name = raw_account.get("player_name")

        if not isinstance(player_tag, str) or not isinstance(player_name, str):
            return None

        accounts.append(
            WarningAffectedAccount(
                player_tag=player_tag,
                player_name=player_name,
            )
        )

    return _PendingWarnTarget(
        telegram_user_id=telegram_user_id,
        label=label,
        accounts=tuple(accounts),
    )


def _parse_warn_reason_callback_data(value: str) -> _WarnReasonCallbackData | None:
    """Парсит callback data выбора причины warn.

    Args:
        value: Callback data.

    Returns:
        Parsed data или `None`.
    """
    if not value.startswith(_WARN_REASON_CALLBACK_PREFIX):
        return None

    raw_value = value.removeprefix(_WARN_REASON_CALLBACK_PREFIX)
    try:
        raw_author_id, raw_reason_code = raw_value.split(":", maxsplit=1)
        author_telegram_user_id = int(raw_author_id)
        reason_code = WarningReasonCode(raw_reason_code)
    except (ValueError, TypeError):
        return None

    if author_telegram_user_id <= 0:
        return None

    if reason_code not in MANUAL_WARNING_REASON_CODES:
        return None

    return _WarnReasonCallbackData(
        author_telegram_user_id=author_telegram_user_id,
        reason_code=reason_code,
    )


def _is_authorized_reason_actor(
    *,
    telegram_user: TelegramUser | None,
    state_data: dict[str, object],
    author_telegram_user_id: int,
) -> bool:
    """Проверяет, что причину выбирает автор warn-сценария."""
    return (
        _same_telegram_user(telegram_user, author_telegram_user_id)
        and _state_author_telegram_user_id(state_data) == author_telegram_user_id
    )


def _state_author_telegram_user_id(state_data: dict[str, object]) -> int | None:
    """Достаёт DB ID автора warn из FSM data."""
    value = state_data.get("warn_author_telegram_user_id")
    if isinstance(value, int) and value > 0:
        return value

    return None


def _same_telegram_user(
    telegram_user: TelegramUser | None,
    telegram_user_id: int,
) -> bool:
    """Проверяет совпадение TelegramUser DB ID."""
    return _telegram_user_db_id(telegram_user) == telegram_user_id


def _telegram_user_db_id(telegram_user: TelegramUser | None) -> int | None:
    """Достаёт DB ID TelegramUser."""
    if telegram_user is None:
        return None

    telegram_user_id = getattr(telegram_user, "id", None)
    if isinstance(telegram_user_id, int) and telegram_user_id > 0:
        return telegram_user_id

    return None


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
    "ManualWarningCreationService",
    "WarnFlowStates",
    "WarnPermissionChecker",
    "WarnTargetResolver",
    "create_warn_router",
    "handle_warn_command",
    "handle_warn_other_comment",
    "handle_warn_reason_choice",
    "handle_warn_target_choice",
]
