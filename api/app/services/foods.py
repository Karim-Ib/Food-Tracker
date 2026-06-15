from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Food
from app.repositories.foods import FoodRepository
from app.schemas.foods import FoodCreate

from app.db.enums import FoodSource, Visibility
from app.services.openfoodfacts import off_client

class FoodNotFound(Exception):
    """Raised when a food lookup returns nothing."""
    pass


class DuplicateBarcode(Exception):
    """Raised when creating a food with a barcode that already exists."""
    pass


class FoodService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.foods = FoodRepository(session)

    async def create_food(self, data: FoodCreate) -> Food:
        try:
            food = await self.foods.create(**data.model_dump())
            await self.session.commit()
            return food
        except IntegrityError as exc:
            await self.session.rollback()
            if "uq_foods_barcode_notnull" in str(exc.orig):
                raise DuplicateBarcode(
                    f"Barcode {data.barcode!r} already exists"
                ) from exc
            raise

    async def get_food(self, food_id: int) -> Food:
        food = await self.foods.get_by_id(food_id)
        if food is None:
            raise FoodNotFound(f"No food with id={food_id}")
        return food

    async def search_foods(
        self,
        query: str,
        limit: int = 10,
    ) -> list[Food]:
        # No business logic yet — thin pass-through. Will grow a visibility
        # filter when user-created (private) foods land in Phase 5.
        return await self.foods.search_by_name(query=query, limit=limit)

    async def lookup_by_barcode(self, barcode: str) -> Food:
        """Look up a food by barcode: DB cache first, then OpenFoodFacts.

        On OFF hit, saves the result to the foods table for future cached lookups.
        Raises FoodNotFound if neither source has the barcode.
        """
        # Tier 1: local DB cache
        food = await self.foods.get_by_barcode(barcode)
        if food is not None:
            return food

        # Tier 2: OpenFoodFacts
        off_data = await off_client.lookup(barcode)
        if off_data is None:
            raise FoodNotFound(
                f"Barcode {barcode!r} not in DB or OpenFoodFacts"
            )

        # Save the OFF hit as a public food (no owner — everyone benefits)
        food = await self.foods.create(
            **off_data,
            source=FoodSource.OPENFOODFACTS,
            visibility=Visibility.PUBLIC,
            created_by_user_id=None,
        )
        await self.session.commit()
        return food