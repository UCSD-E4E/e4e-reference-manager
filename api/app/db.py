"""Async database engine, session factory, and dev-time schema bootstrap."""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings
from .models import Base

settings = get_settings()

engine = create_async_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Phase 0 bootstrap: ensure pgvector is available and create tables.

    Real schema migrations are managed by Alembic (see migrations/); create_all is a
    convenience for getting a dev instance running quickly.
    """
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
