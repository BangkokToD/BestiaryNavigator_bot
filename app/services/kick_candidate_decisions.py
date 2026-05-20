"""Сервис workflow-решений по кандидатам на кик."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KickCandidate, TelegramUser
from app.domain import KickCandidateStatus, require_domain_enum_value

_TERMINAL_STATUSES = frozenset(
    {
        KickCandidateStatus.REJECTED,
        KickCandidateStatus.EXECUTED,
    }
)

_ALLOWED_TRANSITIONS = {
    KickCandidateStatus.PENDING_ADMIN_DECISION: frozenset(
        {
            KickCandidateStatus.APPROVED,
            KickCandidateStatus.REJECTED,
            KickCandidateStatus.POSTPONED,
            KickCandidateStatus.MANUAL_REQUIRED,
        }
    ),
    KickCandidateStatus.POSTPONED: frozenset(
        {
            KickCandidateStatus.APPROVED,
            KickCandidateStatus.REJECTED,
            KickCandidateStatus.POSTPONED,
            KickCandidateStatus.MANUAL_REQUIRED,
        }
    ),
    KickCandidateStatus.APPROVED: frozenset({KickCandidateStatus.EXECUTED}),
    KickCandidateStatus.MANUAL_REQUIRED: frozenset({KickCandidateStatus.EXECUTED}),
}


class KickCandidateDecisionError(RuntimeError):
    """Базовая ошибка workflow-решений по кандидатам на кик."""


class KickCandidateNotFoundError(KickCandidateDecisionError):
    """Кандидат на кик не найден."""


class KickCandidateInvalidTransitionError(KickCandidateDecisionError):
    """Недопустимый переход статуса кандидата на кик."""


@dataclass(frozen=True, slots=True)
class KickCandidateDecisionResult:
    """Результат workflow-действия по кандидату на кик."""

    candidate: KickCandidate
    changed: bool


class KickCandidateDecisionRepository(Protocol):
    """Repository contract для workflow-решений по кандидатам."""

    async def get_by_id(self, candidate_id: int) -> KickCandidate | None:
        """Возвращает кандидата по DB ID.

        Args:
            candidate_id: DB ID кандидата.

        Returns:
            Кандидат или `None`.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyKickCandidateDecisionRepository:
    """SQLAlchemy-реализация repository для workflow-решений."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_id(self, candidate_id: int) -> KickCandidate | None:
        """Возвращает кандидата по DB ID.

        Args:
            candidate_id: DB ID кандидата.

        Returns:
            Кандидат или `None`.
        """
        result = await self._session.execute(
            select(KickCandidate).where(KickCandidate.id == candidate_id)
        )
        return result.scalar_one_or_none()

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class KickCandidateDecisionService:
    """Сервис workflow-решений по кандидатам на кик.

    Сервис меняет только поля decision workflow. Он не создаёт кандидатов,
    не удаляет их физически и не выполняет Telegram-действия.
    """

    def __init__(self, *, repository: KickCandidateDecisionRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository кандидатов на кик.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "KickCandidateDecisionService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный service.
        """
        return cls(repository=SqlAlchemyKickCandidateDecisionRepository(session))

    async def approve_candidate(
        self,
        *,
        candidate_id: int,
        decision_by: TelegramUser,
        comment: str | None = None,
    ) -> KickCandidateDecisionResult:
        """Подтверждает кандидата.

        Args:
            candidate_id: DB ID кандидата.
            decision_by: Пользователь, принявший решение.
            comment: Опциональный комментарий.

        Returns:
            Результат workflow-действия.
        """
        return await self._transition_candidate(
            candidate_id=candidate_id,
            target_status=KickCandidateStatus.APPROVED,
            decision_by=decision_by,
            comment=comment,
            comment_required=False,
            deadline_at=None,
            mark_executed=False,
        )

    async def reject_candidate(
        self,
        *,
        candidate_id: int,
        decision_by: TelegramUser,
        comment: str,
    ) -> KickCandidateDecisionResult:
        """Отклоняет кандидата.

        Args:
            candidate_id: DB ID кандидата.
            decision_by: Пользователь, принявший решение.
            comment: Обязательный комментарий.

        Returns:
            Результат workflow-действия.
        """
        return await self._transition_candidate(
            candidate_id=candidate_id,
            target_status=KickCandidateStatus.REJECTED,
            decision_by=decision_by,
            comment=comment,
            comment_required=True,
            deadline_at=None,
            mark_executed=False,
        )

    async def postpone_candidate(
        self,
        *,
        candidate_id: int,
        decision_by: TelegramUser,
        deadline_at: datetime,
        comment: str,
    ) -> KickCandidateDecisionResult:
        """Откладывает кандидата до конкретного срока.

        Args:
            candidate_id: DB ID кандидата.
            decision_by: Пользователь, принявший решение.
            deadline_at: Timezone-aware срок возврата к решению.
            comment: Обязательный комментарий.

        Returns:
            Результат workflow-действия.
        """
        return await self._transition_candidate(
            candidate_id=candidate_id,
            target_status=KickCandidateStatus.POSTPONED,
            decision_by=decision_by,
            comment=comment,
            comment_required=True,
            deadline_at=_validate_aware_datetime(deadline_at, field_name="deadline_at"),
            mark_executed=False,
        )

    async def mark_manual_required(
        self,
        *,
        candidate_id: int,
        decision_by: TelegramUser,
        comment: str | None = None,
    ) -> KickCandidateDecisionResult:
        """Помечает кандидата как требующего ручного действия.

        Args:
            candidate_id: DB ID кандидата.
            decision_by: Пользователь, принявший решение.
            comment: Опциональный комментарий.

        Returns:
            Результат workflow-действия.
        """
        return await self._transition_candidate(
            candidate_id=candidate_id,
            target_status=KickCandidateStatus.MANUAL_REQUIRED,
            decision_by=decision_by,
            comment=comment,
            comment_required=False,
            deadline_at=None,
            mark_executed=False,
        )

    async def mark_executed(
        self,
        *,
        candidate_id: int,
        decision_by: TelegramUser,
        comment: str | None = None,
    ) -> KickCandidateDecisionResult:
        """Помечает кандидата как исполненного.

        Args:
            candidate_id: DB ID кандидата.
            decision_by: Пользователь, отметивший выполнение.
            comment: Опциональный комментарий.

        Returns:
            Результат workflow-действия.
        """
        return await self._transition_candidate(
            candidate_id=candidate_id,
            target_status=KickCandidateStatus.EXECUTED,
            decision_by=decision_by,
            comment=comment,
            comment_required=False,
            deadline_at=None,
            mark_executed=True,
        )

    async def _transition_candidate(
        self,
        *,
        candidate_id: int,
        target_status: KickCandidateStatus,
        decision_by: TelegramUser,
        comment: str | None,
        comment_required: bool,
        deadline_at: datetime | None,
        mark_executed: bool,
    ) -> KickCandidateDecisionResult:
        """Выполняет валидированный переход статуса кандидата.

        Args:
            candidate_id: DB ID кандидата.
            target_status: Целевой статус.
            decision_by: Пользователь, принявший решение.
            comment: Комментарий решения.
            comment_required: Требуется ли непустой комментарий.
            deadline_at: Срок для postponed-статуса.
            mark_executed: Нужно ли заполнить `executed_at`.

        Returns:
            Результат workflow-действия.
        """
        normalized_candidate_id = _validate_positive_int(candidate_id, field_name="candidate_id")
        decision_by_id = _required_model_id(decision_by, model_name="TelegramUser")
        normalized_comment = _normalize_comment(comment, required=comment_required)
        candidate = await self._get_required_candidate(normalized_candidate_id)
        current_status = _normalize_status(candidate.status)

        _ensure_transition_allowed(current_status=current_status, target_status=target_status)

        decision_at = _utc_now()
        candidate.status = target_status.value
        candidate.decision_by_telegram_user_id = decision_by_id
        candidate.decision_by_user = decision_by
        candidate.decision_at = decision_at
        candidate.decision_comment = normalized_comment
        candidate.deadline_at = deadline_at

        if mark_executed:
            candidate.executed_at = decision_at

        await self._repository.flush()

        return KickCandidateDecisionResult(candidate=candidate, changed=True)

    async def _get_required_candidate(self, candidate_id: int) -> KickCandidate:
        """Возвращает кандидата или выбрасывает service error.

        Args:
            candidate_id: DB ID кандидата.

        Returns:
            Кандидат.

        Raises:
            KickCandidateNotFoundError: Если кандидат не найден.
        """
        candidate = await self._repository.get_by_id(candidate_id)
        if candidate is None:
            raise KickCandidateNotFoundError(f"Kick candidate {candidate_id} не найден.")

        return candidate


def _ensure_transition_allowed(
    *,
    current_status: KickCandidateStatus,
    target_status: KickCandidateStatus,
) -> None:
    """Проверяет допустимость перехода статуса.

    Args:
        current_status: Текущий статус кандидата.
        target_status: Целевой статус.

    Raises:
        KickCandidateInvalidTransitionError: Если переход запрещён.
    """
    if current_status in _TERMINAL_STATUSES:
        raise KickCandidateInvalidTransitionError(
            f"Нельзя изменить терминальный статус {current_status.value}."
        )

    allowed_targets = _ALLOWED_TRANSITIONS.get(current_status, frozenset())
    if target_status not in allowed_targets:
        raise KickCandidateInvalidTransitionError(
            f"Недопустимый переход {current_status.value} -> {target_status.value}."
        )


def _normalize_status(value: str) -> KickCandidateStatus:
    """Валидирует статус кандидата через доменный enum.

    Args:
        value: Строковый статус.

    Returns:
        Enum member `KickCandidateStatus`.
    """
    return require_domain_enum_value(
        KickCandidateStatus,
        value,
        field_name="candidate_status",
    )


def _normalize_comment(value: str | None, *, required: bool) -> str | None:
    """Нормализует комментарий решения.

    Args:
        value: Сырой комментарий.
        required: Требуется ли непустой комментарий.

    Returns:
        Нормализованный комментарий или `None`.

    Raises:
        KickCandidateDecisionError: Если комментарий обязателен, но пустой.
    """
    if value is None:
        if required:
            raise KickCandidateDecisionError("decision_comment не может быть пустым.")
        return None

    normalized = value.strip()
    if not normalized:
        if required:
            raise KickCandidateDecisionError("decision_comment не может быть пустым.")
        return None

    return normalized


def _validate_aware_datetime(value: datetime, *, field_name: str) -> datetime:
    """Проверяет timezone-aware datetime.

    Args:
        value: Datetime-значение.
        field_name: Имя поля для текста ошибки.

    Returns:
        Исходное datetime-значение.

    Raises:
        KickCandidateDecisionError: Если datetime не содержит timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise KickCandidateDecisionError(f"{field_name} должен быть timezone-aware datetime.")

    return value


def _validate_positive_int(value: int, *, field_name: str) -> int:
    """Проверяет положительное целое число.

    Args:
        value: Проверяемое значение.
        field_name: Имя поля для текста ошибки.

    Returns:
        Проверенное значение.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise KickCandidateDecisionError(f"{field_name} должен быть целым числом.")

    if value <= 0:
        raise KickCandidateDecisionError(f"{field_name} должен быть положительным числом.")

    return value


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.

    Raises:
        KickCandidateDecisionError: Если id отсутствует.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise KickCandidateDecisionError(f"{model_name} должен быть сохранён в БД.")


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "KickCandidateDecisionError",
    "KickCandidateDecisionRepository",
    "KickCandidateDecisionResult",
    "KickCandidateDecisionService",
    "KickCandidateInvalidTransitionError",
    "KickCandidateNotFoundError",
    "SqlAlchemyKickCandidateDecisionRepository",
]
