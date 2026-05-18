"""Модели игровых аккаунтов и snapshot-данных игроков."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    false,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, BaseModelMixin, IdMixin

if TYPE_CHECKING:
    from app.db.models.clans import Clan
    from app.db.models.users import TelegramUser


class PlayerAccount(BaseModelMixin, Base):
    """Подтверждённый игровой аккаунт после успешного verifytoken."""

    __tablename__ = "player_accounts"

    telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    player_tag: Mapped[str] = mapped_column(
        String(32),
        unique=True,
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        server_default=true(),
        default=True,
        nullable=False,
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    unlinked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_seen_clan_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("clans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    telegram_user: Mapped["TelegramUser | None"] = relationship(
        "TelegramUser",
        back_populates="accounts",
    )
    last_seen_clan: Mapped["Clan | None"] = relationship(
        "Clan",
        back_populates="player_accounts",
    )


class ClanMemberSnapshot(IdMixin, Base):
    """Snapshot участника клана, полученного из Clash API."""

    __tablename__ = "clan_member_snapshots"
    __table_args__ = (
        Index(
            "ix_clan_member_snapshots_clan_id_player_tag_snapshot_at",
            "clan_id",
            "player_tag",
            "snapshot_at",
        ),
    )

    clan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("clans.id", ondelete="CASCADE"),
        nullable=False,
    )
    player_tag: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    role: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    town_hall_level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    exp_level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    trophies: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    donations: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    donations_received: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    war_preference: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        server_default=false(),
        default=False,
        nullable=False,
    )

    clan: Mapped["Clan"] = relationship(
        "Clan",
        back_populates="member_snapshots",
    )


class PlayerProfileSnapshot(IdMixin, Base):
    """Snapshot профиля игрока, полученного из Clash API."""

    __tablename__ = "player_profile_snapshots"
    __table_args__ = (
        Index(
            "ix_player_profile_snapshots_player_tag_snapshot_at",
            "player_tag",
            "snapshot_at",
        ),
    )

    player_tag: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    town_hall_level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    town_hall_weapon_level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    exp_level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    trophies: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    best_trophies: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    war_stars: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    donations: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    donations_received: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    clan_capital_contributions: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    heroes_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )
    troops_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )
    spells_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )
    achievements_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )