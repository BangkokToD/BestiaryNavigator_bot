"""Модель key-value настроек приложения."""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AppSetting(Base):
    """Key-value настройка приложения."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
    )
    value_json: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
