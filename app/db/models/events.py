"""Модели истории событий игрока."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin

if TYPE_CHECKING:
    from app.db.models.users import TelegramUser


class PlayerEvent(IdMixin, Base):
    """Историческое событие игрока.

    Это не audit log. Модель хранит события, которые нужны для карточки игрока
    и принятия игровых решений.
    """

    __tablename__ = "player_events"

    telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id"),
        nullable=True,
        index=True,
    )
    player_tag: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    telegram_user: Mapped["TelegramUser | None"] = relationship("TelegramUser")
