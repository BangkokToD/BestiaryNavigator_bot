"""Модели маршрутов и логов Telegram-уведомлений."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, BaseModelMixin, IdMixin

if TYPE_CHECKING:
    from app.db.models.clans import Clan
    from app.db.models.telegram import TelegramChat
    from app.db.models.users import TelegramUser


class NotificationRoute(BaseModelMixin, Base):
    """Маршрут уведомлений `клан + тип уведомления -> Telegram chat/topic`."""

    __tablename__ = "notification_routes"

    clan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("clans.id"),
        nullable=False,
        index=True,
    )
    notification_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.chat_id"),
        nullable=False,
        index=True,
    )
    message_thread_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        server_default=true(),
        default=True,
        nullable=False,
    )
    created_by_telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id"),
        nullable=True,
        index=True,
    )

    clan: Mapped["Clan"] = relationship("Clan")
    chat: Mapped["TelegramChat"] = relationship("TelegramChat")
    created_by_user: Mapped["TelegramUser | None"] = relationship("TelegramUser")


Index(
    "uq_notification_routes_clan_notification_chat_thread",
    NotificationRoute.clan_id,
    NotificationRoute.notification_type,
    NotificationRoute.chat_id,
    func.coalesce(NotificationRoute.message_thread_id, 0),
    unique=True,
)


class NotificationLog(IdMixin, Base):
    """Лог попытки отправки Telegram-уведомления."""

    __tablename__ = "notification_logs"

    route_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("notification_routes.id"),
        nullable=True,
        index=True,
    )
    notification_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    event_key: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.chat_id"),
        nullable=False,
        index=True,
    )
    message_thread_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    telegram_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    payload_summary: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )
    error_text: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    route: Mapped["NotificationRoute | None"] = relationship("NotificationRoute")
    chat: Mapped["TelegramChat"] = relationship("TelegramChat")
