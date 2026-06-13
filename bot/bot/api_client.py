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


# Module-level singleton: reused across handlers, owns the connection pool.
client = APIClient()