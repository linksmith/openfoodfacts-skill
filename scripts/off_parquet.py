"""
Open Food Facts Parquet Client — DuckDB-based helper for fast local analysis

Queries the Open Food Facts parquet file (food.parquet) using DuckDB without
loading the full 7.5 GB dataset into RAM. Designed for:
  - Workshop settings with pre-downloaded data on local VMs
  - Investigative journalism analyses requiring sub-second query speeds
  - Filtering, aggregation, and export across 4.5M+ food products

Data source: https://huggingface.co/datasets/openfoodfacts/product-database
License: ODbL (Open Database License)

Dependencies: duckdb, pandas, requests, tqdm
Install: pip install duckdb pandas requests tqdm

Usage:
    from off_parquet import OFFParquet

    off = OFFParquet("data/food.parquet")    # path to local parquet file
    off.info()                               # quick dataset summary
    df = off.nova_distribution(country="en:france")
    df = off.top_additives(category="en:breakfast-cereals")
    df = off.query("SELECT brand, COUNT(*) FROM food GROUP BY 1 ORDER BY 2 DESC")
"""

import sys
import warnings
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd

__version__ = "2.1.0"

# ---------------------------------------------------------------------------
# Attribution (required by ODbL)
# ---------------------------------------------------------------------------

ATTRIBUTION = "Data: Open Food Facts (openfoodfacts.org), ODbL v1.0"

# HuggingFace direct download URL (7.47 GB — pre-download on workshop VMs)
PARQUET_URL = (
    "https://huggingface.co/datasets/openfoodfacts/product-database"
    "/resolve/main/food.parquet?download=true"
)

# Default data directory (relative to project root / working directory)
DEFAULT_DATA_DIR = "data"

# Standard European countries for cross-country analysis queries
EU_FOCUS_COUNTRIES = [
    "en:france", "en:germany", "en:netherlands", "en:spain",
    "en:italy", "en:belgium", "en:united-kingdom", "en:poland",
    "en:sweden", "en:denmark", "en:austria", "en:switzerland",
]

# ---------------------------------------------------------------------------
# Country-split parquet files
# ---------------------------------------------------------------------------
# The full food.parquet (7.5 GB) is split into smaller per-country files
# and hosted on Hetzner Object Storage. Use a country-specific file when:
#   - Workshop focus is on one or a few countries (faster, less RAM pressure)
#   - You want to compare countries without loading the full global dataset
#
# Retrieve files from S3 or the VM data directory: ~/data/openfoodfacts/
# S3 base URL: {OFF_S3_ENDPOINT}/{OFF_S3_BUCKET}/  (set in .env / cloud-config)
#
# Files available:
#   food_eu_all.parquet          — All EU-27 countries combined (deduped)
#   food_united-states.parquet   — United States
#   food_<slug>.parquet          — Per-country (EU + non-EU Europe)
#   manifest.json                — Row counts, sizes, generated timestamp
#
# Example usage:
#   off_fr = OFFParquet("data/food_france.parquet")
#   off_eu = OFFParquet("data/food_eu_all.parquet")
#   off_uk = OFFParquet("data/food_united-kingdom.parquet")

# Row count threshold below which a country file is considered low-data.
# When analysis is requested for a low-data country, warn the user that
# results may not be representative.
LOW_DATA_THRESHOLD = 5_000

# All country-specific files keyed by slug (OFF tag without "en:" prefix).
# Values are the filename relative to the data directory.
# This mirrors the output of scripts/split_parquet.py.
COUNTRY_FILES: dict[str, str] = {
    # EU combined
    "eu_all": "food_eu_all.parquet",
    # US
    "united-states": "food_united-states.parquet",
    # EU-27
    "austria":          "food_austria.parquet",
    "belgium":          "food_belgium.parquet",
    "bulgaria":         "food_bulgaria.parquet",
    "croatia":          "food_croatia.parquet",
    "cyprus":           "food_cyprus.parquet",
    "czech-republic":   "food_czech-republic.parquet",
    "denmark":          "food_denmark.parquet",
    "estonia":          "food_estonia.parquet",
    "finland":          "food_finland.parquet",
    "france":           "food_france.parquet",
    "germany":          "food_germany.parquet",
    "greece":           "food_greece.parquet",
    "hungary":          "food_hungary.parquet",
    "ireland":          "food_ireland.parquet",
    "italy":            "food_italy.parquet",
    "latvia":           "food_latvia.parquet",
    "lithuania":        "food_lithuania.parquet",
    "luxembourg":       "food_luxembourg.parquet",
    "malta":            "food_malta.parquet",
    "netherlands":      "food_netherlands.parquet",
    "poland":           "food_poland.parquet",
    "portugal":         "food_portugal.parquet",
    "romania":          "food_romania.parquet",
    "slovakia":         "food_slovakia.parquet",
    "slovenia":         "food_slovenia.parquet",
    "spain":            "food_spain.parquet",
    "sweden":           "food_sweden.parquet",
    # Non-EU Europe
    "albania":                "food_albania.parquet",
    "andorra":                "food_andorra.parquet",
    "armenia":                "food_armenia.parquet",
    "azerbaijan":             "food_azerbaijan.parquet",
    "belarus":                "food_belarus.parquet",
    "bosnia-and-herzegovina": "food_bosnia-and-herzegovina.parquet",
    "georgia":                "food_georgia.parquet",
    "iceland":                "food_iceland.parquet",
    "kosovo":                 "food_kosovo.parquet",
    "liechtenstein":          "food_liechtenstein.parquet",
    "moldova":                "food_moldova.parquet",
    "monaco":                 "food_monaco.parquet",
    "montenegro":             "food_montenegro.parquet",
    "north-macedonia":        "food_north-macedonia.parquet",
    "norway":                 "food_norway.parquet",
    "russia":                 "food_russia.parquet",
    "san-marino":             "food_san-marino.parquet",
    "serbia":                 "food_serbia.parquet",
    "switzerland":            "food_switzerland.parquet",
    "turkey":                 "food_turkey.parquet",
    "ukraine":                "food_ukraine.parquet",
    "united-kingdom":         "food_united-kingdom.parquet",
    "vatican-city":           "food_vatican-city.parquet",
}


def load_manifest(data_dir: str = DEFAULT_DATA_DIR) -> dict:
    """Load manifest.json from the split data directory, if present.

    Returns a dict keyed by country slug with keys: rows, size_bytes, file.
    Returns an empty dict if manifest.json is not found.
    """
    import json
    p = Path(data_dir) / "manifest.json"
    if p.exists():
        data = json.loads(p.read_text())
        return data.get("files", {})
    return {}


# Additives of journalistic interest (EU-relevant controversies)
ADDITIVES_OF_CONCERN = {
    # Banned / controversial
    "en:e171": "Titanium dioxide (banned EU 2022) ⚠",
    "en:e621": "MSG / Monosodium glutamate ⚠",
    "en:e951": "Aspartame ⚠",
    "en:e150d": "Sulphite ammonia caramel ⚠",
    "en:e320": "BHA / Butylated hydroxyanisole ⚠",
    "en:e407": "Carrageenan ⚠",
    # Azo dyes (children's hyperactivity)
    "en:e102": "Tartrazine (azo dye, hyperactivity) ⚠",
    "en:e110": "Sunset Yellow FCF (azo dye) ⚠",
    "en:e122": "Carmoisine / Azorubine (azo dye) ⚠",
    "en:e124": "Ponceau 4R (azo dye) ⚠",
    "en:e129": "Allura Red (azo dye) ⚠",
    "en:e211": "Sodium benzoate ⚠",
    # Common (not controversial but frequent in ultra-processed foods)
    "en:e471": "Mono- and diglycerides of fatty acids",
    "en:e412": "Guar gum",
    "en:e415": "Xanthan gum",
    "en:e322": "Lecithins (soy/sunflower)",
    "en:e330": "Citric acid",
    "en:e500": "Sodium carbonates",
    "en:e501": "Potassium carbonates",
    "en:e503": "Ammonium carbonates",
    "en:e306": "Tocopherol-rich extract (natural vitamin E)",
    "en:e307": "Alpha-tocopherol (synthetic vitamin E)",
    "en:e160a": "Carotenes",
    "en:e160c": "Paprika extract",
    "en:e100": "Curcumin",
    "en:e150a": "Plain caramel",
    "en:e150c": "Ammonia caramel",
    "en:e420": "Sorbitol",
    "en:e421": "Mannitol",
    "en:e950": "Acesulfame K",
    "en:e952": "Cyclamates",
    "en:e955": "Sucralose",
    "en:e960": "Steviol glycosides (stevia)",
    "en:e420i": "Sorbitol",
    "en:e481": "Sodium stearoyl-2-lactylate",
    "en:e482": "Calcium stearoyl-2-lactylate",
    "en:e1422": "Acetylated distarch adipate",
    "en:e1442": "Hydroxy propyl distarch phosphate",
    "en:e1420": "Acetylated starch",
    "en:e451": "Triphosphates",
    "en:e452": "Polyphosphates",
    "en:e340": "Potassium phosphates",
}


# ---------------------------------------------------------------------------
# OFFParquet
# ---------------------------------------------------------------------------


class OFFParquet:
    """DuckDB-based client for querying the Open Food Facts parquet file.

    Connects DuckDB directly to the parquet file — no data is loaded into
    RAM. A 4.5 million row query typically completes in under 1 second.

    Args:
        parquet_path: Path to food.parquet. Searches common locations if not
            found. Pass None to skip auto-detection.
        verbose: Print query timings and row counts. Default True.
    """

    def __init__(
        self,
        parquet_path: str = "food.parquet",
        verbose: bool = True,
    ):
        self.verbose = verbose
        self.path = self._resolve_path(parquet_path)
        self.con = duckdb.connect(":memory:")

        # Register parquet as a DuckDB view — no data loaded into RAM
        self.con.execute(
            f"CREATE OR REPLACE VIEW food AS "
            f"SELECT * FROM read_parquet('{self.path}')"
        )

        # Detect schema once
        self._schema = self._get_schema()
        self._nutriment_mode = self._detect_nutriment_mode()

        if self.verbose:
            row_count = self.con.execute("SELECT COUNT(*) FROM food").fetchone()[0]
            print(f"✓ Connected: {self.path.name}")
            print(f"  {row_count:,} products · {self._nutriment_mode} nutriments")
            print(f"  {ATTRIBUTION}")

    # ------------------------------------------------------------------
    # Setup / download helpers
    # ------------------------------------------------------------------

    @staticmethod
    def download(
        target_path: str = "food.parquet",
        url: str = PARQUET_URL,
    ) -> Path:
        """Download the Open Food Facts parquet file with a progress bar.

        The file is ~7.5 GB. Pre-download this on workshop VMs before the
        session. On a 100 Mbit connection, download takes ~10 minutes.

        Args:
            target_path: Where to save the file. Default: ./food.parquet
            url: HuggingFace download URL.

        Returns:
            Path to the downloaded file.

        Example:
            OFFParquet.download("data/food.parquet")
        """
        try:
            import requests
            from tqdm import tqdm
        except ImportError:
            raise ImportError(
                "Download requires: pip install requests tqdm"
            )

        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        print(f"Downloading Open Food Facts parquet (~7.5 GB) to {target}")
        print(f"Source: {url}")

        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            with open(target, "wb") as f, tqdm(
                total=total,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=target.name,
            ) as bar:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    bar.update(len(chunk))

        size_gb = target.stat().st_size / 1e9
        print(f"✓ Saved {size_gb:.2f} GB to {target}")
        return target

    # ------------------------------------------------------------------
    # Schema inspection
    # ------------------------------------------------------------------

    def _resolve_path(self, parquet_path: str) -> Path:
        """Find the parquet file, checking data/ first then fallbacks."""
        given = Path(parquet_path)

        # If the path is explicit and exists, use it directly
        if given.exists():
            return given.resolve()

        # If only a filename was given (no directory component), try data/
        if given.parent == Path("."):
            in_data = Path(DEFAULT_DATA_DIR) / given.name
            if in_data.exists():
                return in_data.resolve()

        # Fallback search order (for plain "food.parquet" as default arg)
        name = given.name
        candidates = [
            given,
            Path(DEFAULT_DATA_DIR) / name,
            Path("..") / DEFAULT_DATA_DIR / name,
            Path.home() / "data" / "openfoodfacts" / name,  # VM standard location
            Path.home() / "data" / name,
            Path.home() / DEFAULT_DATA_DIR / name,
            Path.home() / name,
        ]
        for p in candidates:
            if p.exists():
                return p.resolve()

        raise FileNotFoundError(
            f"'{given.name}' not found. Checked:\n"
            + "\n".join(f"  {p.resolve()}" for p in candidates)
            + f"\n\nExpected location: {Path(DEFAULT_DATA_DIR) / given.name}"
            "\n\nRun the setup script to download the parquet:\n"
            "  python scripts/download_data.py"
        )

    def _get_schema(self) -> dict:
        """Return {column_name: column_type} for all columns."""
        result = self.con.execute("DESCRIBE food").df()
        return dict(zip(result["column_name"], result["column_type"]))

    def _detect_nutriment_mode(self) -> str:
        """Detect whether nutriments are in a STRUCT column or top-level."""
        if "nutriments" in self._schema:
            dtype = self._schema["nutriments"]
            if "STRUCT" in dtype.upper():
                return "struct"
            return "json"
        # Check if flattened (some parquet exports)
        if "energy_kcal_100g" in self._schema or "sugars_100g" in self._schema:
            return "flat"
        return "unknown"

    def schema(self) -> pd.DataFrame:
        """Return the full column schema as a DataFrame.

        Use this to discover available columns and their types. Call this
        first when starting an investigation to understand the data.
        """
        return self.con.execute("DESCRIBE food").df()

    def sample(self, n: int = 3) -> pd.DataFrame:
        """Return a small random sample of products for schema exploration."""
        return self.con.execute(
            f"SELECT * FROM food USING SAMPLE {n}"
        ).df()

    # ------------------------------------------------------------------
    # Core query interface
    # ------------------------------------------------------------------

    def query(self, sql: str) -> pd.DataFrame:
        """Execute raw DuckDB SQL and return a pandas DataFrame.

        The view is named `food`. Use standard SQL with DuckDB extensions.

        Args:
            sql: SQL query. Use `food` as the table name.

        Returns:
            pandas DataFrame with query results.

        Examples:
            off.query("SELECT COUNT(*) FROM food")

            off.query('''
                SELECT
                    YEAR(to_timestamp(created_t)) AS year,
                    COUNT(*) AS products_added
                FROM food
                WHERE created_t IS NOT NULL
                GROUP BY 1 ORDER BY 1
            ''')

            off.query('''
                SELECT brands, COUNT(*) AS count,
                    COUNT(*) FILTER (WHERE nutriscore_grade = 'a') AS grade_a
                FROM food
                WHERE list_contains(countries_tags, 'en:france')
                  AND nutriscore_grade IS NOT NULL
                GROUP BY brands
                HAVING count > 20
                ORDER BY count DESC
                LIMIT 20
            ''')
        """
        import time
        t0 = time.time()
        df = self.con.execute(sql).df()
        elapsed = time.time() - t0
        if self.verbose:
            print(f"  {len(df):,} rows · {elapsed:.3f}s")
        return df

    # ------------------------------------------------------------------
    # Dataset overview
    # ------------------------------------------------------------------

    def info(self) -> pd.DataFrame:
        """Print a quick overview of the dataset and return a summary DataFrame.

        Shows total products, coverage by country, nutriscore/NOVA coverage,
        and most common languages.
        """
        summary = self.con.execute("""
            SELECT
                COUNT(*)                                           AS total_products,
                COUNT(*) FILTER (WHERE nutriscore_grade IS NOT NULL) AS has_nutriscore,
                COUNT(*) FILTER (WHERE nova_group IS NOT NULL)     AS has_nova_group,
                COUNT(*) FILTER (WHERE environmental_score_grade IS NOT NULL) AS has_ecoscore,
                COUNT(DISTINCT brands)                             AS distinct_brands,
                COUNT(DISTINCT lang)                               AS distinct_languages,
                MIN(to_timestamp(created_t))::DATE                AS oldest_product,
                MAX(to_timestamp(created_t))::DATE                AS newest_product
            FROM food
        """).df()

        print("\n=== Open Food Facts — Dataset Summary ===")
        row = summary.iloc[0]
        total = row["total_products"]
        print(f"  Total products:   {total:>10,}")
        print(f"  Has Nutri-Score:  {row['has_nutriscore']:>10,}  ({100*row['has_nutriscore']/total:.1f}%)")
        print(f"  Has NOVA group:   {row['has_nova_group']:>10,}  ({100*row['has_nova_group']/total:.1f}%)")
        print(f"  Has Eco-Score:    {row['has_ecoscore']:>10,}  ({100*row['has_ecoscore']/total:.1f}%)")
        print(f"  Distinct brands:  {row['distinct_brands']:>10,}")
        print(f"  Languages:        {row['distinct_languages']:>10,}")
        print(f"  Date range:       {row['oldest_product']} → {row['newest_product']}")
        print(f"\n  {ATTRIBUTION}")
        return summary

    def warn_if_thin(
        self,
        country_slug: str,
        data_dir: str = DEFAULT_DATA_DIR,
        *,
        threshold: int = LOW_DATA_THRESHOLD,
    ) -> bool:
        """Check if a country file has low data coverage and warn the user.

        Use this before running analysis on a country-specific parquet file
        to alert users when results may not be representative.

        Args:
            country_slug: Country slug as used in COUNTRY_FILES
                          (e.g. "malta", "luxembourg", "kosovo")
            data_dir: Directory where manifest.json lives. Default: "data"
            threshold: Minimum rows to be considered well-covered. Default: 5,000

        Returns:
            True if the country has low data (warning issued), False otherwise.
        """
        manifest = load_manifest(data_dir)
        entry = manifest.get(country_slug)
        if entry is None:
            # No manifest — query the file directly
            try:
                rows = self.con.execute("SELECT COUNT(*) FROM food").fetchone()[0]
            except Exception:
                return False
            if rows < threshold:
                print(
                    f"⚠ WARNING: This file contains only {rows:,} products for "
                    f"'{country_slug}'. Results may not be statistically representative. "
                    f"Consider using food_eu_all.parquet or food.parquet for broader analysis."
                )
                return True
            return False

        rows = entry.get("rows", 0)
        if rows < threshold:
            size_mb = entry.get("size_bytes", 0) / 1e6
            print(
                f"⚠ WARNING: '{country_slug}' has only {rows:,} products in Open Food Facts "
                f"(file: {entry.get('file', '?')}, {size_mb:.1f} MB). "
                f"Results may not be statistically representative. "
                f"Flag this caveat clearly in any published analysis."
            )
            return True
        return False

    def country_coverage(self, top_n: int = 20) -> pd.DataFrame:
        """Return product counts for the top countries.

        Note: products can be tagged for multiple countries, so counts
        sum to more than the total product count.
        """
        return self.query(f"""
            SELECT
                unnested_country                         AS country_tag,
                COUNT(*)                                 AS product_count,
                ROUND(100.0 * COUNT(*) / (
                    SELECT COUNT(*) FROM food
                ), 2)                                    AS pct_of_total
            FROM (
                SELECT UNNEST(countries_tags) AS unnested_country
                FROM food
                WHERE countries_tags IS NOT NULL
            ) t
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT {top_n}
        """)

    # ------------------------------------------------------------------
    # NOVA group analysis
    # ------------------------------------------------------------------

    def nova_distribution(
        self,
        country: Optional[str] = None,
        category: Optional[str] = None,
        min_products: int = 100,
    ) -> pd.DataFrame:
        """NOVA group distribution (food processing levels 1-4).

        NOVA 4 = ultra-processed. Journalistically the most interesting.

        Args:
            country: Filter by country tag (e.g. "en:france"). None = global.
            category: Filter by category tag (e.g. "en:breakfast-cereals").
            min_products: Only include results with at least this many products
                          (avoids misleading percentages from tiny samples).

        Returns:
            DataFrame with columns: nova_group, count, pct

        Example:
            off.nova_distribution(country="en:france", category="en:yogurts")
        """
        filters = self._build_filters(country, category)
        where = f"WHERE nova_group IS NOT NULL" + (
            f" AND {filters}" if filters else ""
        )

        df = self.query(f"""
            SELECT
                nova_group::INTEGER       AS nova_group,
                COUNT(*)                  AS count,
                ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
            FROM food
            {where}
            GROUP BY 1
            ORDER BY 1
        """)

        if df.empty or df["count"].sum() < min_products:
            warnings.warn(
                f"Only {df['count'].sum() if not df.empty else 0} products found with "
                f"NOVA data for these filters. Results may not be representative."
            )

        # Add human-readable labels
        nova_labels = {
            1: "Unprocessed / minimally processed",
            2: "Processed culinary ingredients",
            3: "Processed foods",
            4: "Ultra-processed (NOVA 4)",
        }
        df["nova_label"] = df["nova_group"].map(nova_labels)
        return df

    def nova_by_category(
        self,
        country: Optional[str] = None,
        min_products: int = 200,
        top_n: int = 20,
    ) -> pd.DataFrame:
        """NOVA 4 (ultra-processed) percentage by product category.

        Returns the categories with the highest share of NOVA 4 products.

        Args:
            country: Filter by country tag.
            min_products: Minimum products per category to include.
            top_n: Return top N categories by NOVA 4 share.
        """
        country_filter = ""
        if country:
            country_filter = f"AND list_contains(countries_tags, '{country}')"

        return self.query(f"""
            SELECT
                category,
                COUNT(*)                                AS total,
                COUNT(*) FILTER (WHERE nova_group = 4)  AS nova4_count,
                ROUND(100.0 * COUNT(*) FILTER (WHERE nova_group = 4) / COUNT(*), 1)
                                                        AS nova4_pct
            FROM (
                SELECT
                    nova_group,
                    countries_tags,
                    UNNEST(categories_tags) AS category
                FROM food
                WHERE nova_group IS NOT NULL
                  AND categories_tags IS NOT NULL
                  {country_filter}
            ) t
            WHERE category LIKE 'en:%'
            GROUP BY category
            HAVING total >= {min_products}
            ORDER BY nova4_pct DESC
            LIMIT {top_n}
        """)

    # ------------------------------------------------------------------
    # Nutri-Score analysis
    # ------------------------------------------------------------------

    def nutriscore_distribution(
        self,
        country: Optional[str] = None,
        category: Optional[str] = None,
        brand: Optional[str] = None,
    ) -> pd.DataFrame:
        """Nutri-Score grade distribution (a=best, e=worst).

        Args:
            country: Filter by country tag (e.g. "en:germany").
            category: Filter by category tag.
            brand: Filter by brand name (case-insensitive substring match).

        Returns:
            DataFrame: grade, count, pct
        """
        filters = self._build_filters(country, category, brand)
        where = "WHERE nutriscore_grade IS NOT NULL"
        if filters:
            where += f" AND {filters}"

        return self.query(f"""
            SELECT
                LOWER(nutriscore_grade)  AS grade,
                COUNT(*)                 AS count,
                ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
            FROM food
            {where}
            GROUP BY 1
            ORDER BY 1
        """)

    def nutriscore_by_country(
        self,
        countries: Optional[list] = None,
        category: Optional[str] = None,
        min_products: int = 500,
    ) -> pd.DataFrame:
        """Compare Nutri-Score distributions across multiple countries.

        Useful for cross-border comparison stories (e.g. "are French
        breakfast cereals healthier than German ones?").

        Args:
            countries: List of country tags. Defaults to EU_FOCUS_COUNTRIES.
            category: Optional category filter.
            min_products: Minimum products per country to include.

        Returns:
            DataFrame with one row per (country, grade) combination.
        """
        countries = countries or EU_FOCUS_COUNTRIES
        countries_sql = ", ".join(f"'{c}'" for c in countries)
        cat_filter = (
            f"AND list_contains(categories_tags, '{category}')"
            if category else ""
        )

        return self.query(f"""
            SELECT
                country,
                LOWER(nutriscore_grade)  AS grade,
                COUNT(*)                 AS count,
                ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY country), 1)
                                         AS pct
            FROM (
                SELECT
                    UNNEST(countries_tags)   AS country,
                    nutriscore_grade
                FROM food
                WHERE nutriscore_grade IS NOT NULL
                  AND countries_tags IS NOT NULL
                  {cat_filter}
            ) t
            WHERE country IN ({countries_sql})
            GROUP BY country, grade
            HAVING SUM(COUNT(*)) OVER (PARTITION BY country) >= {min_products}
            ORDER BY country, grade
        """)

    def nutriscore_gaps(
        self,
        country: Optional[str] = None,
        min_products_total: int = 50,
        top_n: int = 20,
    ) -> pd.DataFrame:
        """Find categories with the worst Nutri-Score coverage (data gaps).

        Categories with many products but low Nutri-Score completion rates
        are potential data quality stories OR reflect sectors that resist
        transparency.

        Returns:
            DataFrame: category, total_products, pct_scored, pct_d_or_e
        """
        country_filter = (
            f"AND list_contains(countries_tags, '{country}')"
            if country else ""
        )

        return self.query(f"""
            SELECT
                category,
                COUNT(*)                                      AS total,
                ROUND(100.0 * COUNT(*) FILTER (WHERE nutriscore_grade IS NOT NULL)
                      / COUNT(*), 1)                          AS pct_scored,
                ROUND(100.0 * COUNT(*) FILTER (
                      WHERE LOWER(nutriscore_grade) IN ('d', 'e'))
                      / NULLIF(COUNT(*) FILTER (WHERE nutriscore_grade IS NOT NULL), 0),
                      1)                                      AS pct_d_or_e
            FROM (
                SELECT
                    UNNEST(categories_tags) AS category,
                    nutriscore_grade,
                    countries_tags
                FROM food
                WHERE categories_tags IS NOT NULL
                  {country_filter}
            ) t
            WHERE category LIKE 'en:%'
            GROUP BY category
            HAVING total >= {min_products_total}
            ORDER BY pct_scored ASC
            LIMIT {top_n}
        """)

    # ------------------------------------------------------------------
    # Additive analysis
    # ------------------------------------------------------------------

    def top_additives(
        self,
        country: Optional[str] = None,
        category: Optional[str] = None,
        top_n: int = 25,
        only_concerning: bool = False,
    ) -> pd.DataFrame:
        """Most frequent additives in the dataset.

        Args:
            country: Filter by country tag.
            category: Filter by category tag.
            top_n: Return top N additives by frequency.
            only_concerning: If True, filter to ADDITIVES_OF_CONCERN only.

        Returns:
            DataFrame: additive_tag, additive_name, product_count, pct_products
        """
        filters = self._build_filters(country, category)
        where = "WHERE additives_tags IS NOT NULL"
        if filters:
            where += f" AND {filters}"

        df = self.query(f"""
            SELECT
                additive                             AS additive_tag,
                COUNT(DISTINCT code)                 AS product_count,
                ROUND(100.0 * COUNT(DISTINCT code) / (
                    SELECT COUNT(*) FROM food {where}
                ), 3)                                AS pct_products
            FROM (
                SELECT
                    UNNEST(additives_tags) AS additive,
                    code
                FROM food
                {where}
            ) t
            GROUP BY additive
            ORDER BY product_count DESC
            LIMIT {top_n}
        """)

        # Add human-readable names
        df["additive_name"] = df["additive_tag"].map(ADDITIVES_OF_CONCERN)
        df["concerning"] = df["additive_tag"].isin(ADDITIVES_OF_CONCERN)

        if only_concerning:
            df = df[df["concerning"]].copy()

        return df

    def products_with_additive(
        self,
        additive_tag: str,
        country: Optional[str] = None,
        fields: Optional[list] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """Find products containing a specific additive.

        Args:
            additive_tag: Tag like "en:e171" (titanium dioxide).
            country: Filter by country tag.
            fields: Columns to return. Defaults to key identifiers.
            limit: Max rows to return.

        Example:
            # Find products with titanium dioxide (banned EU 2022)
            off.products_with_additive("en:e171", country="en:france")
        """
        default_fields = [
            "code", "product_name", "brands",
            "nutriscore_grade", "nova_group",
            "categories_tags", "countries_tags",
        ]
        cols = ", ".join(fields or default_fields)
        country_filter = (
            f"AND list_contains(countries_tags, '{country}')"
            if country else ""
        )

        return self.query(f"""
            SELECT {cols}
            FROM food
            WHERE list_contains(additives_tags, '{additive_tag}')
              {country_filter}
            LIMIT {limit}
        """)

    def additive_country_comparison(
        self,
        additive_tag: str,
        countries: Optional[list] = None,
    ) -> pd.DataFrame:
        """Compare prevalence of an additive across countries.

        Example: Is titanium dioxide more common in French products
        than in German ones?

        Args:
            additive_tag: Tag like "en:e171".
            countries: List of country tags. Defaults to EU_FOCUS_COUNTRIES.
        """
        countries = countries or EU_FOCUS_COUNTRIES
        countries_sql = ", ".join(f"'{c}'" for c in countries)

        return self.query(f"""
            SELECT
                country,
                COUNT(*)                                           AS total_products,
                COUNT(*) FILTER (WHERE list_contains(additives_tags, '{additive_tag}'))
                                                                   AS with_additive,
                ROUND(100.0 * COUNT(*) FILTER (
                        WHERE list_contains(additives_tags, '{additive_tag}'))
                    / NULLIF(COUNT(*), 0), 2)                      AS pct_with_additive
            FROM (
                SELECT
                    UNNEST(countries_tags) AS country,
                    additives_tags
                FROM food
                WHERE countries_tags IS NOT NULL
            ) t
            WHERE country IN ({countries_sql})
            GROUP BY country
            ORDER BY pct_with_additive DESC
        """)

    # ------------------------------------------------------------------
    # Brand & product comparison
    # ------------------------------------------------------------------

    def brand_comparison(
        self,
        brands: list,
        country: Optional[str] = None,
        metrics: Optional[list] = None,
    ) -> pd.DataFrame:
        """Compare multiple brands on Nutri-Score and NOVA distribution.

        Args:
            brands: List of brand names (case-insensitive substring match).
            country: Filter by country tag.
            metrics: Which scores to include. Default: nutriscore + nova.

        Returns:
            DataFrame with one row per brand.

        Example:
            off.brand_comparison(["Nestlé", "Danone", "Unilever"],
                                 country="en:france")
        """
        brand_conditions = " OR ".join(
            f"LOWER(brands) LIKE '%{b.lower()}%'" for b in brands
        )
        country_filter = (
            f"AND list_contains(countries_tags, '{country}')"
            if country else ""
        )

        return self.query(f"""
            SELECT
                brands,
                COUNT(*)                                          AS total_products,
                COUNT(*) FILTER (WHERE nutriscore_grade IS NOT NULL)
                                                                  AS products_scored,
                ROUND(100.0 * COUNT(*) FILTER (
                        WHERE LOWER(nutriscore_grade) IN ('a', 'b'))
                    / NULLIF(COUNT(*) FILTER (WHERE nutriscore_grade IS NOT NULL), 0),
                    1)                                            AS pct_ab,
                ROUND(100.0 * COUNT(*) FILTER (
                        WHERE LOWER(nutriscore_grade) IN ('d', 'e'))
                    / NULLIF(COUNT(*) FILTER (WHERE nutriscore_grade IS NOT NULL), 0),
                    1)                                            AS pct_de,
                ROUND(AVG(nova_group::FLOAT) FILTER (
                        WHERE nova_group IS NOT NULL), 2)         AS avg_nova_group,
                ROUND(100.0 * COUNT(*) FILTER (WHERE nova_group = 4)
                    / NULLIF(COUNT(*) FILTER (WHERE nova_group IS NOT NULL), 0),
                    1)                                            AS pct_nova4
            FROM food
            WHERE ({brand_conditions})
              {country_filter}
            GROUP BY brands
            HAVING total_products >= 3
            ORDER BY total_products DESC
        """)

    def worst_products_by_brand(
        self,
        brand: str,
        country: Optional[str] = None,
        score: str = "nutriscore",
        limit: int = 20,
    ) -> pd.DataFrame:
        """Find the worst-scoring products for a specific brand.

        Useful for: "Which Nestlé products are ultra-processed?"

        Args:
            brand: Brand name (case-insensitive substring).
            country: Filter by country tag.
            score: "nutriscore" (grade d/e) or "nova" (group 4).
            limit: Max results.
        """
        country_filter = (
            f"AND list_contains(countries_tags, '{country}')"
            if country else ""
        )
        if score == "nutriscore":
            score_filter = "AND LOWER(nutriscore_grade) IN ('d', 'e')"
            score_col = "nutriscore_grade"
        else:  # nova
            score_filter = "AND nova_group = 4"
            score_col = "nova_group"

        return self.query(f"""
            SELECT
                code,
                product_name,
                brands,
                nutriscore_grade,
                nova_group,
                categories_tags
            FROM food
            WHERE LOWER(brands) LIKE '%{brand.lower()}%'
              {country_filter}
              {score_filter}
            ORDER BY {score_col} DESC
            LIMIT {limit}
        """)

    # ------------------------------------------------------------------
    # Organic vs conventional / label analysis
    # ------------------------------------------------------------------

    def label_nutrition_comparison(
        self,
        label: str = "en:organic",
        category: Optional[str] = None,
        country: Optional[str] = None,
    ) -> pd.DataFrame:
        """Compare products WITH a label vs WITHOUT on Nutri-Score/NOVA.

        Classic investigation: do "organic" or "fair-trade" labels
        correlate with better nutritional quality?

        Args:
            label: Label tag (e.g. "en:organic", "en:fair-trade").
            category: Optional category to compare within.
            country: Optional country filter.

        Returns:
            DataFrame with labelled vs unlabelled comparison.
        """
        filters = self._build_filters(country, category)
        base_where = "WHERE nutriscore_grade IS NOT NULL"
        if filters:
            base_where += f" AND {filters}"

        return self.query(f"""
            SELECT
                CASE WHEN list_contains(labels_tags, '{label}') THEN 'With label'
                     ELSE 'Without label' END     AS label_status,
                COUNT(*)                           AS products,
                ROUND(100.0 * COUNT(*) FILTER (
                        WHERE LOWER(nutriscore_grade) = 'a') / COUNT(*), 1) AS pct_a,
                ROUND(100.0 * COUNT(*) FILTER (
                        WHERE LOWER(nutriscore_grade) IN ('a','b')) / COUNT(*), 1) AS pct_ab,
                ROUND(100.0 * COUNT(*) FILTER (
                        WHERE LOWER(nutriscore_grade) IN ('d','e')) / COUNT(*), 1) AS pct_de,
                ROUND(100.0 * COUNT(*) FILTER (
                        WHERE nova_group = 4) /
                      NULLIF(COUNT(*) FILTER (WHERE nova_group IS NOT NULL), 0), 1) AS pct_nova4
            FROM food
            {base_where}
            GROUP BY 1
            ORDER BY 1
        """)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        country: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """Search products by name (case-insensitive substring match).

        Args:
            query: Search term (e.g. "chocolate", "yogurt").
            country: Filter by country tag.
            category: Filter by category tag.
            limit: Max results.
        """
        filters = self._build_filters(country, category)
        # product_name is STRUCT(lang, text)[] — search across all language variants
        q = query.lower().replace("'", "''")
        where = (
            f"WHERE lower(array_to_string(list_transform(product_name, x -> x.\"text\"), ' '))"
            f" LIKE '%{q}%'"
        )
        if filters:
            where += f" AND {filters}"

        return self.query(f"""
            SELECT
                code,
                array_to_string(list_transform(product_name, x -> x."text"), ' / ')
                    AS product_name,
                brands,
                nutriscore_grade,
                nova_group,
                environmental_score_grade AS ecoscore_grade,
                countries_tags,
                categories_tags
            FROM food
            {where}
            ORDER BY
                CASE WHEN nutriscore_grade IS NOT NULL THEN 0 ELSE 1 END,
                product_name
            LIMIT {limit}
        """)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_subset(
        self,
        sql: str,
        output_path: str,
        format: str = "parquet",
    ) -> Path:
        """Export a query result to a file.

        Args:
            sql: SQL query. The result will be exported.
            output_path: Output file path.
            format: "parquet", "csv", or "json".

        Example:
            # Export all French products to a smaller parquet
            off.export_subset(
                "SELECT * FROM food WHERE list_contains(countries_tags, 'en:france')",
                "france_products.parquet"
            )
        """
        out = Path(output_path)
        format_upper = format.upper()

        if format_upper == "CSV":
            self.con.execute(f"COPY ({sql}) TO '{out}' (FORMAT CSV, HEADER)")
        elif format_upper == "JSON":
            self.con.execute(f"COPY ({sql}) TO '{out}' (FORMAT JSON)")
        else:
            self.con.execute(
                f"COPY ({sql}) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )

        size_mb = out.stat().st_size / 1e6
        print(f"✓ Exported to {out} ({size_mb:.1f} MB)")
        return out

    # ------------------------------------------------------------------
    # Nutriment helpers
    # ------------------------------------------------------------------

    def nutriments(
        self,
        country: Optional[str] = None,
        category: Optional[str] = None,
        fields: Optional[list] = None,
        limit: int = 10_000,
    ) -> pd.DataFrame:
        """Extract nutritional values per 100g as a flat DataFrame.

        Handles both struct-format and flat-format parquet columns.
        Returns key macro-nutrients: energy, fat, saturated fat,
        carbohydrates, sugars, fiber, proteins, salt.

        Args:
            country: Filter by country tag.
            category: Filter by category tag.
            fields: Extra columns to include.
            limit: Max rows (nutriments queries can be expensive).
        """
        filters = self._build_filters(country, category)
        where = "WHERE 1=1"
        if filters:
            where += f" AND {filters}"

        extra_cols = ", ".join(fields or [])
        if extra_cols:
            extra_cols = ", " + extra_cols

        if self._nutriment_mode == "struct":
            # Access nested struct fields
            return self.query(f"""
                SELECT
                    code,
                    product_name,
                    brands,
                    nutriscore_grade,
                    nova_group
                    {extra_cols},
                    -- nutriments is STRUCT(name, 100g, ...)[] — extract by name field
                    list_extract(list_filter(nutriments, x -> x.name = 'energy-kcal'), 1)."100g"    AS energy_kcal_100g,
                    list_extract(list_filter(nutriments, x -> x.name = 'fat'), 1)."100g"             AS fat_100g,
                    list_extract(list_filter(nutriments, x -> x.name = 'saturated-fat'), 1)."100g"  AS saturated_fat_100g,
                    list_extract(list_filter(nutriments, x -> x.name = 'carbohydrates'), 1)."100g"  AS carbs_100g,
                    list_extract(list_filter(nutriments, x -> x.name = 'sugars'), 1)."100g"          AS sugars_100g,
                    list_extract(list_filter(nutriments, x -> x.name = 'fiber'), 1)."100g"           AS fiber_100g,
                    list_extract(list_filter(nutriments, x -> x.name = 'proteins'), 1)."100g"        AS proteins_100g,
                    list_extract(list_filter(nutriments, x -> x.name = 'salt'), 1)."100g"            AS salt_100g
                FROM food
                {where}
                LIMIT {limit}
            """)
        elif self._nutriment_mode == "flat":
            # Already top-level columns
            return self.query(f"""
                SELECT
                    code,
                    product_name,
                    brands,
                    nutriscore_grade,
                    nova_group
                    {extra_cols},
                    energy_kcal_100g,
                    fat_100g,
                    saturated_fat_100g,
                    carbohydrates_100g  AS carbs_100g,
                    sugars_100g,
                    fiber_100g,
                    proteins_100g,
                    salt_100g
                FROM food
                {where}
                LIMIT {limit}
            """)
        else:
            warnings.warn(
                "Unknown nutriments format. Use off.schema() to inspect columns, "
                "then write a custom query with off.query(sql)."
            )
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Database growth (time-based analysis)
    # ------------------------------------------------------------------

    def product_growth_by_year(
        self,
        country: Optional[str] = None,
    ) -> pd.DataFrame:
        """Products added to the database by year (using created_t).

        This shows growth of food transparency reporting — not changes in
        food product formulations. Products created before 2012 are rare
        (OFF launched in 2012).

        ⚠ Important caveat: created_t reflects when the product was ENTERED
        into OFF, not when it was manufactured or sold. The data for old
        entries reflects TODAY's state, not the state at entry time.
        """
        country_filter = (
            f"AND list_contains(countries_tags, '{country}')"
            if country else ""
        )
        return self.query(f"""
            SELECT
                YEAR(to_timestamp(created_t))::INTEGER  AS year,
                COUNT(*)                                 AS products_added,
                COUNT(*) FILTER (WHERE nutriscore_grade IS NOT NULL)
                                                         AS with_nutriscore,
                COUNT(*) FILTER (WHERE nova_group IS NOT NULL)
                                                         AS with_nova_group
            FROM food
            WHERE created_t IS NOT NULL
              AND YEAR(to_timestamp(created_t)) BETWEEN 2012 AND 2026
              {country_filter}
            GROUP BY 1
            ORDER BY 1
        """)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_filters(
        self,
        country: Optional[str] = None,
        category: Optional[str] = None,
        brand: Optional[str] = None,
    ) -> str:
        """Build a SQL WHERE clause fragment for common filters."""
        parts = []
        if country:
            parts.append(f"list_contains(countries_tags, '{country}')")
        if category:
            parts.append(f"list_contains(categories_tags, '{category}')")
        if brand:
            parts.append(f"LOWER(brands) LIKE '%{brand.lower()}%'")
        return " AND ".join(parts)

    @staticmethod
    def attribution() -> str:
        """Return the required ODbL attribution string."""
        return ATTRIBUTION


# ---------------------------------------------------------------------------
# Standalone demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Open Food Facts Parquet Client — Demo")
    print("=" * 50)

    try:
        off = OFFParquet()
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)

    print("\n1. Dataset overview:")
    off.info()

    print("\n2. NOVA distribution — France:")
    print(off.nova_distribution(country="en:france"))

    print("\n3. Top additives in French breakfast cereals:")
    print(off.top_additives(
        country="en:france",
        category="en:breakfast-cereals",
        top_n=10,
    ))

    print("\n4. Nutri-Score by country (EU):")
    print(off.nutriscore_by_country().head(20))

    print(f"\n{OFFParquet.attribution()}")
