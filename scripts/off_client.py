"""
Open Food Facts Client — Rate-Limited Helper for Data Journalism

A self-contained Python module wrapping the openfoodfacts SDK with tiered
rate limiting, DataFrame conversion, and proper User-Agent handling.
Designed for workshop settings where IP bans from rate limit violations
would be disruptive.

Dependencies: openfoodfacts, pandas
Install: pip install openfoodfacts pandas

Usage:
    from off_client import OFFClient
    client = OFFClient(contact_email="you@example.com")

    # Search for products
    df = client.search_products("breakfast cereal", country="france")

    # Get a specific product
    product = client.get_product("3017620422003")

    # Compare multiple products
    df = client.compare_products(["3017620422003", "8000500310427"])

License: CC0 (public domain) — do whatever you want with this.
"""

import time
import warnings
from collections import defaultdict
from typing import Optional

import pandas as pd

try:
    import openfoodfacts
except ImportError:
    raise ImportError(
        "The openfoodfacts SDK is required. Install it with:\n"
        "  pip install openfoodfacts\n"
        "Requires Python 3.10 or higher."
    )

__version__ = "1.0.0"


# ---------------------------------------------------------------------------
# Rate limit configuration
# ---------------------------------------------------------------------------

# These are the documented OFF API rate limits. Exceeding them can result
# in IP bans, which would disrupt an entire workshop.
RATE_LIMITS = {
    "product": {"max_per_minute": 100, "min_delay_seconds": 0.6},
    "search": {"max_per_minute": 10, "min_delay_seconds": 6.0},
    "facet": {"max_per_minute": 2, "min_delay_seconds": 30.0},
}

# Warn the user when they've used this fraction of the per-minute budget
BUDGET_WARNING_THRESHOLD = 0.8

# Default fields to request when none are specified — keeps responses small
DEFAULT_PRODUCT_FIELDS = [
    "code",
    "product_name",
    "brands",
    "categories_tags",
    "countries_tags",
    "labels_tags",
    "nutriscore_grade",
    "nova_group",
    "ecoscore_grade",
    "nutriments",
    "ingredients_text",
    "allergens_tags",
    "additives_tags",
    "image_url",
]


# ---------------------------------------------------------------------------
# OFFClient
# ---------------------------------------------------------------------------


class OFFClient:
    """Rate-limited Open Food Facts API client for data journalism.

    Wraps the openfoodfacts Python SDK with automatic throttling across
    three API tiers (product, search, facet) to prevent IP bans.

    Args:
        contact_email: Your email for the User-Agent header (required by OFF).
        app_name: Application name for the User-Agent header.
        version: Application version for the User-Agent header.
        country: Default country for queries. Use ISO 2-letter codes or
            "world" for global. Defaults to "world".
    """

    def __init__(
        self,
        contact_email: str = "workshop@example.com",
        app_name: str = "OFFDataJournalism",
        version: str = "1.0",
        country: str = "world",
    ):
        user_agent = f"{app_name}/{version} ({contact_email})"
        self.api = openfoodfacts.API(
            user_agent=user_agent,
            country=country,
        )
        self.user_agent = user_agent
        self.default_country = country

        # Rate limiting state: track last request time and count per tier
        self._last_request: dict[str, float] = defaultdict(float)
        self._minute_counts: dict[str, list[float]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _rate_limit(self, tier: str) -> None:
        """Sleep if needed to respect the rate limit for the given tier.

        Also warns if approaching the per-minute budget.
        """
        config = RATE_LIMITS[tier]
        now = time.time()

        # Enforce minimum delay between consecutive requests
        elapsed = now - self._last_request[tier]
        if elapsed < config["min_delay_seconds"]:
            sleep_time = config["min_delay_seconds"] - elapsed
            time.sleep(sleep_time)

        # Track per-minute request count
        one_minute_ago = time.time() - 60
        self._minute_counts[tier] = [
            t for t in self._minute_counts[tier] if t > one_minute_ago
        ]
        count = len(self._minute_counts[tier])

        # Warn at 80% of budget
        limit = config["max_per_minute"]
        if count >= int(limit * BUDGET_WARNING_THRESHOLD):
            warnings.warn(
                f"Rate limit warning: {count}/{limit} {tier} requests in "
                f"the last minute. Approaching the limit.",
                stacklevel=3,
            )

        # If at the limit, wait until the oldest request expires
        if count >= limit:
            oldest = self._minute_counts[tier][0]
            wait = 60 - (time.time() - oldest) + 0.1
            if wait > 0:
                print(f"Rate limit reached for {tier} tier. Waiting {wait:.1f}s...")
                time.sleep(wait)

        self._minute_counts[tier].append(time.time())
        self._last_request[tier] = time.time()

    # ------------------------------------------------------------------
    # Product lookups
    # ------------------------------------------------------------------

    def get_product(
        self, barcode: str, fields: Optional[list[str]] = None
    ) -> dict:
        """Look up a single product by barcode.

        Args:
            barcode: The product barcode (EAN-13, UPC, etc.).
            fields: Specific fields to return. Defaults to a curated set
                of the most useful fields for analysis.

        Returns:
            Product data as a dictionary, or empty dict if not found.
        """
        self._rate_limit("product")
        result = self.api.product.get(
            barcode, fields=fields or DEFAULT_PRODUCT_FIELDS
        )
        if result is None:
            print(f"Product {barcode} not found.")
            return {}
        return result

    def compare_products(
        self, barcodes: list[str], fields: Optional[list[str]] = None
    ) -> pd.DataFrame:
        """Look up multiple products and return them as a DataFrame.

        Useful for side-by-side nutritional comparisons. Each barcode
        makes one product-tier API call (0.6s delay each).

        Args:
            barcodes: List of product barcodes to look up.
            fields: Specific fields to return.

        Returns:
            DataFrame with one row per product found.
        """
        products = []
        for barcode in barcodes:
            product = self.get_product(barcode, fields=fields)
            if product:
                products.append(product)

        if not products:
            return pd.DataFrame()

        return self._products_to_df(products)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_products(
        self,
        query: Optional[str] = None,
        country: Optional[str] = None,
        category: Optional[str] = None,
        brand: Optional[str] = None,
        nutriscore: Optional[str] = None,
        nova_group: Optional[int] = None,
        labels: Optional[str] = None,
        page_size: int = 50,
        max_pages: int = 1,
        fields: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        """Search for products with filters. Returns a DataFrame.

        Each page costs one search-tier API call (6s delay). Use max_pages
        cautiously — 5 pages means 30 seconds of waiting.

        Args:
            query: Free text search (e.g., "olive oil", "chocolate").
            country: Country filter (e.g., "france", "germany").
                Overrides the default country set in the constructor.
            category: Category tag filter (e.g., "breakfast-cereals").
            brand: Brand filter (e.g., "nestle").
            nutriscore: Nutri-Score grade filter ("a", "b", "c", "d", "e").
            nova_group: NOVA group filter (1, 2, 3, or 4).
            labels: Label filter (e.g., "organic", "fair-trade").
            page_size: Results per page (max 100, default 50).
            max_pages: Maximum pages to fetch (default 1). Each additional
                page adds a 6-second delay.
            fields: Specific fields to return.

        Returns:
            DataFrame with one row per product.
        """
        all_products = []
        page_size = min(page_size, 100)  # API maximum
        request_fields = ",".join(fields or DEFAULT_PRODUCT_FIELDS)

        for page in range(1, max_pages + 1):
            self._rate_limit("search")

            # Build query parameters for the v2 search API
            params: dict = {
                "fields": request_fields,
                "page_size": page_size,
                "page": page,
            }

            if query:
                params["search_terms"] = query
            if category:
                params["categories_tags_en"] = category.lstrip("en:")
            if brand:
                params["brands_tags"] = brand
            if nutriscore:
                params["nutriscore_grade"] = nutriscore
            if nova_group:
                params["nova_groups_tags"] = str(nova_group)
            if labels:
                params["labels_tags_en"] = labels.lstrip("en:")
            if country:
                params["countries_tags_en"] = country

            # Always use the v2 search API — the old /cgi/search.pl
            # endpoint is less reliable. The API can return 503 for broad
            # queries or during high load. We retry with backoff.
            import requests as req

            max_retries = 3
            for attempt in range(max_retries):
                resp = req.get(
                    "https://world.openfoodfacts.org/api/v2/search",
                    params=params,
                    headers={"User-Agent": self.user_agent},
                    timeout=30,
                )
                if resp.status_code != 503:
                    break
                if attempt == 0:
                    has_filters = any([category, brand, nutriscore, nova_group, labels, country])
                    if not has_filters:
                        print(
                            "Search returned 503. The OFF API often rejects "
                            "broad queries without filters. Try adding a "
                            "country= or category= filter."
                        )
                        return pd.DataFrame()
                wait = 5 * (attempt + 1)
                print(f"Server returned 503. Retrying in {wait}s (attempt {attempt + 2}/{max_retries})...")
                time.sleep(wait)

            resp.raise_for_status()
            result = resp.json()

            products = result.get("products", [])
            if not products:
                break
            all_products.extend(products)

            # Stop if we got fewer products than requested (last page)
            if len(products) < page_size:
                break

        if not all_products:
            print("No products found matching your search.")
            return pd.DataFrame()

        total = result.get("count", len(all_products))
        print(f"Found {total} total products. Retrieved {len(all_products)}.")
        return self._products_to_df(all_products)

    # ------------------------------------------------------------------
    # Category / facet queries
    # ------------------------------------------------------------------

    def get_category_products(
        self,
        category: str,
        country: Optional[str] = None,
        page_size: int = 50,
        max_pages: int = 1,
        fields: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        """Get products from a specific category.

        Uses the search endpoint with a category filter. This is a
        search-tier call (10/min limit).

        Args:
            category: Category name (e.g., "breakfast-cereals", "yogurts").
            country: Country filter (e.g., "france").
            page_size: Results per page (max 100).
            max_pages: Max pages to fetch.
            fields: Specific fields to return.

        Returns:
            DataFrame with one row per product.
        """
        return self.search_products(
            category=category,
            country=country,
            page_size=page_size,
            max_pages=max_pages,
            fields=fields,
        )

    # ------------------------------------------------------------------
    # Data processing utilities
    # ------------------------------------------------------------------

    def to_nutriment_df(self, data) -> pd.DataFrame:
        """Flatten nested nutriment data into a clean DataFrame.

        Takes either a list of product dicts or a DataFrame from
        search_products/compare_products and extracts the nutriment
        fields into flat columns.

        Args:
            data: List of product dicts, or a DataFrame with a
                'nutriments' column.

        Returns:
            DataFrame with flat nutriment columns (energy_kcal_100g,
            sugars_100g, etc.) alongside product identifiers.
        """
        if isinstance(data, pd.DataFrame):
            if "nutriments" not in data.columns:
                print("No 'nutriments' column found. Returning as-is.")
                return data
            products = data.to_dict("records")
        else:
            products = data

        rows = []
        for product in products:
            row = {
                "code": product.get("code", ""),
                "product_name": product.get("product_name", ""),
                "brands": product.get("brands", ""),
                "nutriscore_grade": product.get("nutriscore_grade", ""),
                "nova_group": product.get("nova_group", ""),
            }

            nutriments = product.get("nutriments", {})
            if isinstance(nutriments, dict):
                # Extract the _100g values, normalising field names
                for key, value in nutriments.items():
                    if key.endswith("_100g") and value is not None:
                        # Normalise: replace hyphens with underscores
                        clean_key = key.replace("-", "_")
                        try:
                            row[clean_key] = float(value)
                        except (ValueError, TypeError):
                            row[clean_key] = None

            rows.append(row)

        return pd.DataFrame(rows)

    @staticmethod
    def clean_tags(tags: list, strip_lang_prefix: bool = True) -> list[str]:
        """Clean taxonomy tags for display.

        Strips the language prefix (e.g., "en:") and replaces hyphens
        with spaces.

        Args:
            tags: List of taxonomy tag strings.
            strip_lang_prefix: Whether to remove the "en:" prefix.

        Returns:
            List of cleaned tag strings.
        """
        if not tags:
            return []
        cleaned = []
        for tag in tags:
            if not isinstance(tag, str):
                continue
            if strip_lang_prefix and ":" in tag:
                tag = tag.split(":", 1)[1]
            cleaned.append(tag.replace("-", " "))
        return cleaned

    @staticmethod
    def attribution_text() -> str:
        """Return the required ODbL attribution string.

        Include this in every chart footer, report conclusion, or data
        output to comply with the Open Database License.
        """
        return "Data: Open Food Facts (openfoodfacts.org), ODbL v1.0"

    def budget_report(self) -> str:
        """Show remaining API requests per tier in the current minute.

        Useful for planning multi-step analyses and understanding how
        much budget you have left.
        """
        lines = ["API budget (requests remaining in current minute):"]
        now = time.time()
        one_minute_ago = now - 60

        for tier, config in RATE_LIMITS.items():
            recent = [
                t for t in self._minute_counts[tier] if t > one_minute_ago
            ]
            used = len(recent)
            limit = config["max_per_minute"]
            remaining = limit - used
            lines.append(f"  {tier:>8}: {remaining}/{limit} remaining")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _products_to_df(self, products: list[dict]) -> pd.DataFrame:
        """Convert a list of product dicts to a flat DataFrame.

        Handles the common case where some fields are lists or dicts
        by converting them to strings for display.
        """
        rows = []
        for product in products:
            row = {}
            for key, value in product.items():
                if key == "nutriments":
                    # Keep as dict for to_nutriment_df; store as string in DF
                    row["nutriments"] = value
                elif isinstance(value, list):
                    row[key] = ", ".join(str(v) for v in value)
                elif isinstance(value, dict):
                    row[key] = str(value)
                else:
                    row[key] = value
            rows.append(row)

        return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Standalone demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Open Food Facts Client — Quick Demo")
    print("=" * 50)

    client = OFFClient(contact_email="demo@example.com")

    # Look up Nutella
    print("\n1. Looking up Nutella (barcode 3017620422003)...")
    product = client.get_product("3017620422003")
    if product:
        print(f"   Name: {product.get('product_name', 'N/A')}")
        print(f"   Brand: {product.get('brands', 'N/A')}")
        print(f"   Nutri-Score: {product.get('nutriscore_grade', 'N/A')}")
        print(f"   NOVA group: {product.get('nova_group', 'N/A')}")

    # Search for breakfast cereals in France
    print("\n2. Searching for breakfast cereals in France...")
    df = client.search_products(category="breakfast-cereals", country="france", page_size=5)
    if not df.empty:
        print(f"   Retrieved {len(df)} products")
        if "product_name" in df.columns:
            for name in df["product_name"].head():
                print(f"   - {name}")

    # Budget report
    print(f"\n3. {client.budget_report()}")

    # Attribution
    print(f"\n{client.attribution_text()}")
