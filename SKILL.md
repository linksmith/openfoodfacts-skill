---
name: openfoodfacts
description: "Skill for investigating food product data using the Open Food Facts database. Use this skill when the user wants to analyse food products at scale — Nutri-Score distributions, NOVA ultra-processed food rates, additive frequencies, brand comparisons, or European cross-country nutrition patterns. SHOWCASE TRIGGER: when the user says 'showcase [brand]' or 'show me [brand]' generate a full HTML brand profile page. Triggers on: Open Food Facts, OFF, food.parquet, DuckDB food data, Nutri-Score, NOVA groups, ultra-processed foods, food labelling, E-numbers, food additives, European food data, food product databases, food transparency, Dataharvest workshop, food journalism, showcase, brand profile, brand dashboard. Primary method: DuckDB queries on a local food.parquet file (4.5M products, sub-second queries). Secondary: live API for individual product lookups."
---

## Purpose

Turn an AI agent into a food data investigative partner that analyses the
Open Food Facts database at scale — distribution patterns across millions
of products, brand comparisons, additive inventories, and cross-country
nutrition stories — all using a pre-downloaded local parquet file and
DuckDB for sub-second queries.

**Open Food Facts** is the world's largest open food product database:
4.5 million products, 150 countries, licensed under the Open Database
License (ODbL). Every output you produce must include attribution.

## Parquet file

The full Open Food Facts parquet lives in `data/` in the **project root**
(not inside the skill directory).

| File | Contents | Approx. size |
|---|---|---|
| `data/food.parquet` | Full dataset (4.5M+ products, 150 countries) | ~7.5 GB |

**Loading data:**
```python
from off_parquet import OFFParquet

off = OFFParquet("data/food.parquet")
# ✓ Connected: food.parquet
# ✓ 4,490,000 products · struct nutriments
# ✓ Data: Open Food Facts (openfoodfacts.org), ODbL v1.0
```

**Country filtering is done at query time** using DuckDB's `list_contains`:
```python
# All French products
off.query("SELECT * FROM food WHERE list_contains(countries_tags, 'en:france') LIMIT 10")

# Built-in methods also accept a country filter
df = off.nova_distribution(country="en:france")
df = off.top_additives(country="en:netherlands", category="en:breakfast-cereals")
```

---

## 🎯 Showcase trigger — fast-track brand analysis

**Trigger pattern:** any prompt containing `showcase` + a food brand name,
or variations like *"show me [brand]"*, *"brand profile for [brand]"*,
*"demo [brand]"*, *"create a dashboard for [brand]"*.

When you detect this pattern, follow these steps **exactly** and **do not
ask clarifying questions** unless the brand name is completely ambiguous:

### Step-by-step

**1. Extract the brand name** from the prompt.

**2. Choose the parquet file.** Use `data/food.parquet` (the full dataset).
Filter by country at query time using `list_contains(countries_tags, 'en:france')`.

**3. Run the showcase generator:**

```python
import sys
sys.path.insert(0, "path/to/skill/scripts")  # adjust to actual skill location
from brand_showcase import generate_brand_showcase

path = generate_brand_showcase(
    brand="[brand name from prompt]",
    parquet_path="data/food.parquet",
    output_dir="output",
    open_browser=True,       # opens in browser automatically
)
print(f"Saved to: {path}")
```

**4. Report the key findings** to the user in this format:
```
✅ Brand showcase generated: output/[slug]_showcase.html

Key findings for [Brand]:
• [X] products in the [eu/full] dataset
• [Y]% of products are ultra-processed (NOVA 4)
• Most common Nutri-Score: [grade]  ([ns_coverage]% of products scored)
• Avg. sugar: [X]g/100g vs. [Y]g/100g for [category] average
• [N] products contain additives of concern: [list top 1-2]

⚠️ Caveat: [brief data completeness note]
```

**5. If the brand returns 0 products**, try:
- A shorter brand name variant (e.g. "Kellogg" instead of "Kellogg's")
- The `"full"` parquet if using a subset
- Suggest similar brand names

### What the showcase generates

The HTML page includes 8 visualisations and tables:

| Section | What it shows |
|---|---|
| **Hero stats** | Product count · countries · % ultra-processed · avg sugar |
| **Nutri-Score distribution** | Stacked bar A→E with official colours |
| **NOVA distribution** | Doughnut chart, NOVA 4 highlighted red |
| **Nutrition vs. category avg** | Grouped bar: brand vs. category average (sugars, fat, salt, proteins) |
| **Top categories** | Horizontal bar — what kinds of products the brand makes |
| **Additives** | Table with E-numbers, flagging controversial ones (⚠️) |
| **Best products** | Table of Nutri-Score A/B products |
| **Worst products** | Table of Nutri-Score D/E products |

Output file: `output/{brand_slug}_showcase.html` (self-contained, no server needed).

### What makes this journalistically interesting

The three most **surprising** findings that OFF data reliably produces for major brands:

1. **NOVA 4 rate** — Major food brands often have 50–80% of their range
   classified as ultra-processed, even when they market products as "natural".
   This is the headline number.

2. **Nutri-Score vs. processing level mismatch** — A Nutri-Score B product
   can still be NOVA 4. Research shows 51% of B-grade products are
   ultra-processed. The showcase surfaces this tension.

3. **Additive fingerprint** — Each brand has a characteristic set of
   additives. Comparing the E-numbers across brands in the same category
   reveals very different food engineering approaches.

---

## Two data access modes

| Mode | When to use | Tool |
|---|---|---|
| **Parquet + DuckDB** (primary) | Any population-level analysis, distribution, aggregation, comparison | `scripts/off_parquet.py` |
| **REST API** (secondary) | Individual product lookup by barcode, real-time data | `scripts/off_client.py` |

**Default to parquet mode.** The API is rate-limited (10 searches/min),
returns at most a few hundred products per query, and cannot answer
population-level questions. Parquet queries run against all 4.5M products
in under one second.

## Quick-reference: files to read

| Need | File |
|---|---|
| Story templates and DuckDB query patterns | `references/story-recipes.md` |
| Nutri-Score methodology, NOVA classification | `references/nutriscore-nova-reference.md` |
| API endpoint details (for individual lookups only) | `references/api-guide.md` |

---

## Parquet workflow (primary)

### Step 1: Locate the parquet file

The workshop VMs have `food.parquet` pre-downloaded. Look for it in:
- The working directory: `./food.parquet`
- `./data/food.parquet`
- The user's home directory: `~/food.parquet`

The `OFFParquet` class auto-detects files in `data/`. If it raises
`FileNotFoundError`, run the setup script.

**One-command setup:**
```bash
# Install dependencies
pip install duckdb pandas requests tqdm

# Download food.parquet (~7.5 GB, ~10 min on 100 Mbit)
python scripts/download_data.py
```

**Check status:**
```bash
python scripts/download_data.py --status
```

**VM provisioning script (run during VM setup, before workshop):**
```bash
pip install duckdb pandas matplotlib plotly requests tqdm
python scripts/download_data.py   # ~10 min download
```

### Step 2: Install dependencies

```bash
# Core (always required for parquet mode)
pip install duckdb pandas

# Visualisation (install when the user wants charts)
pip install matplotlib plotly

# For download only
pip install requests tqdm huggingface_hub
```

### Step 3: Import and connect

```python
import sys
sys.path.insert(0, "/path/to/skill/scripts")  # adjust to actual skill location
from off_parquet import OFFParquet

off = OFFParquet("data/food.parquet")
# ✓ Connected: food.parquet
# ✓ 4,490,000 products · struct nutriments
# ✓ Data: Open Food Facts (openfoodfacts.org), ODbL v1.0
```

### Step 4: Explore the schema first

**Always start an investigation by inspecting the schema and running info().**
The parquet has ~180 columns; most analyses use a small subset.

```python
# Full column list with types
print(off.schema())

# Quick dataset overview
off.info()

# Sample 3 products to understand data shape
print(off.sample(3))

# Country coverage
print(off.country_coverage(top_n=15))
```

### Step 5: Choose your query approach

| Investigation | Method |
|---|---|
| NOVA ultra-processed distribution | `off.nova_distribution(country, category)` |
| NOVA by category (which foods are most processed?) | `off.nova_by_category(country)` |
| Nutri-Score distribution | `off.nutriscore_distribution(country, category, brand)` |
| Cross-country Nutri-Score comparison | `off.nutriscore_by_country(countries, category)` |
| Nutri-Score coverage gaps | `off.nutriscore_gaps(country)` |
| Most common additives | `off.top_additives(country, category)` |
| Products containing a specific additive | `off.products_with_additive("en:e171", country)` |
| Additive across countries | `off.additive_country_comparison("en:e171")` |
| Brand comparison | `off.brand_comparison(["Nestlé", "Danone"], country)` |
| Organic vs conventional | `off.label_nutrition_comparison("en:organic", category)` |
| Product search by name | `off.search("chocolate", country, category)` |
| Custom analysis | `off.query("SELECT ... FROM food WHERE ...")` |
| Export results | `off.export_subset(sql, "results.parquet")` |

### Step 6: Run the analysis

All methods return pandas DataFrames. For custom analyses, use `off.query(sql)`.

**Key DuckDB patterns for food data:**

```python
# Filter by country (tags are arrays)
off.query("""
    SELECT product_name, nutriscore_grade
    FROM food
    WHERE list_contains(countries_tags, 'en:france')
    LIMIT 100
""")

# Count products per country
off.query("""
    SELECT UNNEST(countries_tags) AS country, COUNT(*) AS n
    FROM food
    GROUP BY 1 ORDER BY 2 DESC LIMIT 20
""")

# Most common additives in French sodas
off.query("""
    SELECT UNNEST(additives_tags) AS additive, COUNT(*) AS n
    FROM food
    WHERE list_contains(countries_tags, 'en:france')
      AND list_contains(categories_tags, 'en:sodas')
    GROUP BY 1 ORDER BY 2 DESC LIMIT 20
""")

# Brand comparison: Nutri-Score and NOVA for Nestlé products
off.query("""
    SELECT
        LOWER(nutriscore_grade) AS grade,
        COUNT(*) AS n
    FROM food
    WHERE LOWER(brands) LIKE '%nestlé%'
    GROUP BY 1 ORDER BY 1
""")

# Products added by year (database growth)
off.query("""
    SELECT
        YEAR(to_timestamp(created_t)) AS year,
        COUNT(*) AS added
    FROM food
    WHERE created_t IS NOT NULL
    GROUP BY 1 ORDER BY 1
""")
```

### Step 7: Visualise findings

```python
import matplotlib.pyplot as plt
import pandas as pd

NUTRISCORE_COLORS = {
    'a': '#038141', 'b': '#85BB2F', 'c': '#FECB02',
    'd': '#EE8100', 'e': '#E63E11',
}
NOVA_COLORS = {1: '#4CAF50', 2: '#8BC34A', 3: '#FF9800', 4: '#F44336'}

# Example: NOVA distribution bar chart
df = off.nova_distribution(country="en:france")

fig, ax = plt.subplots(figsize=(9, 5))
colors = [NOVA_COLORS.get(g, '#999') for g in df["nova_group"]]
bars = ax.bar(df["nova_label"], df["pct"], color=colors)

# Journalism-style title: finding, not description
ax.set_title(
    "One in three French food products is ultra-processed (NOVA 4)",
    fontsize=13, fontweight='bold', loc='left'
)
ax.set_ylabel("% of products with NOVA data")
ax.bar_label(bars, labels=[f"{v:.1f}%" for v in df["pct"]], padding=3)

# Add sample size
n = df["count"].sum()
ax.annotate(f"n = {n:,} products with NOVA classification",
            xy=(0, 1.01), xycoords='axes fraction', fontsize=9, color='#555')

# Attribution (required for ODbL)
ax.annotate(
    OFFParquet.attribution(),
    xy=(0, -0.12), xycoords='axes fraction', fontsize=8, color='grey'
)

plt.tight_layout()
plt.savefig('output/nova_france.png', dpi=200, bbox_inches='tight')
plt.show()
```

---

## Key schema reference

The parquet has ~180 columns. The most useful for investigations:

### Identity
| Column | Type | Description |
|---|---|---|
| `code` | string | Product barcode (EAN-13/UPC) |
| `product_name` | string | Product name |
| `brands` | string | Brand(s), comma-separated |
| `brands_tags` | list[string] | Brand taxonomy tags |
| `lang` | string | Product language code |

### Geography
| Column | Type | Description |
|---|---|---|
| `countries_tags` | list[string] | Countries where sold (e.g. `["en:france"]`) |
| `stores` | string | Store names |
| `purchase_places` | string | Where purchased |

### Classification
| Column | Type | Description |
|---|---|---|
| `categories_tags` | list[string] | Category taxonomy (e.g. `["en:breakfast-cereals"]`) |
| `labels_tags` | list[string] | Labels (e.g. `["en:organic", "en:fair-trade"]`) |
| `packaging_tags` | list[string] | Packaging types |

### Scores (key for investigations)
| Column | Type | Description |
|---|---|---|
| `nutriscore_grade` | string | A–E (null if unavailable) |
| `nutriscore_score` | integer | Underlying numeric score |
| `nova_group` | integer | 1–4 processing level |
| `ecoscore_grade` | string | Environmental score A–E |

### Nutrition (in `nutriments` struct)
Access struct fields: `nutriments['energy-kcal_100g']`  
Or with the helper: `off.nutriments(country, category)`

| Field | Description |
|---|---|
| `energy-kcal_100g` | Energy in kcal per 100g |
| `fat_100g` | Total fat |
| `saturated-fat_100g` | Saturated fat |
| `carbohydrates_100g` | Total carbohydrates |
| `sugars_100g` | Sugars |
| `fiber_100g` | Dietary fibre |
| `proteins_100g` | Proteins |
| `salt_100g` | Salt |

### Ingredients & additives
| Column | Type | Description |
|---|---|---|
| `ingredients_text` | string | Full ingredient list text |
| `ingredients_tags` | list[string] | Parsed ingredient tags |
| `additives_tags` | list[string] | E-number tags (e.g. `["en:e330"]`) |
| `allergens_tags` | list[string] | Allergen tags |

### Timestamps
| Column | Type | Description |
|---|---|---|
| `created_t` | integer | Unix timestamp: when product was ENTERED into OFF |
| `created_datetime` | string | ISO 8601 creation date |
| `last_modified_t` | integer | Unix timestamp: last edit |
| `last_modified_datetime` | string | ISO 8601 last modified |

⚠ **Important for "change over time" angles:** `created_t` shows when
a product was added to OFF, NOT when it was manufactured. The current
values (nutriscore, ingredients, etc.) reflect TODAY's state of the
product entry, not its state when first added.

For true historical comparison, Open Food Facts does NOT maintain annual
snapshots. The per-product revision history is accessible via the API
(`?revisions` endpoint) but is impractical at scale. Story angles that
DO work with timestamp data: database growth by year, nutriscore adoption
timeline, countries that ramped up submissions, categories that expanded.

---

## Key Open Food Facts data concepts

### Nutri-Score (a–e)

A letter grade for nutritional quality used across Europe. `a` (dark
green) is best, `e` (dark red) is worst. Based on a points system weighing
negative factors (energy, sugars, saturated fat, salt) against positive
factors (fibre, protein, fruits/vegetables/nuts).

**Coverage**: ~40–50% of products have a Nutri-Score. It requires complete
nutritional information AND a category assignment. Missing scores are a
finding: report the percentage always.

### NOVA groups (1–4)

Food processing classification:
- **1** — Unprocessed or minimally processed (fresh fruit, rice, eggs)
- **2** — Processed culinary ingredients (oil, butter, sugar, flour)
- **3** — Processed foods (canned vegetables, cheese, bread)
- **4** — Ultra-processed foods (soft drinks, chips, instant noodles)

NOVA 4 is the journalistically interesting category. Research links UPF
consumption to adverse health outcomes. Coverage varies: ~30–40% of products
have NOVA data.

### Eco-Score (a–e)

Environmental impact score based on Life Cycle Analysis. Coverage is
lower than Nutri-Score (~20%). Less useful for workshop investigations
unless specifically requested.

### Taxonomy tags

All categories, labels, allergens, additives, and countries come as arrays
of taxonomy tags in the format `en:breakfast-cereals`. The `en:` prefix
is the language. Use `list_contains(column, 'tag')` in DuckDB SQL for
filtering.

**Key tag namespaces:**
- Countries: `en:france`, `en:germany`, `en:netherlands`
- Categories: `en:breakfast-cereals`, `en:yogurts`, `en:sodas`
- Labels: `en:organic`, `en:fair-trade`, `en:vegan`
- Additives: `en:e171`, `en:e621`, `en:e951`
- Allergens: `en:gluten`, `en:milk`, `en:nuts`

---

## Coverage caveats (always report these)

1. **OFF is not a random sample.** It's a crowd-sourced database. France
   has ~2M products; smaller countries may have far fewer. Engaged
   communities (health-conscious consumers, France, Belgium) are
   overrepresented. This affects representativeness for any country analysis.

2. **Score coverage varies.** ~40–50% of products have Nutri-Score; ~30–40%
   have NOVA. Always report: how many products in your analysis had the
   score, and what percentage that is of the total. If 60% are missing, that
   itself may be the story.

3. **Brand names are messy.** Products from the same brand can appear as
   "Nestlé", "nestle", "Nestle S.A." etc. Use `LIKE '%nestl%'` for fuzzy
   matching. Group by brand cautiously.

4. **Categories are nested.** A product can be in both `en:cereals` AND
   `en:breakfast-cereals`. `list_contains` checks for an exact tag, so be
   specific. To find any cereal: use `ILIKE '%cereal%'` on `categories_tags::TEXT`.

5. **Additives require ingredient text.** NOVA and additive detection
   depends on accurate ingredient parsing. Products with incomplete
   ingredient text will lack these fields.

---

## Data ethics & attribution

Every analysis, chart, and export must include:
```
Data: Open Food Facts (openfoodfacts.org), ODbL v1.0
```

Open Food Facts is a non-profit. If journalists use the data in a
published story, they should credit OFF and optionally link back.

---

## Visualisation

### Chart selection

| Goal | Chart type |
|---|---|
| Nutri-Score distribution | Horizontal bar, coloured by grade |
| NOVA distribution | Stacked or grouped bar (never pie) |
| Cross-country comparison | Grouped bar, one group per country |
| Brand comparison | Dot plot or grouped bar |
| Additive frequency | Horizontal bar, sorted descending |
| Database growth by year | Line chart or area chart |

### Journalism chart principles

1. **Title states the finding, not the data.** Write "One in three French
   products is ultra-processed" — not "NOVA distribution in France".
2. **Show sample size** in subtitle: `n = 45,203 products with NOVA data`.
3. **Always include attribution** in every chart footer.
4. **Use colorblind-safe palettes.** For Nutri-Score use official colours.
5. **Report missingness.** "60% of products lack a Nutri-Score in this
   category — the analysis covers the 40% that do."

### Nutri-Score official colours

```python
NUTRISCORE_COLORS = {
    'a': '#038141',  # dark green
    'b': '#85BB2F',  # light green
    'c': '#FECB02',  # yellow
    'd': '#EE8100',  # orange
    'e': '#E63E11',  # red
}
```

### NOVA colours

```python
NOVA_COLORS = {
    1: '#4CAF50',  # green — unprocessed
    2: '#8BC34A',  # light green — culinary ingredients
    3: '#FF9800',  # orange — processed
    4: '#F44336',  # red — ultra-processed
}
```

---

## Output format

Every investigation must produce:

1. **A clear investigation question** (the hypothesis being tested)
2. **Complete, runnable Python code** — no pseudocode, no placeholders
3. **A finding summary** (2–3 sentences on the key insight)
4. **Data caveats** (coverage rate, what's missing, representativeness)
5. **ODbL attribution**: `Data: Open Food Facts (openfoodfacts.org), ODbL v1.0`

---

## API mode (secondary — individual lookups only)

Use the REST API only when the user needs real-time data for a specific
product by barcode, or when the parquet file is not available.

```python
sys.path.insert(0, "/path/to/skill/scripts")
from off_client import OFFClient

client = OFFClient(contact_email="workshop@example.com")

# Look up a single product by barcode
product = client.get_product("3017620422003")  # Nutella

# Search (rate-limited: 10 req/min, 6s delay per call)
df = client.search_products("olive oil", country="france", page_size=50)
```

The API helper handles rate limiting automatically. See `references/api-guide.md`
for endpoint details and `scripts/off_client.py` for method documentation.

**Do not use the API for population-level analysis.** The search endpoint
returns at most a few hundred products per query and is rate-limited to
10 calls/minute. Use the parquet instead.
