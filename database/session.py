"""
Async SQLAlchemy session factory for FastAPI dependency injection.

Provides:
  - async_engine        — shared asyncpg-backed engine
  - AsyncSessionLocal   — session factory
  - get_async_session() — FastAPI Depends() provider

The sync engine in app/database.py remains for Alembic migrations and
background scripts that run outside the async context.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config.settings import settings

# Build the async URL — convert psycopg2 dialect to asyncpg if needed
_raw_url = settings.database_url
if _raw_url.startswith("postgresql://") or _raw_url.startswith("postgresql+psycopg2://"):
    _async_url = _raw_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    _async_url = _async_url.replace("postgresql://",        "postgresql+asyncpg://")
else:
    _async_url = _raw_url   # already asyncpg

async_engine = create_async_engine(
    _async_url,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_pre_ping=settings.database_pool_pre_ping,
    pool_recycle=settings.database_pool_recycle,
    echo=settings.database_echo,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency — yields an AsyncSession and ensures it is closed
    after the request regardless of success or failure.

    Usage:
        @router.get("/example")
        async def handler(session: AsyncSession = Depends(get_async_session)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
