"""Async SQLAlchemy engine, session factory и transaction helpers."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import get_settings

type DatabaseOperation[T] = Callable[[AsyncSession], Awaitable[T]]


def create_db_engine(
    database_url: str,
    *,
    echo: bool = False,
    pool_pre_ping: bool = True,
) -> AsyncEngine:
    """Создаёт async SQLAlchemy engine.

    Args:
        database_url: SQLAlchemy URL подключения к PostgreSQL через asyncpg.
        echo: Флаг SQLAlchemy echo для отладочного вывода SQL.
        pool_pre_ping: Флаг проверки соединения перед выдачей из пула.

    Returns:
        Async SQLAlchemy engine.
    """
    return create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=pool_pre_ping,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Создаёт единую async session factory.

    Args:
        engine: Async SQLAlchemy engine.

    Returns:
        Фабрика `AsyncSession`.
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        expire_on_commit=False,
    )


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Возвращает кешированный async engine приложения.

    Returns:
        Async SQLAlchemy engine, созданный из `DATABASE_URL`.
    """
    settings = get_settings()
    return create_db_engine(settings.database_url)


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Возвращает кешированную session factory приложения.

    Returns:
        Единая фабрика `AsyncSession`.
    """
    return create_session_factory(get_engine())


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency для получения DB session.

    Yields:
        Async SQLAlchemy session.
    """
    session_factory = get_session_factory()

    async with session_factory() as session:
        yield session


@asynccontextmanager
async def transactional_session(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """Открывает session и транзакцию в одном context manager.

    Args:
        session_factory: Явная фабрика session для тестов или специальных сценариев.
            Если не передана, используется глобальная factory приложения.

    Yields:
        Async SQLAlchemy session внутри транзакции.
    """
    factory = session_factory or get_session_factory()

    async with factory() as session, session.begin():
        yield session


async def run_in_transaction[T](
    operation: DatabaseOperation[T],
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> T:
    """Выполняет async-операцию внутри DB-транзакции.

    Args:
        operation: Async callable, принимающий `AsyncSession`.
        session_factory: Явная фабрика session для тестов или специальных сценариев.

    Returns:
        Результат выполнения `operation`.
    """
    async with transactional_session(session_factory) as session:
        return await operation(session)