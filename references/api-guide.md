# Open Food Facts API Reference

Read this file when you need endpoint details beyond what the helper module
(`off_client.py`) provides — for example, constructing custom queries,
understanding response structures, or using facet endpoints directly.

## Base URLs

| Environment | URL | Notes |
|---|---|---|
| Production (global) | `https://world.openfoodfacts.org` | Main API, use for all queries |
| Production (country) | `https://{cc}.openfoodfacts.org` | Country-specific (e.g., `fr.`, `de.`) |
| Staging | `https://world.openfoodfacts.net` | For testing; basic auth: `off`/`off` |

For most queries, use the global URL with a `countries_tags_en` filter
rather than a country-specific subdomain.

## Product endpoint

```
GET /api/v2/product/{barcode}
```

| Parameter | Description |
|---|---|
| `fields` | Comma-separated list of fields to return (always use this) |

**Example:**
```
GET /api/v2/product/3017620422003?fields=code,product_name,nutriscore_grade,nutriments
```

Returns a JSON object with a `product` key containing the product data,
or `status: 0` if not found.

## Search endpoint

```
GET /api/v2/search
```

This is the recommended search endpoint. The older `/cgi/search.pl` endpoint
exists but is less reliable and may return 503 errors under load.

| Parameter | Type | Description |
|---|---|---|
| `search_terms` | string | Free text search query |
| `page` | int | Page number (1-based) |
| `page_size` | int | Results per page (max 100) |
| `fields` | string | Comma-separated fields to return |
| `sort_by` | string | Sort field: `popularity`, `product_name`, `created_t`, `last_modified_t` |

### Filter parameters

| Parameter | Example | Description |
|---|---|---|
| `categories_tags` | `en:breakfast-cereals` | Filter by category (use `en:` prefix) |
| `brands_tags` | `nestle` | Filter by brand (lowercase) |
| `countries_tags_en` | `france` | Filter by country (English name, lowercase) |
| `labels_tags` | `en:organic` | Filter by label |
| `nutriscore_grade` | `a` | Filter by Nutri-Score (a-e) |
| `nova_groups_tags` | `4` | Filter by NOVA group (1-4) |
| `allergens_tags` | `en:gluten` | Filter by allergen |
| `additives_tags` | `en:e322` | Filter by additive (E-number) |

Multiple values can be combined. All tag filters accept the `en:` prefix
format.

### Response structure

```json
{
  "count": 1234,
  "page": 1,
  "page_count": 25,
  "page_size": 50,
  "skip": 0,
  "products": [
    {
      "code": "3017620422003",
      "product_name": "Nutella",
      ...
    }
  ]
}
```

- `count`: Total matching products (not just this page)
- `products`: Array of product objects
- `page_count`: Total number of pages

## Facet endpoints

Browse products by facet (category, brand, country, etc.):

```
GET /{facet}.json                         # List all values
GET /{facet}/{value}.json                 # Products for a specific value
GET /{facet}/{value}/{facet2}/{value2}.json  # Nested facets
```

**Available facets:** `categories`, `brands`, `countries`, `labels`,
`allergens`, `additives`, `states`, `packaging`

**Example:**
```
GET /category/breakfast-cereals/country/france.json
```

Facet queries are the most rate-limited tier (2/min). Prefer search
queries with filter parameters when possible.

## Nutriment fields

All nutrient values are available per 100g (`_100g`) and per serving
(`_serving`). The `_100g` fields are standard for comparison.

### Core fields (most commonly used)

| Field | Unit | Notes |
|---|---|---|
| `energy-kcal_100g` | kcal | Energy in kilocalories |
| `energy-kj_100g` | kJ | Energy in kilojoules |
| `fat_100g` | g | Total fat |
| `saturated-fat_100g` | g | Saturated fat (note the hyphen) |
| `carbohydrates_100g` | g | Total carbohydrates |
| `sugars_100g` | g | Total sugars |
| `fiber_100g` | g | Dietary fibre |
| `proteins_100g` | g | Protein content |
| `salt_100g` | g | Salt |
| `sodium_100g` | g | Sodium |

### Additional fields

| Field | Unit | Notes |
|---|---|---|
| `fruits-vegetables-nuts_100g` | % | Used in Nutri-Score calculation |
| `trans-fat_100g` | g | Trans fats |
| `cholesterol_100g` | mg | Cholesterol |
| `calcium_100g` | mg | Calcium |
| `iron_100g` | mg | Iron |
| `vitamin-a_100g` | µg | Vitamin A |
| `vitamin-c_100g` | mg | Vitamin C |

**Naming quirk:** Some fields use hyphens (`saturated-fat`), others use
underscores. The helper module's `to_nutriment_df()` normalises all
hyphens to underscores.

## Taxonomy tag format

Categories, labels, allergens, additives, and countries all use taxonomy
tags in this format:

```
{language_code}:{tag-name}
```

Examples:
- `en:breakfast-cereals` (English category)
- `fr:bio` (French label for organic)
- `en:e322` (additive: lecithin)
- `en:gluten` (allergen)
- `en:france` (country)

When filtering, always use the `en:` prefix for English tag names. When
displaying, strip the prefix and replace hyphens with spaces.

## Error handling

- **Product not found**: Returns `{"status": 0, "status_verbose": "product not found"}`
- **Empty search**: Returns `{"count": 0, "products": []}`
- **Rate limited**: Connection may be refused or timeout. The helper module
  prevents this by enforcing delays.
- **Server errors (500)**: Rare, but retry after a few seconds if it happens.

## User-Agent requirement

Every request must include a User-Agent header identifying your
application. Format:

```
AppName/Version (contact@email.com)
```

Requests without a proper User-Agent may be blocked. The helper module
sets this automatically.
