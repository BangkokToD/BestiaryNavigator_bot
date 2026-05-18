"""Модели Telegram-пользователей."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, String, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, BaseModelMixin

if TYPE_CHECKING:
    from app.db.models.players import PlayerAccount


class TelegramUser(BaseModelMixin, Base):
    """Telegram-пользователь системы.

    Пользователь может иметь несколько подтверждённых игровых аккаунтов.
    Админский статус кешируется из настроек, но источником истины остаётся
    `TELEGRAM_ADMIN_ID` из runtime-конфигурации.
    """

    __tablename__ = "telegram_users"

    telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
    )
    username: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    display_name: Mapped[str | None] = mapped_column(
        String(255),
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
    is_admin_cached: Mapped[bool] = mapped_column(
        Boolean,
        server_default=false(),
        default=False,
        nullable=False,
    )

    accounts: Mapped[list["PlayerAccount"]] = relationship(
        "PlayerAccount",
        back_populates="telegram_user",
    )