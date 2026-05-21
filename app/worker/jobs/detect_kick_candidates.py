"""Worker job обнаружения кандидатов на кик."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Clan, ClanMemberSnapshot, PlayerAccount, TelegramUser, Warning
from app.domain.enums import ClanType, WarningSource, WarningStatus
from app.services.kick_candidates import KickCandidateCreationResult, KickCandidateService
from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry

DETECT_KICK_CANDIDATES_JOB_NAME = "detect_kick_candidates"

_NO_CWL_SEASON_KEY = "no-cwl-season"
_TRACKED_CLAN_TYPES_FOR_PRESENCE = frozenset(
    {
        ClanType.MAIN.value,
        ClanType.ACADEMY.value,
        ClanType.FREEZER.value,
    }
)


@dataclass(frozen=True, slots=True)
class ImpactfulWarningsCandidate:
    """Данные TelegramUser с active impactful warn."""

    telegram_user: TelegramUser
    warnings: tuple[Warning, ...]
    season_key: str


@dataclass(frozen=True, slots=True)
class LinkedAccountPresence:
    """Текущее присутствие одного привязанного аккаунта."""

    player_tag: str
    is_current_in_tracked_clan: bool


@dataclass(frozen=True, slots=True)
class TelegramUserAccountPresence:
    """Текущее присутствие всех активных аккаунтов TelegramUser."""

    telegram_user: TelegramUser
    accounts: tuple[LinkedAccountPresence, ...]


class KickCandidateCreator(Protocol):
    """Contract сервиса создания кандидатов на кик."""

    async def create_for_two_impactful_warnings(
        self,
        *,
        telegram_user: TelegramUser,
        warnings: list[Warning],
        season_key: str,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата по двум active impactful warn.

        Args:
            telegram_user: TelegramUser-кандидат.
            warnings: Warn-записи пользователя.
            season_key: Ключ сезона для dedup.

        Returns:
            Результат создания кандидата.
        """

    async def create_for_all_accounts_left(
        self,
        *,
        telegram_user: TelegramUser,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата по уходу всех аккаунтов TelegramUser.

        Args:
            telegram_user: TelegramUser-кандидат.

        Returns:
            Результат создания кандидата.
        """

    async def create_for_linked_account_left(
        self,
        *,
        telegram_user: TelegramUser,
        player_tag: str,
    ) -> KickCandidateCreationResult:
        """Создаёт кандидата по уходу одного linked account.

        Args:
            telegram_user: TelegramUser владельца аккаунта.
            player_tag: Тег ушедшего аккаунта.

        Returns:
            Результат создания кандидата.
        """


class DetectKickCandidatesRepository(Protocol):
    """Repository contract для detect kick candidates job."""

    async def list_impactful_warning_candidates(self) -> tuple[ImpactfulWarningsCandidate, ...]:
        """Возвращает TelegramUser с active impactful system warn.

        Returns:
            Tuple кандидатов по warn.
        """

    async def list_account_presence_candidates(self) -> tuple[TelegramUserAccountPresence, ...]:
        """Возвращает присутствие активных linked accounts по TelegramUser.

        Returns:
            Tuple данных присутствия аккаунтов.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyDetectKickCandidatesRepository:
    """SQLAlchemy repository для detect kick candidates job."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_impactful_warning_candidates(self) -> tuple[ImpactfulWarningsCandidate, ...]:
        """Возвращает TelegramUser с active impactful system warn.

        Returns:
            Tuple кандидатов по warn.
        """
        result = await self._session.execute(
            select(TelegramUser, Warning)
            .join(Warning, Warning.telegram_user_id == TelegramUser.id)
            .where(
                Warning.status == WarningStatus.ACTIVE.value,
                Warning.source == WarningSource.SYSTEM.value,
                Warning.is_impactful.is_(True),
            )
            .order_by(TelegramUser.id, Warning.id)
        )

        warnings_by_user_id: dict[int, list[Warning]] = {}
        users_by_id: dict[int, TelegramUser] = {}
        for telegram_user, warning in result.all():
            telegram_user_id = _required_model_id(telegram_user, model_name="TelegramUser")
            users_by_id[telegram_user_id] = telegram_user
            warnings_by_user_id.setdefault(telegram_user_id, []).append(warning)

        candidates: list[ImpactfulWarningsCandidate] = []
        for telegram_user_id, warnings in warnings_by_user_id.items():
            if len(warnings) < 2:
                continue

            candidates.append(
                ImpactfulWarningsCandidate(
                    telegram_user=users_by_id[telegram_user_id],
                    warnings=tuple(warnings),
                    season_key=_resolve_warning_season_key(warnings),
                )
            )

        return tuple(candidates)

    async def list_account_presence_candidates(self) -> tuple[TelegramUserAccountPresence, ...]:
        """Возвращает присутствие active linked accounts в tracked clans.

        Returns:
            Tuple данных присутствия аккаунтов.
        """
        current_member_exists = (
            select(ClanMemberSnapshot.id)
            .join(Clan, Clan.id == ClanMemberSnapshot.clan_id)
            .where(
                ClanMemberSnapshot.player_tag == PlayerAccount.player_tag,
                ClanMemberSnapshot.is_current.is_(True),
                Clan.is_active.is_(True),
                Clan.type.in_(sorted(_TRACKED_CLAN_TYPES_FOR_PRESENCE)),
            )
            .exists()
        )
        result = await self._session.execute(
            select(
                TelegramUser,
                PlayerAccount,
                current_member_exists.label("is_current_in_tracked_clan"),
            )
            .join(PlayerAccount, PlayerAccount.telegram_user_id == TelegramUser.id)
            .where(
                PlayerAccount.is_active.is_(True),
                PlayerAccount.telegram_user_id.is_not(None),
            )
            .order_by(TelegramUser.id, PlayerAccount.player_tag)
        )

        accounts_by_user_id: dict[int, list[LinkedAccountPresence]] = {}
        users_by_id: dict[int, TelegramUser] = {}
        for telegram_user, account, is_current_in_tracked_clan in result.all():
            telegram_user_id = _required_model_id(telegram_user, model_name="TelegramUser")
            users_by_id[telegram_user_id] = telegram_user
            accounts_by_user_id.setdefault(telegram_user_id, []).append(
                LinkedAccountPresence(
                    player_tag=account.player_tag,
                    is_current_in_tracked_clan=bool(is_current_in_tracked_clan),
                )
            )

        return tuple(
            TelegramUserAccountPresence(
                telegram_user=users_by_id[telegram_user_id],
                accounts=tuple(accounts),
            )
            for telegram_user_id, accounts in accounts_by_user_id.items()
        )

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class DetectKickCandidatesJobResult:
    """Результат одного запуска detect kick candidates job."""

    warning_candidate_users_count: int
    warning_candidates_created_count: int
    warning_candidates_existing_count: int
    warning_candidates_skipped_count: int
    account_presence_users_count: int
    all_accounts_left_created_count: int
    all_accounts_left_existing_count: int
    linked_account_left_created_count: int
    linked_account_left_existing_count: int


class DetectKickCandidatesJob:
    """Job обнаружения кандидатов на кик по warn и уходу аккаунтов."""

    def __init__(
        self,
        *,
        repository: DetectKickCandidatesRepository,
        candidate_creator: KickCandidateCreator,
    ) -> None:
        """Инициализирует job.

        Args:
            repository: Repository для поиска кандидатов.
            candidate_creator: Сервис создания кандидатов на кик.
        """
        self._repository = repository
        self._candidate_creator = candidate_creator

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "DetectKickCandidatesJob":
        """Создаёт job поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенная job.
        """
        return cls(
            repository=SqlAlchemyDetectKickCandidatesRepository(session),
            candidate_creator=KickCandidateService.from_session(session=session),
        )

    async def run(self, context: WorkerJobContext) -> DetectKickCandidatesJobResult:
        """Создаёт candidates по warn и уходу linked accounts.

        Args:
            context: Runtime-контекст worker job.

        Returns:
            Сводка результата запуска.
        """
        warning_candidates = await self._repository.list_impactful_warning_candidates()
        account_presences = await self._repository.list_account_presence_candidates()

        warning_candidates_created_count = 0
        warning_candidates_existing_count = 0
        warning_candidates_skipped_count = 0
        all_accounts_left_created_count = 0
        all_accounts_left_existing_count = 0
        linked_account_left_created_count = 0
        linked_account_left_existing_count = 0

        for candidate in warning_candidates:
            if context.should_stop:
                break

            result = await self._candidate_creator.create_for_two_impactful_warnings(
                telegram_user=candidate.telegram_user,
                warnings=list(candidate.warnings),
                season_key=candidate.season_key,
            )
            if result.created:
                warning_candidates_created_count += 1
            elif result.candidate is not None:
                warning_candidates_existing_count += 1
            else:
                warning_candidates_skipped_count += 1

        for presence in account_presences:
            if context.should_stop:
                break

            current_accounts = tuple(
                account for account in presence.accounts if account.is_current_in_tracked_clan
            )
            left_accounts = tuple(
                account for account in presence.accounts if not account.is_current_in_tracked_clan
            )

            if not left_accounts:
                continue

            if not current_accounts:
                result = await self._candidate_creator.create_for_all_accounts_left(
                    telegram_user=presence.telegram_user,
                )
                if result.created:
                    all_accounts_left_created_count += 1
                elif result.candidate is not None:
                    all_accounts_left_existing_count += 1
                continue

            for account in left_accounts:
                result = await self._candidate_creator.create_for_linked_account_left(
                    telegram_user=presence.telegram_user,
                    player_tag=account.player_tag,
                )
                if result.created:
                    linked_account_left_created_count += 1
                elif result.candidate is not None:
                    linked_account_left_existing_count += 1

        await self._repository.flush()

        return DetectKickCandidatesJobResult(
            warning_candidate_users_count=len(warning_candidates),
            warning_candidates_created_count=warning_candidates_created_count,
            warning_candidates_existing_count=warning_candidates_existing_count,
            warning_candidates_skipped_count=warning_candidates_skipped_count,
            account_presence_users_count=len(account_presences),
            all_accounts_left_created_count=all_accounts_left_created_count,
            all_accounts_left_existing_count=all_accounts_left_existing_count,
            linked_account_left_created_count=linked_account_left_created_count,
            linked_account_left_existing_count=linked_account_left_existing_count,
        )


def register_detect_kick_candidates_job(registry: WorkerJobRegistry) -> None:
    """Регистрирует detect kick candidates job в worker registry.

    Args:
        registry: Registry worker jobs.
    """

    @registry.job(name=DETECT_KICK_CANDIDATES_JOB_NAME)
    async def detect_kick_candidates(context: WorkerJobContext) -> None:
        """Запускает обнаружение кандидатов на кик внутри worker scheduler.

        Args:
            context: Runtime-контекст worker job.

        Raises:
            RuntimeError: Если job запущена без DB session.
        """
        if context.session is None:
            raise RuntimeError("detect_kick_candidates требует DB session.")

        job = DetectKickCandidatesJob.from_session(session=context.session)
        await job.run(context)


def _resolve_warning_season_key(warnings: list[Warning]) -> str:
    """Возвращает season key для candidate по двум warn.

    Args:
        warnings: Active impactful warn пользователя.

    Returns:
        CWL season key из warn или fallback для warn без сезона.
    """
    season_keys = sorted(
        {
            warning.created_cwl_season_key.strip()
            for warning in warnings
            if warning.created_cwl_season_key is not None and warning.created_cwl_season_key.strip()
        }
    )
    if season_keys:
        return season_keys[-1]

    return _NO_CWL_SEASON_KEY


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise ValueError(f"{model_name} должен быть сохранён в БД.")


__all__ = [
    "DETECT_KICK_CANDIDATES_JOB_NAME",
    "DetectKickCandidatesJob",
    "DetectKickCandidatesJobResult",
    "DetectKickCandidatesRepository",
    "ImpactfulWarningsCandidate",
    "KickCandidateCreator",
    "LinkedAccountPresence",
    "SqlAlchemyDetectKickCandidatesRepository",
    "TelegramUserAccountPresence",
    "register_detect_kick_candidates_job",
]
