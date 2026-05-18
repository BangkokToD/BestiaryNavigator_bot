"""Слой доступа к базе данных."""

from app.db.base import Base, BaseModelMixin, IdMixin, TimestampMixin
from app.db.models import (
    Clan,
    ClanMemberSnapshot,
    CwlSeason,
    CwlWar,
    PlayerAccount,
    PlayerProfileSnapshot,
    RaidMember,
    RaidSeason,
    TelegramUser,
    WarAttack,
    WarMember,
    WarSnapshot,
)
from app.db.session import (
    create_db_engine,
    create_session_factory,
    get_db_session,
    get_engine,
    get_session_factory,
    run_in_transaction,
    transactional_session,
)

__all__ = [
    "Base",
    "BaseModelMixin",
    "Clan",
    "ClanMemberSnapshot",
    "CwlSeason",
    "CwlWar",
    "IdMixin",
    "PlayerAccount",
    "PlayerProfileSnapshot",
    "RaidMember",
    "RaidSeason",
    "TelegramUser",
    "TimestampMixin",
    "WarAttack",
    "WarMember",
    "WarSnapshot",
    "create_db_engine",
    "create_session_factory",
    "get_db_session",
    "get_engine",
    "get_session_factory",
    "run_in_transaction",
    "transactional_session",
]
