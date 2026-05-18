"""Модели рейдов столицы клана."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin


class RaidSeason(IdMixin, Base):
    """Snapshot рейдового сезона столицы клана."""

    __tablename__ = "raid_seasons"

    clan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("clans.id"),
        nullable=False,
        index=True,
    )
    state: Mapped[str] = mapped_column(
        String(64),
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
    capital_total_loot: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    raids_completed: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    total_attacks: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    enemy_districts_destroyed: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    offensive_reward: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    defensive_reward: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class RaidMember(IdMixin, Base):
    """Участник рейдового сезона столицы клана."""

    __tablename__ = "raid_members"

    raid_season_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("raid_seasons.id"),
        nullable=False,
        index=True,
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
    attacks: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    project_expected_attacks: Mapped[int] = mapped_column(
        Integer,
        default=6,
        server_default=text("6"),
        nullable=False,
    )
    capital_resources_looted: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    raid_season: Mapped[RaidSeason] = relationship("RaidSeason")