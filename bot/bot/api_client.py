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


# Module-level singleton: reused across handlers, owns the connection pool.
client = APIClient()