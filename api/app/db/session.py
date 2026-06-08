from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings


engine = create_async_engine(
    settings.app_db_url_async,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)


SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. Yields a DB session and ensures cleanup."""
    async with SessionLocal() as session:
        yield session