from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Food


class FoodRepository:
    """Data access for the foods table.

    Methods return ORM objects (or None). No commits or rollbacks happen here
    — transaction control belongs to the service layer.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, food_id: int) -> Optional[Food]:
        result = await self.session.execute(
            select(Food).where(Food.id == food_id)
        )
        return result.scalar_one_or_none()

    async def create(self, **fields) -> Food:
        food = Food(**fields)
        self.session.add(food)
        await self.session.flush()
        return food

    async def search_by_name(
        self,
        query: str,
        limit: int = 10,
    ) -> list[Food]:
        """Fuzzy-match foods by name using pg_trgm trigram similarity.

        Returns matches with similarity > 0.15, ordered by similarity desc.
        Visibility / ownership filtering is intentionally absent — it lands
        in Phase 5 when user-created (private) foods become possible.
        """
        similarity = func.similarity(Food.name, query)
        stmt = (
            select(Food)
            .where(similarity > 0.15)
            .order_by(similarity.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_barcode(self, barcode: str) -> Optional[Food]:
        """Look up a food by exact barcode match. Uses the partial unique index."""
        result = await self.session.execute(
            select(Food).where(Food.barcode == barcode)
        )
        return result.scalar_one_or_none()