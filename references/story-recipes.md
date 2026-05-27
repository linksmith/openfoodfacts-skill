# Story Recipes: Food Data Investigation Templates

## How to use these recipes

Each recipe is a complete investigation template: a journalistic question,
DuckDB code using the `OFFParquet` helper, what to look for, and caveats.

All recipes use **parquet mode** (DuckDB on `food.parquet`). This gives you
sub-second queries across 4.5 million products.

**Difficulty** reflects data wrangling complexity, not query time.

---

## Recipe 1: The Ultra-Processed Aisle

**Difficulty:** Easy  
**Angle:** What percentage of products in a category are ultra-processed (NOVA 4)?

```python
from off_parquet import OFFParquet
import matplotlib.pyplot as plt

off = OFFParquet("food.parquet")

# NOVA distribution for breakfast cereals in France
df = off.nova_distribution(country="en:france", category="en:breakfast-cereals")
print(df)

# Same, but for ALL categories — which are the worst?
worst_categories = off.nova_by_category(country="en:france", top_n=20)
print(worst_categories)

# Manual DuckDB version if you want custom categories
df_custom = off.query("""
    SELECT
        UNNEST(categories_tags) AS category,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE nova_group = 4) AS nova4,
        ROUND(100.0 * COUNT(*) FILTER (WHERE nova_group = 4) / COUNT(*), 1) AS nova4_pct
    FROM food
    WHERE list_contains(countries_tags, 'en:france')
      AND nova_group IS NOT NULL
    GROUP BY category
    HAVING category LIKE 'en:%' AND total >= 200
    ORDER BY nova4_pct DESC
    LIMIT 20
""")
```

**What to look for:**
- Categories with surprisingly high NOVA 4 rates (e.g., "healthy" categories
  like yoghurts or fruit juices)
- The most processed categories versus least processed
- Always report the n (products with NOVA data) and the coverage rate

**Lede template:** "Of the [X] [category] products listed on Open Food Facts
in [country], [Y]% are classified as ultra-processed (NOVA 4) — meaning
they contain ingredients you would not find in a home kitchen."

**Caveats:** NOVA depends on ingredient parsing. Products without ingredient
text won't have a NOVA group. Report coverage: "of the X products with NOVA
data, representing Y% of all products in this category."

---

## Recipe 2: Nutri-Score Showdown

**Difficulty:** Easy  
**Angle:** Which brand makes the healthiest products in a category?

```python
from off_parquet import OFFParquet

off = OFFParquet("food.parquet")

# Compare specific brands — replace with brands you're investigating
df = off.brand_comparison(
    brands=["Nestlé", "Danone", "Unilever", "Kellogg"],
    country="en:france",
)
print(df)

# Worst products for a specific brand
worst = off.worst_products_by_brand("Kellogg", country="en:france", score="nutriscore")
print(worst)

# Full Nutri-Score breakdown by brand in a category
df_detail = off.query("""
    SELECT
        brands,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE LOWER(nutriscore_grade) = 'a') AS grade_a,
        COUNT(*) FILTER (WHERE LOWER(nutriscore_grade) = 'b') AS grade_b,
        COUNT(*) FILTER (WHERE LOWER(nutriscore_grade) = 'c') AS grade_c,
        COUNT(*) FILTER (WHERE LOWER(nutriscore_grade) = 'd') AS grade_d,
        COUNT(*) FILTER (WHERE LOWER(nutriscore_grade) = 'e') AS grade_e,
        ROUND(100.0 * COUNT(*) FILTER (WHERE LOWER(nutriscore_grade) IN ('a','b'))
            / NULLIF(COUNT(*) FILTER (WHERE nutriscore_grade IS NOT NULL), 0), 1) AS pct_ab
    FROM food
    WHERE list_contains(countries_tags, 'en:france')
      AND list_contains(categories_tags, 'en:breakfast-cereals')
      AND brands IS NOT NULL
    GROUP BY brands
    HAVING COUNT(*) >= 5
    ORDER BY pct_ab DESC
    LIMIT 30
""")
```

**What to look for:**
- Brands where most products are Nutri-Score A/B vs. D/E
- Premium brands that score poorly; budget brands that score well
- The % of products with NO Nutri-Score (potential transparency gap)

**Lede template:** "Among [X] breakfast cereal brands in [country],
[Brand] leads with [Y]% of its products earning Nutri-Score A or B.
Meanwhile, [Brand2] has [Z]% of its products in the D or E range."

**Caveats:** Brand names are messy in OFF — "Nestlé", "nestle", "NESTLE"
all appear. Use case-insensitive LIKE. Results reflect products in the
database, not market share.

---

## Recipe 3: The Additive Inventory

**Difficulty:** Medium  
**Angle:** Which additives are most common in a category? Any EU-controversial ones?

```python
from off_parquet import OFFParquet, ADDITIVES_OF_CONCERN

off = OFFParquet("food.parquet")

# Top additives in French sodas
df = off.top_additives(
    country="en:france",
    category="en:sodas",
    top_n=25,
)
print(df)

# Show only concerning additives
concerning = off.top_additives(country="en:france", only_concerning=True)
print(concerning)

# Products still containing titanium dioxide (banned EU 2022)
# This is a strong investigative angle — legally should not be in EU products
e171 = off.products_with_additive("en:e171", country="en:france")
print(f"Products with E171 (banned EU 2022): {len(e171)}")
print(e171[["product_name", "brands", "categories_tags"]].head(20))

# E171 prevalence across EU countries
e171_by_country = off.additive_country_comparison("en:e171")
print(e171_by_country)

# Manual: most common additives with their E-number
df_additives = off.query("""
    SELECT
        UNNEST(additives_tags)    AS additive,
        COUNT(DISTINCT code)      AS product_count,
        ROUND(100.0 * COUNT(DISTINCT code) / (SELECT COUNT(*) FROM food
            WHERE list_contains(countries_tags, 'en:france')
              AND list_contains(categories_tags, 'en:sodas')
        ), 2)                     AS pct
    FROM food
    WHERE list_contains(countries_tags, 'en:france')
      AND list_contains(categories_tags, 'en:sodas')
    GROUP BY 1
    ORDER BY 2 DESC
    LIMIT 25
""")
```

**Additives of EU journalistic interest:**
| E-number | Name | Issue |
|---|---|---|
| E171 | Titanium dioxide | Banned in EU since Aug 2022 |
| E621 | Monosodium glutamate (MSG) | Controversy; not banned but watch |
| E951 | Aspartame | IARC Group 2B (possibly carcinogenic) July 2023 |
| E102 | Tartrazine | Azo dye linked to hyperactivity |
| E110, E122, E124, E129 | Other azo dyes | EU requires warning labels |
| E211 | Sodium benzoate | Reacts with E300 (vitamin C) to form benzene |
| E320 | BHA | Possible carcinogen; banned in some countries |

**Lede template:** "The average [category] in [country] contains [X]
additives. [Y] products still list E171 (titanium dioxide) as an
ingredient — despite the EU banning it in August 2022."

**Caveats:** Additive detection depends on ingredient text parsing. Products
without complete ingredient lists won't have additive tags. OFF data reflects
what was contributed by volunteers — enforcement bodies have more authoritative data.

---

## Recipe 4: The Health Halo Investigation

**Difficulty:** Medium  
**Angle:** Do "organic" or "fair-trade" labels actually mean better nutrition?

```python
from off_parquet import OFFParquet

off = OFFParquet("food.parquet")

# Organic vs conventional in French yoghurts
df = off.label_nutrition_comparison(
    label="en:organic",
    category="en:yogurts",
    country="en:france",
)
print(df)

# Manual version for more detail
df_compare = off.query("""
    SELECT
        CASE WHEN list_contains(labels_tags, 'en:organic') THEN 'Organic'
             ELSE 'Conventional' END                      AS type,
        COUNT(*)                                           AS products,
        COUNT(*) FILTER (WHERE nutriscore_grade IS NOT NULL) AS scored,
        ROUND(AVG(CASE WHEN LOWER(nutriscore_grade) = 'a' THEN 1.0
                       WHEN LOWER(nutriscore_grade) = 'b' THEN 2.0
                       WHEN LOWER(nutriscore_grade) = 'c' THEN 3.0
                       WHEN LOWER(nutriscore_grade) = 'd' THEN 4.0
                       WHEN LOWER(nutriscore_grade) = 'e' THEN 5.0
                  END), 2)                                 AS avg_grade_numeric,
        ROUND(100.0 * COUNT(*) FILTER (WHERE LOWER(nutriscore_grade) IN ('a','b'))
            / NULLIF(COUNT(*) FILTER (WHERE nutriscore_grade IS NOT NULL), 0), 1)
                                                           AS pct_ab,
        ROUND(100.0 * COUNT(*) FILTER (WHERE nova_group = 4)
            / NULLIF(COUNT(*) FILTER (WHERE nova_group IS NOT NULL), 0), 1)
                                                           AS pct_ultra_processed
    FROM food
    WHERE list_contains(categories_tags, 'en:yogurts')
      AND list_contains(countries_tags, 'en:france')
    GROUP BY 1
""")
print(df_compare)

# Labels available for exploration
off.query("""
    SELECT UNNEST(labels_tags) AS label, COUNT(*) AS n
    FROM food
    WHERE list_contains(countries_tags, 'en:france')
    GROUP BY 1 ORDER BY 2 DESC LIMIT 20
""")
```

**What to look for:**
- Does Nutri-Score distribution differ between organic and conventional?
- Is ultra-processed rate lower for organic products?
- The answer often surprises: "organic" is about farming methods, not
  nutrition. Organic cookies can still be full of sugar and be NOVA 4.

**Lede template:** "Organic [category] in [country] score [better/no better/
worse] on nutrition than their conventional counterparts. [Y]% of organic
products earn a Nutri-Score A or B vs. [Z]% for conventional."

**Caveats:** "Organic" in OFF comes from label tags contributed by volunteers.
Coverage may be incomplete. Organic products cluster in premium categories
(e.g., organic beer vs. organic fresh vegetables) — compare within categories.

---

## Recipe 5: Cross-Border Comparison

**Difficulty:** Medium  
**Angle:** Does the same category have different nutritional profiles in different countries?

```python
from off_parquet import OFFParquet
import pandas as pd
import matplotlib.pyplot as plt

off = OFFParquet("food.parquet")

# Nutri-Score distribution across EU countries for breakfast cereals
df = off.nutriscore_by_country(
    countries=["en:france", "en:germany", "en:netherlands",
               "en:spain", "en:italy", "en:united-kingdom"],
    category="en:breakfast-cereals",
)
print(df)

# Pivot for easy comparison
pivot = df.pivot(index="country", columns="grade", values="pct").fillna(0)
print(pivot)

# NOVA 4 rate by country (all categories)
nova_by_country = off.query("""
    SELECT
        country,
        COUNT(*) AS total_with_nova,
        COUNT(*) FILTER (WHERE nova_group = 4) AS nova4_count,
        ROUND(100.0 * COUNT(*) FILTER (WHERE nova_group = 4) / COUNT(*), 1)
            AS nova4_pct
    FROM (
        SELECT UNNEST(countries_tags) AS country, nova_group
        FROM food
        WHERE nova_group IS NOT NULL
    ) t
    WHERE country IN ('en:france', 'en:germany', 'en:netherlands',
                      'en:spain', 'en:italy', 'en:united-kingdom',
                      'en:belgium', 'en:poland')
    GROUP BY country
    HAVING total_with_nova >= 1000
    ORDER BY nova4_pct DESC
""")
print(nova_by_country)
```

**What to look for:**
- Countries with notably better or worse average scores
- Same category, different country — are EU food markets diverging?
- Very different sample sizes by country: France has ~2M products;
  smaller countries have far fewer. Always report n.

**Lede template:** "Breakfast cereals in [country] are [more/less] likely
to earn a Nutri-Score A or B than in [country2]. [Y]% score A or B in
[country1] vs. [Z]% in [country2] — among products with a Nutri-Score."

**Caveats:** Coverage varies enormously. France dominates OFF with ~2M
products. Products listed reflect what consumers scanned — not a
representative market survey. Report sample sizes always.

---

## Recipe 6: Hidden Salt & Sugar

**Difficulty:** Easy  
**Angle:** Which "healthy"-labelled products have surprising amounts of sugar or salt?

```python
from off_parquet import OFFParquet

off = OFFParquet("food.parquet")

# Nutriment data for organic products in France
nutrients = off.nutriments(
    country="en:france",
    limit=50_000,
)

# Merge with labels
df_full = off.query("""
    SELECT
        product_name,
        brands,
        labels_tags,
        nutriscore_grade,
        list_extract(list_filter(nutriments, x -> x.name = 'sugars'), 1)."100g" AS sugars_100g,
        list_extract(list_filter(nutriments, x -> x.name = 'salt'), 1)."100g"   AS salt_100g,
        list_extract(list_filter(nutriments, x -> x.name = 'fat'), 1)."100g"    AS fat_100g
    FROM food
    WHERE list_contains(labels_tags, 'en:organic')
      AND list_contains(countries_tags, 'en:france')
      AND nutriments IS NOT NULL AND len(nutriments) > 0
    ORDER BY sugars_100g DESC NULLS LAST
    LIMIT 50
""")
print(df_full.head(20))

# Find the worst offenders: organic products with high sugar
high_sugar_organic = off.query("""
    SELECT
        product_name,
        brands,
        nutriscore_grade,
        list_extract(list_filter(nutriments, x -> x.name = 'sugars'), 1)."100g" AS sugars_100g,
        categories_tags
    FROM food
    WHERE list_contains(labels_tags, 'en:organic')
      AND list_contains(countries_tags, 'en:france')
      AND list_extract(list_filter(nutriments, x -> x.name = 'sugars'), 1)."100g" > 20
    ORDER BY sugars_100g DESC
    LIMIT 20
""")
print(high_sugar_organic)
```

**What to look for:**
- "Healthy" labelled products with >15g sugar/100g
- "Wholegrain" products with high salt
- Category averages vs. labelled product averages
- Compare threshold: EU "high sugar" = >22.5g/100g; "high salt" = >1.5g/100g

**Lede template:** "[Product name], marketed as [healthy label], contains
[X]g of sugar per 100g — [Y] times the average for [category]."

**Caveats:** Nutriment data requires complete product entry. Many products
lack exact values. Thresholds for "high" depend on category. Always compare
within categories.

---

## Recipe 7: Transparency gaps — where is the data missing?

**Difficulty:** Easy  
**Angle:** Which categories have the worst Nutri-Score coverage?

A low coverage rate can mean: the category is dominated by small producers
who don't enter data, OR that the industry resists transparency.

```python
from off_parquet import OFFParquet

off = OFFParquet("food.parquet")

# Categories with worst Nutri-Score coverage
gaps = off.nutriscore_gaps(country="en:france")
print(gaps)

# Custom: categories with most products but no score
off.query("""
    SELECT
        UNNEST(categories_tags) AS category,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE nutriscore_grade IS NOT NULL) AS scored,
        ROUND(100.0 * COUNT(*) FILTER (WHERE nutriscore_grade IS NOT NULL)
            / COUNT(*), 1) AS pct_scored
    FROM food
    WHERE list_contains(countries_tags, 'en:france')
      AND categories_tags IS NOT NULL
    GROUP BY category
    HAVING category LIKE 'en:%' AND total >= 500
    ORDER BY pct_scored ASC
    LIMIT 20
""")

# Overall coverage by country
off.query("""
    SELECT
        country,
        COUNT(*) AS total_products,
        ROUND(100.0 * COUNT(*) FILTER (WHERE nutriscore_grade IS NOT NULL)
            / COUNT(*), 1) AS nutriscore_coverage_pct,
        ROUND(100.0 * COUNT(*) FILTER (WHERE nova_group IS NOT NULL)
            / COUNT(*), 1) AS nova_coverage_pct
    FROM (
        SELECT UNNEST(countries_tags) AS country, nutriscore_grade, nova_group
        FROM food
        WHERE countries_tags IS NOT NULL
    ) t
    WHERE country IN ('en:france', 'en:germany', 'en:netherlands',
                      'en:spain', 'en:italy', 'en:united-kingdom')
    GROUP BY country
    ORDER BY total_products DESC
""")
```

**What to look for:**
- Categories with <20% Nutri-Score coverage in a country
- Categories where coverage varies significantly between countries
- Fast-food or alcohol categories (they're deliberately excluded from
  Nutri-Score by EU policy)

**Lede template:** "Of [X] [category] products in [country], only [Y]%
have a Nutri-Score — leaving consumers without nutritional guidance
for the majority of [category] on supermarket shelves."

---

## Recipe 8: Database growth over time

**Difficulty:** Easy  
**Angle:** How has food transparency changed since Open Food Facts launched?

⚠ **Important context:** This tracks when products were ENTERED into OFF
(created_t), not when food products were manufactured or reformulated.
It tells the story of growing food transparency and citizen engagement —
a valid and compelling angle. It does NOT track product reformulation.

```python
from off_parquet import OFFParquet
import matplotlib.pyplot as plt

off = OFFParquet("food.parquet")

# Products added by year (global)
growth = off.product_growth_by_year()
print(growth)

# Growth for a specific country
nl_growth = off.product_growth_by_year(country="en:netherlands")
print(nl_growth)

# Year when Nutri-Score was widely adopted (shows policy uptake)
off.query("""
    SELECT
        YEAR(to_timestamp(created_t)) AS year,
        COUNT(*) AS total_added,
        COUNT(*) FILTER (WHERE nutriscore_grade IS NOT NULL) AS with_nutriscore,
        ROUND(100.0 * COUNT(*) FILTER (WHERE nutriscore_grade IS NOT NULL)
            / COUNT(*), 1) AS pct_scored
    FROM food
    WHERE created_t IS NOT NULL
      AND YEAR(to_timestamp(created_t)) BETWEEN 2015 AND 2026
    GROUP BY 1 ORDER BY 1
""")

# Which country drove growth in recent years?
off.query("""
    SELECT
        UNNEST(countries_tags) AS country,
        YEAR(to_timestamp(created_t)) AS year,
        COUNT(*) AS products_added
    FROM food
    WHERE created_t IS NOT NULL
      AND YEAR(to_timestamp(created_t)) BETWEEN 2020 AND 2026
      AND countries_tags IS NOT NULL
    GROUP BY 1, 2
    HAVING country IN ('en:france', 'en:germany', 'en:netherlands',
                       'en:spain', 'en:united-kingdom')
    ORDER BY 2, 3 DESC
""")
```

**What to look for:**
- OFF launched in 2012 — products before 2014 are very few
- Major spikes after app launches, media coverage, regulation
- Which country drove growth in each year
- Nutriscore adoption: what % of new products were scored in 2023 vs 2018?

**Lede template:** "Since 2012, citizens in [country] have contributed
[X] food products to the world's largest open food database — [Y] of them
in the past two years alone."

---

## Recipe 9: Product name search + deep profile

**Difficulty:** Easy  
**Angle:** Find all products matching a search term and compare them.

```python
from off_parquet import OFFParquet

off = OFFParquet("food.parquet")

# Search for chocolate products in Netherlands
results = off.search("chocolade", country="en:netherlands")
print(f"Found {len(results)} products")

# Custom search with nutriment data
off.query("""
    SELECT
        product_name,
        brands,
        nutriscore_grade,
        nova_group,
        list_extract(list_filter(nutriments, x -> x.name = 'sugars'), 1)."100g" AS sugars_100g,
        list_extract(list_filter(nutriments, x -> x.name = 'salt'), 1)."100g"   AS salt_100g
    FROM food
    WHERE lower(array_to_string(list_transform(product_name, x -> x."text"), ' ')) LIKE '%chocolade%'
       OR lower(array_to_string(list_transform(product_name, x -> x."text"), ' ')) LIKE '%chocolate%'
    AND list_contains(countries_tags, 'en:netherlands')
    ORDER BY sugars_100g DESC NULLS LAST
    LIMIT 50
""")

# Full-text across all text fields (slower but thorough)
off.query("""
    SELECT code, product_name, brands, nutriscore_grade, nova_group
    FROM food
    WHERE (
        LOWER(product_name) LIKE '%palm oil%'
        OR LOWER(ingredients_text) LIKE '%palm oil%'
    )
    AND list_contains(countries_tags, 'en:netherlands')
    LIMIT 200
""")
```

---

## General tips: finding stories in food data

### The five story shapes

1. **Contrast**: X is different from Y (country, brand, label)
2. **Ranking**: X is the best/worst at Z (top 10, bottom 10)
3. **Gap**: Reality doesn't match expectation (healthy label ≠ healthy)
4. **Trend**: Growth over time (database growth, nutriscore adoption)
5. **Outlier**: This one case is unusual — why?

### Statistical hygiene

- **Always report sample sizes.** "80% of products" means very different
  things for n=5 vs. n=500.
- **Always report coverage.** "Based on 2,341 products that have NOVA data,
  out of 8,900 total in this category (26%)."
- **Missing data is data.** A 60% missing Nutri-Score rate is a finding.
- **Don't cherry-pick.** If you show the top 5, mention the overall distribution.
- **Brand names are messy.** Always use case-insensitive partial match. Never
  trust exact brand name matches.
- **Correlation ≠ causation.** "Organic products have higher sugar" doesn't
  mean organic farming causes more sugar.

### Benchmark numbers to know

| Metric | Rough European average |
|---|---|
| Ultra-processed (NOVA 4) | ~30-35% of products with NOVA data |
| Nutri-Score A or B | ~30-35% of products with a score |
| Nutri-Score D or E | ~25-30% of products with a score |
| Products with Nutri-Score | ~40-50% of all products |
| Products with NOVA | ~30-40% of all products |

These are rough benchmarks. They vary significantly by country and category.
Use `off.nutriscore_distribution()` and `off.nova_distribution()` for your
specific subset.

### EU policy context (useful for framing stories)

- **Nutri-Score**: Not yet mandatory in EU (voluntary). France, Belgium,
  Netherlands, Germany, Spain have adopted it. Ongoing EU debate.
- **NOVA**: Not an official EU score — it's a research framework. But widely
  cited in health research and increasingly in journalism.
- **Eco-Score**: Being tested; not mandatory.
- **E171 (titanium dioxide)**: Banned in EU food since August 2022.
- **Azo dyes** (E102, E110, E122, E124, E129, E155): EU requires warning label
  "may have an adverse effect on activity and attention in children."
- **Aspartame (E951)**: IARC classified as "possibly carcinogenic" (Group 2B)
  in July 2023. WHO reaffirmed safe intake levels.
