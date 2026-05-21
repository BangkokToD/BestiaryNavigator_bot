"""Worker job обнаружения непривязанных аккаунтов."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Clan, ClanMemberSnapshot, PlayerAccount
from app.domain.enums import ClanType
from app.services.kick_candidates import KickCandidateCreationResult, KickCandidateService
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

DETECT_UNLINKED_ACCOUNTS_JOB_NAME = "detect_unlinked_accounts"

_DETECT_UNLINKED_CLAN_TYPES = frozenset({ClanType.MAIN.value, ClanType.ACADEMY.value})


@dataclass(frozen=True, slots=True)
class UnlinkedAccountCandidate:
    """Данные непривязанного аккаунта для проверки кандидата на кик."""

    clan: Clan
    player_tag: str
    first_seen_at: datetime


class UnlinkedAccountCandidateCreator(Protocol):
    """Contract сервиса создания кандидатов по непривязанным аккаунтам."""

    async def create_for_unlinked_account(
        self,
        *,
        clan: Clan,
        player_tag: str,
        first_seen_at: datetime,
        observed_at: datetime | None = None,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата по непривязанному аккаунту.

        Args:
            clan: Клан, где найден непривязанный аккаунт.
            player_tag: Тег непривязанного аккаунта.
            first_seen_at: Первое появление аккаунта в API-составе.
            observed_at: Время текущей проверки.

        Returns:
            Результат создания кандидата.
        """


class DetectUnlinkedAccountsRepository(Protocol):
    """Repository contract для detect unlinked accounts job."""

    async def list_unlinked_current_members(self) -> tuple[UnlinkedAccountCandidate, ...]:
        """Возвращает текущие непривязанные аккаунты.

        Returns:
            Tuple данных непривязанных аккаунтов.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyDetectUnlinkedAccountsRepository:
    """SQLAlchemy repository для detect unlinked accounts job."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_unlinked_current_members(self) -> tuple[UnlinkedAccountCandidate, ...]:
        """Возвращает current members без активной Telegram-привязки.

        Returns:
            Tuple данных непривязанных аккаунтов.
        """
        linked_account_exists = (
            select(PlayerAccount.id)
            .where(
                PlayerAccount.player_tag == ClanMemberSnapshot.player_tag,
                PlayerAccount.is_active.is_(True),
                PlayerAccount.telegram_user_id.is_not(None),
            )
            .exists()
        )
        result = await self._session.execute(
            select(ClanMemberSnapshot, Clan)
            .join(Clan, Clan.id == ClanMemberSnapshot.clan_id)
            .where(
                ClanMemberSnapshot.is_current.is_(True),
                Clan.is_active.is_(True),
                Clan.type.in_(sorted(_DETECT_UNLINKED_CLAN_TYPES)),
                ~exists(linked_account_exists.select()),
            )
            .order_by(Clan.id, ClanMemberSnapshot.player_tag)
        )

        return tuple(
            UnlinkedAccountCandidate(
                clan=clan,
                player_tag=snapshot.player_tag,
                first_seen_at=snapshot.first_seen_at,
            )
            for snapshot, clan in result.all()
        )

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class DetectUnlinkedAccountsJobResult:
    """Результат одного запуска detect unlinked accounts job."""

    discovered_count: int
    created_count: int
    existing_count: int
    not_old_enough_count: int


class DetectUnlinkedAccountsJob:
    """Job обнаружения current accounts без Telegram-привязки."""

    def __init__(
        self,
        *,
        repository: DetectUnlinkedAccountsRepository,
        candidate_creator: UnlinkedAccountCandidateCreator,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository для поиска непривязанных current members.
            candidate_creator: Сервис создания кандидатов на кик.
            clock: Источник текущего времени для тестов.
        """
        self._repository = repository
        self._candidate_creator = candidate_creator
        self._clock = clock or _utc_now

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "DetectUnlinkedAccountsJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemyDetectUnlinkedAccountsRepository(session),
            candidate_creator=KickCandidateService.from_session(session=session),
        )

    async def run(self, context: WorkerJobContext) -> DetectUnlinkedAccountsJobResult:
        """Создаёт candidates по непривязанным current members старше 3 дней.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        candidates = await self._repository.list_unlinked_current_members()

        created_count = 0
        existing_count = 0
        not_old_enough_count = 0
        observed_at = self._clock()

        for candidate in candidates:
            if context.should_stop:
                break

            result = await self._candidate_creator.create_for_unlinked_account(
                clan=candidate.clan,
                player_tag=candidate.player_tag,
                first_seen_at=candidate.first_seen_at,
                observed_at=observed_at,
            )

            if result.created:
                created_count += 1
            elif result.candidate is not None:
                existing_count += 1
            else:
                not_old_enough_count += 1

        await self._repository.flush()

        return DetectUnlinkedAccountsJobResult(
            discovered_count=len(candidates),
            created_count=created_count,
            existing_count=existing_count,
            not_old_enough_count=not_old_enough_count,
        )


def register_detect_unlinked_accounts_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует detect unlinked accounts job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=DETECT_UNLINKED_ACCOUNTS_JOB_NAME)
    async def detect_unlinked_accounts(context: WorkerJobContext) -> None:
        """Запускает обнаружение непривязанных аккаунтов внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("detect_unlinked_accounts требует DB session.")

        job = DetectUnlinkedAccountsJob.from_session(session=context.session)
        await job.run(context)


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "DETECT_UNLINKED_ACCOUNTS_JOB_NAME",
    "DetectUnlinkedAccountsJob",
    "DetectUnlinkedAccountsJobResult",
    "DetectUnlinkedAccountsRepository",
    "SqlAlchemyDetectUnlinkedAccountsRepository",
    "UnlinkedAccountCandidate",
    "UnlinkedAccountCandidateCreator",
    "register_detect_unlinked_accounts_job",
]
