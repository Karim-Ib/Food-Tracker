from datetime import date, datetime, timezone, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.goals import GoalRead
from app.db.models import MealEntry, User
from app.schemas.meal_entries import (
    DailyTotals,
    DailyStatus,
    DayTotals,
    WeekResponse,
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

    @staticmethod
    def _macros_for(entry: MealEntry) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        """Scale a food's per-100g macros by the entry weight. Decimal throughout."""
        factor = entry.weight_g / Decimal(100)
        f = entry.food
        return (
            f.kcal_100g * factor,
            f.protein_100g * factor,
            f.fat_100g * factor,
            f.carbs_100g * factor,
        )

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

    async def list_for_week(self, user_id: int) -> WeekResponse:
        user = await self.session.get(User, user_id)
        if user is None:
            raise UserNotFound(f"No user with id={user_id}")

        tz = ZoneInfo(user.timezone)
        now_local = datetime.now(tz)
        today_local = now_local.date()
        monday = today_local - timedelta(days=today_local.weekday())

        # Local Monday 00:00 -> UTC lower bound. Upper bound is "now".
        start_utc = datetime.combine(monday, time.min, tzinfo=tz).astimezone(timezone.utc)
        end_utc = now_local.astimezone(timezone.utc)

        entries = await self.entries.list_for_range(user_id, start_utc, end_utc)

        # Bucket by LOCAL date. An entry at 00:30 local is the previous UTC day —
        # bucketing on UTC date would misfile it.
        buckets: dict[date, list[Decimal]] = {}
        for e in entries:
            if e.food is None:
                continue  # defensive: recipe entries don't exist yet
            local_day = e.consumed_at.astimezone(tz).date()
            kcal, protein, fat, carbs = self._macros_for(e)
            acc = buckets.setdefault(local_day, [Decimal(0)] * 4)
            acc[0] += kcal
            acc[1] += protein
            acc[2] += fat
            acc[3] += carbs

        # Spine: Monday through today inclusive. Partial week — never future days.
        span = today_local.weekday() + 1  # Mon=1 day, Wed=3, Sun=7
        days: list[DayTotals] = []
        week_k = week_p = week_f = week_c = Decimal(0)
        for i in range(span):
            d = monday + timedelta(days=i)
            k, p, f, c = buckets.get(d, [Decimal(0)] * 4)
            days.append(DayTotals(day=d, kcal=k, protein=p, fat=f, carbs=c))
            week_k += k
            week_p += p
            week_f += f
            week_c += c

        return WeekResponse(
            week_start=monday,
            days=days,
            totals=DailyTotals(kcal=week_k, protein=week_p, fat=week_f, carbs=week_c),
        )

    async def status_for_today(self, user_id: int) -> DailyStatus:
        user = await self.session.get(User, user_id)
        if user is None:
            raise UserNotFound(f"No user with id={user_id}")

        tz = ZoneInfo(user.timezone)
        today_local = datetime.now(tz).date()
        start_utc = datetime.combine(today_local, time.min, tzinfo=tz).astimezone(timezone.utc)
        end_utc = datetime.combine(
            today_local + timedelta(days=1), time.min, tzinfo=tz
        ).astimezone(timezone.utc)

        entries = await self.entries.list_for_range(user_id, start_utc, end_utc)

        k = p = f = c = Decimal(0)
        for e in entries:
            if e.food is None:
                continue
            ek, ep, ef, ec = self._macros_for(e)
            k += ek
            p += ep
            f += ef
            c += ec

        return DailyStatus(
            day=today_local,
            totals=DailyTotals(kcal=k, protein=p, fat=f, carbs=c),
            targets=GoalRead.model_validate(user),
        )

    async def create_entries(
            self, items: list[MealEntryCreate]
    ) -> list[MealEntry]:
        """Create several entries in one transaction — all succeed or none do."""
        now = datetime.now(timezone.utc)
        rows: list[dict] = []
        for item in items:
            fields = item.model_dump()
            if fields.get("consumed_at") is None:
                fields["consumed_at"] = now
            rows.append(fields)

        entries = await self.entries.create_many(rows)
        await self.session.commit()
        return entries