from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MealEntry
from app.repositories.meal_entries import MealEntryRepository
from app.schemas.meal_entries import MealEntryCreate


class MealEntryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.entries = MealEntryRepository(session)

    async def create_entry(self, data: MealEntryCreate) -> MealEntry:
        fields = data.model_dump()

        # Server-side default for "user didn't specify a time"
        if fields.get("consumed_at") is None:
            fields["consumed_at"] = datetime.now(timezone.utc)

        entry = await self.entries.create(**fields)
        await self.session.commit()
        return entry