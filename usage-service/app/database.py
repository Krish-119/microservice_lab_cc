"""
usage-service – database connection & session factory
"""
import asyncio
import logging
import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=10,
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db(retries: int = 30, delay: float = 1.0) -> None:
    for attempt in range(1, retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("usage-service: DB tables ready")
            return
        except Exception as exc:
            if attempt == retries:
                raise
            logger.warning("usage-service: DB not ready (attempt %d/%d): %s", attempt, retries, exc)
            await asyncio.sleep(delay)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
