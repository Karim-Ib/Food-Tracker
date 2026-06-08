from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import engine, get_session


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once at startup and once at shutdown."""
    # Startup — nothing to do yet. Will eventually verify migrations,
    # warm caches, etc.
    yield
    # Shutdown — close the engine cleanly so the connection pool is drained.
    await engine.dispose()


app = FastAPI(
    title="FoodBot API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness check — does not touch the database."""
    return {"status": "alive"}


@app.get("/health/db")
async def health_db(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    """Readiness check — confirms database connectivity."""
    result = await session.execute(text("SELECT 1"))
    value = result.scalar_one()
    return {"status": "ok", "db": "ok" if value == 1 else "unexpected"}