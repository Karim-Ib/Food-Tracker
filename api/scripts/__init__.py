"""
Seed the foods table with common pantry items.

Idempotent — re-running skips foods whose names already exist.

Run via:
    docker compose exec api python -m scripts.seed_foods
"""
import asyncio
from decimal import Decimal

from sqlalchemy import select

from app.db.enums import FoodSource, Visibility
from app.db.models import Food
from app.db.session import SessionLocal, engine


# (name, kcal_100g, protein_100g, fat_100g, carbs_100g)
# Real values per 100g raw weight unless noted. Source: USDA FoodData Central,
# rounded to 1 decimal.
SEED_FOODS = [
    ("Chicken breast, raw",      165, 31.0,   3.6,  0.0),
    ("Chicken breast, grilled",  195, 32.0,   4.5,  0.0),
    ("Salmon, raw",              208, 20.0,  13.0,  0.0),
    ("Egg, whole",               155, 13.0,  11.0,  1.1),
    ("Egg whites",                52, 11.0,   0.2,  0.7),
    ("White rice, cooked",       130,  2.7,   0.3, 28.0),
    ("Brown rice, cooked",       112,  2.3,   0.9, 24.0),
    ("Rolled oats, dry",         379, 13.0,   6.5, 68.0),
    ("Olive oil",                884,  0.0, 100.0,  0.0),
    ("Almonds",                  579, 21.0,  50.0, 22.0),
    ("Greek yogurt, plain",       59, 10.0,   0.4,  3.6),
    ("Banana",                    89,  1.1,   0.3, 23.0),
    ("Apple",                     52,  0.3,   0.2, 14.0),
    ("Whole grain bread",        247, 13.0,   4.2, 41.0),
    ("Pasta, dry",               371, 13.0,   1.5, 75.0),
    ("Broccoli, raw",             34,  2.8,   0.4,  7.0),
    ("Sweet potato, raw",         86,  1.6,   0.1, 20.0),
    ("Cheddar cheese",           403, 25.0,  33.0,  1.3),
    ("Avocado",                  160,  2.0,  15.0,  9.0),
    ("Peanut butter",            588, 25.0,  50.0, 20.0),
]


async def seed() -> None:
    async with SessionLocal() as session:
        # Idempotency: skip any name that already exists.
        existing_names = set(
            (await session.execute(select(Food.name))).scalars().all()
        )

        new_foods = [
            Food(
                name=name,
                kcal_100g=Decimal(str(kcal)),
                protein_100g=Decimal(str(p)),
                fat_100g=Decimal(str(f)),
                carbs_100g=Decimal(str(c)),
                source=FoodSource.SYSTEM,
                visibility=Visibility.PUBLIC,
            )
            for name, kcal, p, f, c in SEED_FOODS
            if name not in existing_names
        ]

        session.add_all(new_foods)
        await session.commit()

        skipped = len(SEED_FOODS) - len(new_foods)
        print(
            f"Seeded {len(new_foods)} foods. "
            f"Skipped {skipped} (already present)."
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())