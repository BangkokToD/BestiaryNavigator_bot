"""Smoke-тесты базового async DB-layer."""

import pytest
from sqlalchemy import DateTime, inspect
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.base import Base, BaseModelMixin
from app.db.session import (
    create_db_engine,
    create_session_factory,
    run_in_transaction,
    transactional_session,
)

TEST_DATABASE_URL = "postgresql+asyncpg://bn:bn@postgres:5432/bestiary"


class DbSmokeModel(BaseModelMixin, Base):
    """Тестовая модель для проверки базовых DB mixins."""

    __tablename__ = "db_smoke_model"


def test_base_model_mixin_declares_id_and_timezone_aware_timestamps() -> None:
    """Проверяет наличие id и timezone-aware timestamp-полей."""
    mapper = inspect(DbSmokeModel)
    columns = mapper.columns

    assert columns["id"].primary_key is True
    assert columns["created_at"].nullable is False
    assert columns["updated_at"].nullable is False

    created_at_type = columns["created_at"].type
    updated_at_type = columns["updated_at"].type

    assert isinstance(created_at_type, DateTime)
    assert isinstance(updated_at_type, DateTime)
    assert created_at_type.timezone is True
    assert updated_at_type.timezone is True


@pytest.mark.asyncio
async def test_create_db_engine_returns_async_engine() -> None:
    """Проверяет создание async engine без подключения к реальной БД."""
    engine = create_db_engine(TEST_DATABASE_URL)

    try:
        assert isinstance(engine, AsyncEngine)
        assert engine.url.drivername == "postgresql+asyncpg"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_session_factory_returns_async_sessionmaker() -> None:
    """Проверяет создание единой session factory."""
    engine = create_db_engine(TEST_DATABASE_URL)

    try:
        session_factory = create_session_factory(engine)

        assert session_factory.class_ is AsyncSession
        assert session_factory.kw["autoflush"] is False
        assert session_factory.kw["expire_on_commit"] is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_transactional_session_yields_async_session_without_db_io() -> None:
    """Проверяет transaction helper без SQL-запросов к реальной БД."""
    engine = create_db_engine(TEST_DATABASE_URL)
    session_factory = create_session_factory(engine)

    try:
        async with transactional_session(session_factory) as session:
            assert isinstance(session, AsyncSession)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_in_transaction_returns_operation_result() -> None:
    """Проверяет выполнение async-операции через transaction helper."""
    engine = create_db_engine(TEST_DATABASE_URL)
    session_factory = create_session_factory(engine)

    async def operation(session: AsyncSession) -> str:
        """Возвращает тестовый результат без SQL-запросов.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Строковый результат операции.
        """
        assert isinstance(session, AsyncSession)
        return "ok"

    try:
        result = await run_in_transaction(operation, session_factory)
    finally:
        await engine.dispose()

    assert result == "ok"
