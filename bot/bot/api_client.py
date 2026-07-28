import httpx

from bot.config import settings


class APIClient:
    """Async wrapper around the FoodBot HTTP API.

    Owns a long-lived httpx.AsyncClient for connection pooling — one client
    instance reused across every handler invocation.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.api_base_url,
            timeout=settings.api_timeout_s,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict:
        """Liveness check — proves the API process is up."""
        response = await self._client.get("/health")
        response.raise_for_status()
        return response.json()

    async def get_user_by_telegram_id(self, telegram_id: int) -> dict | None:
        """Return user profile by Telegram ID, or None if no such user."""
        response = await self._client.get(
            f"/users/by-telegram-id/{telegram_id}"
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def create_user(self, telegram_id: int, display_name: str) -> dict:
        """Create a new user and return the resulting profile."""
        response = await self._client.post(
            "/users",
            json={
                "telegram_id": telegram_id,
                "display_name": display_name,
            },
        )
        response.raise_for_status()
        return response.json()

    async def search_foods(self, query: str, limit: int = 10) -> list[dict]:
        """Fuzzy-search foods by name. Returns a (possibly empty) list."""
        response = await self._client.get(
            "/foods/search",
            params={"q": query, "limit": limit},
        )
        response.raise_for_status()
        return response.json()

    async def create_meal_entry(
        self,
        user_id: int,
        food_id: int,
        weight_g: float,
    ) -> dict:
        """Create a meal entry of source_type='food'. Returns the saved entry."""
        response = await self._client.post(
            "/meal-entries",
            json={
                "user_id": user_id,
                "source_type": "food",
                "food_id": food_id,
                "weight_g": weight_g,
            },
        )
        response.raise_for_status()
        return response.json()

    async def parse_food(self, description: str) -> dict:
        """Call /foods/parse to turn free text into a ParsedFood dict."""
        response = await self._client.post(
            "/foods/parse",
            json={"description": description},
            timeout=httpx.Timeout(60.0, connect=5.0),
        )
        response.raise_for_status()
        return response.json()

    async def parse_meal(self, description: str) -> dict:
        """Call /foods/parse-meal to estimate a full meal (per-100g + portion)."""
        response = await self._client.post(
            "/foods/parse-meal",
            json={"description": description},
            timeout=httpx.Timeout(60.0, connect=5.0),
        )
        response.raise_for_status()
        return response.json()

    async def create_food_from_parse(
            self,
            parsed: dict,
            user_id: int,
    ) -> dict:
        """Save an LLM-parsed food to /foods with the server-side fields filled in."""
        payload = {
            **parsed,
            "source": "ai_estimated",
            "visibility": "private",
            "created_by_user_id": user_id,
        }
        response = await self._client.post("/foods", json=payload)
        response.raise_for_status()
        return response.json()

    async def get_food_by_barcode(self, barcode: str) -> dict:
        """Look up a food by exact barcode. Raises HTTPStatusError on 404."""
        response = await self._client.get(f"/foods/by-barcode/{barcode}")
        response.raise_for_status()
        return response.json()

    async def get_today(self, user_id: int) -> dict:
        """GET /meal-entries/today?user_id=…"""
        response = await self._client.get(
            "/meal-entries/today",
            params={"user_id": user_id},
        )
        response.raise_for_status()
        return response.json()

    async def create_body_metric(
            self,
            user_id: int,
            weight_kg: float | None = None,
            body_fat_pct: float | None = None,
            notes: str | None = None,
            is_seed: bool = False,
    ) -> dict:
        response = await self._client.post(
            "/body-metrics",
            json={
                "user_id": user_id,
                "weight_kg": weight_kg,
                "body_fat_pct": body_fat_pct,
                "notes": notes,
                "is_seed": is_seed,
            },
        )
        response.raise_for_status()
        return response.json()

    async def get_weight_model(self, user_id: int, horizon_days: int) -> dict:
        """Fit summary, trigger state, and target crossings for the weight trend."""
        response = await self._client.get(
            "/body-metrics/weight-model",
            params={"user_id": user_id, "horizon_days": horizon_days},
        )
        response.raise_for_status()
        return response.json()

    async def get_weight_model_chart(self, user_id: int, horizon_days: int) -> bytes:
        """The rendered trend chart as PNG bytes. Raises HTTPStatusError on 4xx."""
        response = await self._client.get(
            "/body-metrics/weight-model/chart.png",
            params={"user_id": user_id, "horizon_days": horizon_days},
            timeout=httpx.Timeout(60.0, connect=5.0),
        )
        response.raise_for_status()
        return response.content

    async def get_recent_body_metrics(self, user_id: int, limit: int = 10) -> list[dict]:
        response = await self._client.get(
            "/body-metrics/recent",
            params={"user_id": user_id, "limit": limit},
        )
        response.raise_for_status()
        return response.json()

    async def get_week(self, user_id: int) -> dict:
        response = await self._client.get("/meal-entries/week", params={"user_id": user_id})
        response.raise_for_status()
        return response.json()

    async def get_status(self, user_id: int) -> dict:
        response = await self._client.get("/meal-entries/status", params={"user_id": user_id})
        response.raise_for_status()
        return response.json()

    async def update_goal(self, user_id: int, field: str, value: float) -> dict:
        response = await self._client.patch(
            f"/users/{user_id}/goal",
            json={field: value},
        )
        response.raise_for_status()
        return response.json()

    async def create_meal_entries(self, entries: list[dict]) -> list[dict]:
        """Log several meal entries atomically. Each entry: {food_id, weight_g}."""
        response = await self._client.post(
            "/meal-entries/batch",
            json={"entries": entries},
        )
        response.raise_for_status()
        return response.json()

# Module-level singleton: reused across handlers, owns the connection pool.
client = APIClient()