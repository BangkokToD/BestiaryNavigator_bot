"""Модели отслеживаемых кланов."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, BaseModelMixin

if TYPE_CHECKING:
    from app.db.models.players import ClanMemberSnapshot, PlayerAccount


class Clan(BaseModelMixin, Base):
    """Отслеживаемый клан Clash of Clans."""

    __tablename__ = "clans"

    tag: Mapped[str] = mapped_column(
        String(32),
        unique=True,
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    badge_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        server_default=true(),
        default=True,
        nullable=False,
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    sync_status: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    player_accounts: Mapped[list["PlayerAccount"]] = relationship(
        "PlayerAccount",
        back_populates="last_seen_clan",
    )
    member_snapshots: Mapped[list["ClanMemberSnapshot"]] = relationship(
        "ClanMemberSnapshot",
        back_populates="clan",
    )