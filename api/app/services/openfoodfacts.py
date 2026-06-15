"""OpenFoodFacts API client for barcode-based nutrition lookup."""
import logging
from typing import Optional

import httpx

log = logging.getLogger(__name__)

OFF_BASE_URL = "https://world.openfoodfacts.org/api/v2"

# OFF stores nutriments under kebab-case-suffix keys. Map our normalized
# field names to the candidate keys in OFF's nutriments object.
NUTRIMENT_KEYS = {
    "kcal_100g": ["energy-kcal_100g"],
    "protein_100g": ["proteins_100g"],
    "fat_100g": ["fat_100g"],
    "carbs_100g": ["carbohydrates_100g"],
    "fiber_100g": ["fiber_100g"],
    "sugar_100g": ["sugars_100g"],
    "sat_fat_100g": ["saturated-fat_100g"],
}

# A product without these four is unusable for tracking.
REQUIRED = ("kcal_100g", "protein_100g", "fat_100g", "carbs_100g")


class OpenFoodFactsClient:
    """Async client for OpenFoodFacts barcode lookup."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=OFF_BASE_URL,
            timeout=10.0,
            headers={
                # OFF asks API consumers to identify themselves; good citizenship,
                # also reduces risk of being rate-limited as "anonymous traffic".
                "User-Agent": "FoodBot/0.1 (https://github.com/Karim-Ib/Food-Tracker)",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def lookup(self, barcode: str) -> Optional[dict]:
        """Fetch a product by barcode and return a normalized dict, or None.

        Returns None if:
          - OFF doesn't have the barcode (response status != 1)
          - The product is missing a name
          - The product lacks any of the required macros (kcal/protein/fat/carbs)
          - Network or HTTP error
        """
        try:
            response = await self._client.get(f"/product/{barcode}.json")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("OFF request failed for barcode %s: %s", barcode, exc)
            return None

        data = response.json()
        if data.get("status") != 1:
            return None

        product = data.get("product", {})
        nutriments = product.get("nutriments", {})

        name = product.get("product_name") or product.get("generic_name")
        if not name:
            return None

        out: dict = {
            "name": name.strip(),
            "barcode": barcode,
        }

        # OFF joins multiple brands with commas; we take the first one.
        brands = product.get("brands")
        if brands:
            out["brand"] = brands.split(",")[0].strip()

        for field, candidates in NUTRIMENT_KEYS.items():
            value = self._first_present(nutriments, candidates)
            if value is not None:
                try:
                    out[field] = float(value)
                except (TypeError, ValueError):
                    # Malformed value; skip rather than crash
                    pass

        # Reject products missing any required macro — better a clean miss
        # than a row with zeros that misleads the user later.
        for field in REQUIRED:
            if field not in out:
                log.info(
                    "OFF product %s lacks %s; treating as miss",
                    barcode, field,
                )
                return None

        return out

    @staticmethod
    def _first_present(
        nutriments: dict,
        keys: list[str],
    ) -> Optional[float]:
        for key in keys:
            if key in nutriments and nutriments[key] is not None:
                return nutriments[key]
        return None


# Module-level singleton — reuses HTTP client across requests.
off_client = OpenFoodFactsClient()