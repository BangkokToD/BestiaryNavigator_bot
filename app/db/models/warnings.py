"""Модели warn и кандидатов на кик."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, BaseModelMixin

if TYPE_CHECKING:
    from app.db.models.clans import Clan
    from app.db.models.cwl import CwlSeason
    from app.db.models.users import TelegramUser


class Warning(BaseModelMixin, Base):
    """Warn, привязанный к Telegram-пользователю.

    Warn не привязывается к отдельному игровому аккаунту. Если у пользователя
    несколько игровых аккаунтов, запись warn остаётся одной, а затронутые
    аккаунты сохраняются в JSONB-полях.
    """

    __tablename__ = "warnings"

    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    reason_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    is_impactful: Mapped[bool] = mapped_column(
        Boolean,
        server_default=false(),
        default=False,
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    author_telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id"),
        nullable=True,
        index=True,
    )
    clan_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("clans.id"),
        nullable=True,
        index=True,
    )
    event_key: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
    )
    affected_player_tags_json: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )
    affected_player_names_json: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )
    created_cwl_season_key: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    active_until_cwl_season_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("cwl_seasons.id"),
        nullable=True,
        index=True,
    )
    expired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancelled_by_telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id"),
        nullable=True,
        index=True,
    )
    cancelled_reason: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )

    telegram_user: Mapped["TelegramUser"] = relationship(
        "TelegramUser",
        foreign_keys=[telegram_user_id],
    )
    author: Mapped["TelegramUser | None"] = relationship(
        "TelegramUser",
        foreign_keys=[author_telegram_user_id],
    )
    cancelled_by: Mapped["TelegramUser | None"] = relationship(
        "TelegramUser",
        foreign_keys=[cancelled_by_telegram_user_id],
    )
    clan: Mapped["Clan | None"] = relationship("Clan")
    active_until_cwl_season: Mapped["CwlSeason | None"] = relationship("CwlSeason")


class KickCandidate(BaseModelMixin, Base):
    """Кандидат на кик или удаление из Telegram-чата."""

    __tablename__ = "kick_candidates"

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
    reason_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    event_key: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
    )
    created_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id"),
        nullable=True,
        index=True,
    )
    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    decision_by_telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id"),
        nullable=True,
        index=True,
    )
    decision_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    decision_comment: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    telegram_user: Mapped["TelegramUser | None"] = relationship(
        "TelegramUser",
        foreign_keys=[telegram_user_id],
    )
    created_by_user: Mapped["TelegramUser | None"] = relationship(
        "TelegramUser",
        foreign_keys=[created_by],
    )
    decision_by_user: Mapped["TelegramUser | None"] = relationship(
        "TelegramUser",
        foreign_keys=[decision_by_telegram_user_id],
    )