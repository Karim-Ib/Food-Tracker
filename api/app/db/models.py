from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import EntrySource, FoodSource, Visibility


# ---- Reusable enum column types ----
# Defined once so they can be referenced from multiple columns if needed
# and so the Postgres enum type names stay consistent.

food_source_enum = Enum(
    FoodSource,
    name="food_source",
    schema="app",
    values_callable=lambda enum_cls: [e.value for e in enum_cls],
)

visibility_enum = Enum(
    Visibility,
    name="visibility",
    schema="app",
    values_callable=lambda enum_cls: [e.value for e in enum_cls],
)

entry_source_enum = Enum(
    EntrySource,
    name="entry_source",
    schema="app",
    values_callable=lambda enum_cls: [e.value for e in enum_cls],
)


# ---- Models ----

class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'Europe/Vienna'")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    daily_kcal_target: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 1))
    daily_protein_target_g: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 1))
    daily_fat_target_g: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 1))
    daily_carbs_target_g: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 1))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
class Food(Base):
    __tablename__ = "foods"
    __table_args__ = (
        CheckConstraint("kcal_100g >= 0", name="kcal_nonneg"),
        CheckConstraint("protein_100g >= 0", name="protein_nonneg"),
        CheckConstraint("fat_100g >= 0", name="fat_nonneg"),
        CheckConstraint("carbs_100g >= 0", name="carbs_nonneg"),
        CheckConstraint(
            "visibility <> 'private' OR created_by_user_id IS NOT NULL",
            name="private_requires_owner",
        ),
        # Partial unique index: only enforces uniqueness where barcode is not null
        Index(
            "uq_foods_barcode_notnull",
            "barcode",
            unique=True,
            postgresql_where=text("barcode IS NOT NULL"),
        ),
        # Trigram index for fuzzy text search on name
        Index(
            "ix_foods_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index("ix_foods_owner", "created_by_user_id", "visibility"),
        {"schema": "app"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[Optional[str]] = mapped_column(Text)
    barcode: Mapped[Optional[str]] = mapped_column(Text)

    kcal_100g: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    protein_100g: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    fat_100g: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    carbs_100g: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    fiber_100g: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    sugar_100g: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    sat_fat_100g: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))

    source: Mapped[FoodSource] = mapped_column(food_source_enum, nullable=False)
    visibility: Mapped[Visibility] = mapped_column(
        visibility_enum, nullable=False, server_default=text("'private'")
    )
    created_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("app.users.id", ondelete="SET NULL")
    )
    openfoodfacts_id: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Recipe(Base):
    __tablename__ = "recipes"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    override_total_weight_g: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"
    __table_args__ = (
        CheckConstraint("weight_g > 0", name="weight_positive"),
        Index("ix_recipe_ingredients_recipe", "recipe_id"),
        {"schema": "app"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.recipes.id", ondelete="CASCADE"), nullable=False
    )
    food_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.foods.id", ondelete="RESTRICT"), nullable=False
    )
    weight_g: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    position: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )

    recipe: Mapped["Recipe"] = relationship(back_populates="ingredients")


class MealEntry(Base):
    __tablename__ = "meal_entries"
    __table_args__ = (
        CheckConstraint("weight_g > 0", name="weight_positive"),
        CheckConstraint(
            "(source_type = 'food' AND food_id IS NOT NULL AND recipe_id IS NULL) "
            "OR (source_type = 'recipe' AND recipe_id IS NOT NULL AND food_id IS NULL)",
            name="entry_target_consistent",
        ),
        Index("ix_meal_entries_user_time", "user_id", "consumed_at"),
        {"schema": "app"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.users.id", ondelete="CASCADE"), nullable=False
    )
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_type: Mapped[EntrySource] = mapped_column(entry_source_enum, nullable=False)
    food_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("app.foods.id", ondelete="RESTRICT")
    )
    recipe_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("app.recipes.id", ondelete="RESTRICT")
    )
    weight_g: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    food: Mapped[Optional["Food"]] = relationship(lazy="raise")
    recipe: Mapped[Optional["Recipe"]] = relationship(lazy="raise")

class BodyMetric(Base):
    __tablename__ = "body_metrics"
    __table_args__ = (
        CheckConstraint("weight_kg IS NULL OR weight_kg > 0", name="weight_positive"),
        CheckConstraint(
            "body_fat_pct IS NULL OR (body_fat_pct >= 0 AND body_fat_pct <= 70)",
            name="body_fat_pct_range",
        ),
        Index("ix_body_metrics_user_time", "user_id", "recorded_at"),
        {"schema": "app"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.users.id", ondelete="CASCADE"), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    weight_kg: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    body_fat_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 2))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    # Remembered / estimated rather than stepped on a scale. Excluded from every
    # trend fit — the flag is the ONLY exclusion mechanism (never by date or value),
    # because a single unmeasured anchor point visibly drags the slope.
    is_seed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

