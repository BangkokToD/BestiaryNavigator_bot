"""Модели обычных клановых войн."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, BaseModelMixin, IdMixin

if TYPE_CHECKING:
    from app.db.models.clans import Clan


class WarSnapshot(BaseModelMixin, Base):
    """Snapshot текущей или последней известной войны клана."""

    __tablename__ = "war_snapshots"

    clan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("clans.id"),
        nullable=False,
        index=True,
    )
    war_tag: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    war_event_key: Mapped[str] = mapped_column(
        String(160),
        unique=True,
        nullable=False,
        index=True,
    )
    state: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    team_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    attacks_per_member: Mapped[int] = mapped_column(
        Integer,
        default=2,
        server_default=text("2"),
        nullable=False,
    )
    preparation_start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    end_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    opponent_tag: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    opponent_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    our_stars: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    opponent_stars: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    our_destruction: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("0"),
        server_default=text("0"),
        nullable=False,
    )
    opponent_destruction: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("0"),
        server_default=text("0"),
        nullable=False,
    )
    our_attacks: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    opponent_attacks: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    clan: Mapped["Clan"] = relationship("Clan")


class WarMember(IdMixin, Base):
    """Участник войны из snapshot-данных Clash API."""

    __tablename__ = "war_members"

    war_snapshot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("war_snapshots.id"),
        nullable=False,
        index=True,
    )
    side: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    player_tag: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    town_hall_level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    map_position: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    attacks_done: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    attacks_left: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )

    war_snapshot: Mapped[WarSnapshot] = relationship("WarSnapshot")


class WarAttack(IdMixin, Base):
    """Атака в обычной клановой войне."""

    __tablename__ = "war_attacks"

    war_snapshot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("war_snapshots.id"),
        nullable=False,
        index=True,
    )
    attacker_tag: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    defender_tag: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    stars: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    destruction_percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("0"),
        server_default=text("0"),
        nullable=False,
    )
    duration: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    war_snapshot: Mapped[WarSnapshot] = relationship("WarSnapshot")
