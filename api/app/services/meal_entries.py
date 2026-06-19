from datetime import datetime, timezone, date
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MealEntry, User
from app.schemas.meal_entries import (
    DailyTotals,
    MealEntryCreate,
    MealEntryTodayItem,
    TodayResponse,
)
from app.repositories.meal_entries import MealEntryRepository
from app.schemas.meal_entries import MealEntryCreate
from app.services.users import UserNotFound

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

    async def list_for_today(self, user_id: int) -> TodayResponse:
        """Compute the user's today: entries (joined with food) + macro totals."""
        user = await self.session.get(User, user_id)
        if user is None:
            raise UserNotFound(f"No user with id={user_id}")

        tz = ZoneInfo(user.timezone)
        today_local = datetime.now(tz).date()

        entries = await self.entries.list_for_user_on_date(
            user_id=user_id,
            target_date=today_local,
            timezone_name=user.timezone,
        )

        items: list[MealEntryTodayItem] = []
        total_kcal = total_protein = total_fat = total_carbs = Decimal(0)

        for entry in entries:
            if entry.food is None:
                # source_type='recipe' would land here; we don't have any yet
                continue

            factor = entry.weight_g / Decimal(100)
            kcal = entry.food.kcal_100g * factor
            protein = entry.food.protein_100g * factor
            fat = entry.food.fat_100g * factor
            carbs = entry.food.carbs_100g * factor

            items.append(
                MealEntryTodayItem(
                    id=entry.id,
                    consumed_at=entry.consumed_at,
                    weight_g=entry.weight_g,
                    food_name=entry.food.name,
                    kcal=kcal,
                    protein=protein,
                    fat=fat,
                    carbs=carbs,
                )
            )

            total_kcal += kcal
            total_protein += protein
            total_fat += fat
            total_carbs += carbs

        return TodayResponse(
            day=today_local,
            entries=items,
            totals=DailyTotals(
                kcal=total_kcal,
                protein=total_protein,
                fat=total_fat,
                carbs=total_carbs,
            ),
        )