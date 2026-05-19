"""Модели Лиги войн кланов."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, BaseModelMixin, IdMixin

if TYPE_CHECKING:
    from app.db.models.clans import Clan


class CwlSeason(BaseModelMixin, Base):
    """Сезон Лиги войн кланов для отслеживаемого клана."""

    __tablename__ = "cwl_seasons"

    clan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("clans.id"),
        nullable=False,
        index=True,
    )
    season: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    state: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    clan: Mapped["Clan"] = relationship("Clan")


class CwlWar(IdMixin, Base):
    """Конкретная война внутри раунда ЛВК."""

    __tablename__ = "cwl_wars"

    cwl_season_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cwl_seasons.id"),
        nullable=False,
        index=True,
    )
    round_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    war_tag: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        index=True,
    )
    state: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    our_clan_tag: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    opponent_clan_tag: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    end_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
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

    cwl_season: Mapped[CwlSeason] = relationship("CwlSeason")
