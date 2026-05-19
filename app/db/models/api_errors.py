"""Модель ошибок внешних API."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin


class ApiError(IdMixin, Base):
    """Краткий debug-контекст ошибки внешнего API."""

    __tablename__ = "api_errors"

    endpoint: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        index=True,
    )
    method: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    entity_type: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    entity_tag: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    status_code: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    message: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )
    response_snippet: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    exception_class: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    worker_name: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
