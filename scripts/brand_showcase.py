"""
brand_showcase.py — Generate a self-contained HTML brand analysis page

Given a food brand name, queries the local parquet file with DuckDB and
produces a single HTML file with interactive Chart.js visualisations:
  - Hero stats (product count, NOVA 4%, dominant Nutri-Score)
  - Nutri-Score grade distribution
  - NOVA processing level distribution
  - Nutrition vs. EU/dataset average (for brand's top category)
  - Top product categories
  - Controversial additives
  - Best and worst products by nutrition score
  - Countries presence

Dependencies: duckdb, pandas  (already required by off_parquet.py)
Chart.js 4.x loaded from CDN in the generated HTML.

Usage:
    # From Python
    from brand_showcase import generate_brand_showcase
    generate_brand_showcase("Kellogg's")
    generate_brand_showcase("Alpro", parquet_path="data/food.parquet")

    # From the command line (project root)
    python scripts/brand_showcase.py "Kellogg's"
    python scripts/brand_showcase.py "Alpro" --no-browser
"""

import json
import re
import sys
import webbrowser
from datetime import date
from pathlib import Path
from typing import Optional

# ── allow running from project root without installing ──────────────────────
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from off_parquet import OFFParquet, ADDITIVES_OF_CONCERN, DEFAULT_DATA_DIR

# ---------------------------------------------------------------------------
# E-number lookup (extends the core list with common non-controversial ones)
# ---------------------------------------------------------------------------

ADDITIVE_NAMES: dict[str, str] = {
    **ADDITIVES_OF_CONCERN,
    "en:e100":  "Curcumin (E100) — natural yellow",
    "en:e160a": "Carotenes (E160a) — natural orange",
    "en:e160c": "Paprika extract (E160c)",
    "en:e162":  "Beetroot red (E162)",
    "en:e163":  "Anthocyanins (E163)",
    "en:e200":  "Sorbic acid (E200) — preservative",
    "en:e202":  "Potassium sorbate (E202) — preservative",
    "en:e250":  "Sodium nitrite (E250) — meat preservative",
    "en:e252":  "Potassium nitrate (E252) — meat preservative",
    "en:e270":  "Lactic acid (E270)",
    "en:e300":  "Vitamin C / Ascorbic acid (E300)",
    "en:e306":  "Vitamin E / Tocopherol (E306)",
    "en:e322":  "Lecithin (E322) — emulsifier",
    "en:e330":  "Citric acid (E330)",
    "en:e331":  "Sodium citrates (E331)",
    "en:e332":  "Potassium citrates (E332)",
    "en:e333":  "Calcium citrates (E333)",
    "en:e334":  "Tartaric acid (E334)",
    "en:e336":  "Potassium tartrates (E336)",
    "en:e339":  "Sodium phosphates (E339)",
    "en:e340":  "Potassium phosphates (E340)",
    "en:e341":  "Calcium phosphates (E341)",
    "en:e415":  "Xanthan gum (E415)",
    "en:e422":  "Glycerol (E422)",
    "en:e440":  "Pectin (E440)",
    "en:e450":  "Diphosphates (E450)",
    "en:e451":  "Triphosphates (E451)",
    "en:e452":  "Polyphosphates (E452)",
    "en:e460":  "Cellulose (E460)",
    "en:e461":  "Methyl cellulose (E461)",
    "en:e466":  "Carboxymethylcellulose (E466)",
    "en:e500":  "Sodium carbonates (E500)",
    "en:e503":  "Ammonium carbonates (E503)",
    "en:e551":  "Silicon dioxide (E551)",
    "en:e627":  "Disodium guanylate (E627) — flavour enhancer",
    "en:e631":  "Disodium inosinate (E631) — flavour enhancer",
    "en:e950":  "Acesulfame K (E950) — sweetener",
    "en:e952":  "Cyclamates (E952) — sweetener (banned US)",
    "en:e954":  "Saccharin (E954) — sweetener",
    "en:e960":  "Steviol glycosides (E960) — stevia",
    "en:e965":  "Maltitol (E965) — sugar alcohol",
    "en:e967":  "Xylitol (E967) — sugar alcohol",
}

CONCERN_TAGS = set(ADDITIVES_OF_CONCERN.keys())

# ---------------------------------------------------------------------------
# Nutri-Score & NOVA colours (official)
# ---------------------------------------------------------------------------

NS_COLORS = {
    "a": "#038141", "b": "#85BB2F",
    "c": "#FECB02", "d": "#EE8100", "e": "#E63E11",
    "?": "#d1d5db",
}
NS_BG = {
    "a": "#e8f5e9", "b": "#f1f8e9",
    "c": "#fffde7", "d": "#fff3e0", "e": "#fbe9e7",
    "?": "#f3f4f6",
}

NOVA_COLORS = {1: "#4CAF50", 2: "#8BC34A", 3: "#FF9800", 4: "#F44336", "?": "#d1d5db"}
NOVA_LABELS = {
    1: "Unprocessed",
    2: "Culinary ingredients",
    3: "Processed",
    4: "Ultra-processed",
}


# ---------------------------------------------------------------------------
# Brand normalisation helpers
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    """Convert brand name to a safe filename slug."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _like_pattern(brand: str) -> str:
    """Build a SQL LIKE pattern that handles apostrophes and common variants."""
    base = brand.lower()
    # Replace apostrophes with SQL single-char wildcard _ so "Kellogg's" → "kellogg_s"
    # and still matches the stored "kellogg's" via the _ wildcard
    base = re.sub(r"[''`']", "_", base)
    base = re.sub(r"\s+", "%", base.strip())       # spaces → wildcards
    return f"%{base}%"


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def _collect_data(off: OFFParquet, brand: str) -> dict:
    """Run all DuckDB queries for the brand. Returns a data dict for rendering."""

    pattern = _like_pattern(brand)
    like_clause = f"LOWER(brands) LIKE '{pattern}'"

    # ── 1. Basic counts + Nutri-Score distribution ───────────────────────
    basic = off.con.execute(f"""
        SELECT
            COUNT(*)                                                  AS total,
            COUNT(*) FILTER (WHERE nutriscore_grade IS NOT NULL)      AS has_nutriscore,
            COUNT(*) FILTER (WHERE nova_group IS NOT NULL)            AS has_nova,
            COUNT(*) FILTER (WHERE LOWER(nutriscore_grade) = 'a')    AS ns_a,
            COUNT(*) FILTER (WHERE LOWER(nutriscore_grade) = 'b')    AS ns_b,
            COUNT(*) FILTER (WHERE LOWER(nutriscore_grade) = 'c')    AS ns_c,
            COUNT(*) FILTER (WHERE LOWER(nutriscore_grade) = 'd')    AS ns_d,
            COUNT(*) FILTER (WHERE LOWER(nutriscore_grade) = 'e')    AS ns_e
        FROM food
        WHERE {like_clause}
    """).fetchone()

    if basic is None or basic[0] == 0:
        raise ValueError(
            f"No products found for brand '{brand}'.\n"
            f"  Pattern tried: {pattern}\n"
            "  Try a shorter or simpler variant of the brand name."
        )

    total, has_ns, has_nova = basic[0], basic[1], basic[2]
    ns_counts = {"a": basic[3], "b": basic[4], "c": basic[5], "d": basic[6], "e": basic[7]}
    ns_missing = total - has_ns

    # Most common Nutri-Score grade
    dominant_ns = max(ns_counts, key=lambda k: ns_counts[k]) if has_ns else "?"

    # ── 2. NOVA distribution ─────────────────────────────────────────────
    nova_rows = off.con.execute(f"""
        SELECT nova_group::INTEGER AS g, COUNT(*) AS n
        FROM food
        WHERE {like_clause} AND nova_group IS NOT NULL
        GROUP BY g ORDER BY g
    """).fetchall()
    nova_counts = {r[0]: r[1] for r in nova_rows}
    nova4_pct = round(100 * nova_counts.get(4, 0) / has_nova, 1) if has_nova > 0 else 0.0
    nova_missing = total - has_nova

    # ── 3. Countries ─────────────────────────────────────────────────────
    countries_rows = off.con.execute(f"""
        SELECT c, COUNT(*) AS n
        FROM food, UNNEST(countries_tags) AS t(c)
        WHERE {like_clause} AND countries_tags IS NOT NULL
        GROUP BY c ORDER BY n DESC LIMIT 15
    """).fetchall()
    countries = [
        (r[0].replace("en:", "").replace("-", " ").title(), r[1])
        for r in countries_rows
        if not r[0].startswith("en:unknown")
    ]

    # ── 4. Top categories ─────────────────────────────────────────────────
    cat_rows = off.con.execute(f"""
        SELECT c, COUNT(*) AS n
        FROM food, UNNEST(categories_tags) AS t(c)
        WHERE {like_clause} AND categories_tags IS NOT NULL
        GROUP BY c
        HAVING c LIKE 'en:%' AND n >= 2
        ORDER BY n DESC LIMIT 12
    """).fetchall()
    categories = [
        (r[0].replace("en:", "").replace("-", " ").title(), r[1])
        for r in cat_rows
    ]
    top_category_tag = cat_rows[0][0] if cat_rows else None

    # ── 5. Average nutrition (brand) ──────────────────────────────────────
    # nutriments is STRUCT(name, 100g, ...)[] — extract by name field
    nutr_row = off.con.execute(f"""
        SELECT
            ROUND(AVG(list_extract(list_filter(nutriments, x -> x.name = 'sugars'), 1)."100g"), 1)        AS sugars,
            ROUND(AVG(list_extract(list_filter(nutriments, x -> x.name = 'fat'), 1)."100g"), 1)           AS fat,
            ROUND(AVG(list_extract(list_filter(nutriments, x -> x.name = 'saturated-fat'), 1)."100g"), 1) AS sat_fat,
            ROUND(AVG(list_extract(list_filter(nutriments, x -> x.name = 'salt'), 1)."100g"), 1)          AS salt,
            ROUND(AVG(list_extract(list_filter(nutriments, x -> x.name = 'proteins'), 1)."100g"), 1)      AS proteins,
            ROUND(AVG(list_extract(list_filter(nutriments, x -> x.name = 'energy-kcal'), 1)."100g"), 0)   AS kcal,
            COUNT(*) FILTER (WHERE nutriments IS NOT NULL AND len(nutriments) > 0) AS n_with_nutr
        FROM food
        WHERE {like_clause}
    """).fetchone()

    brand_nutrition = {
        "sugars":   nutr_row[0],
        "fat":      nutr_row[1],
        "sat_fat":  nutr_row[2],
        "salt":     nutr_row[3],
        "proteins": nutr_row[4],
        "kcal":     nutr_row[5],
        "n":        nutr_row[6] or 0,
    }

    # ── 6. Category-average nutrition (comparison baseline) ───────────────
    cat_nutrition = None
    cat_label_short = "dataset average"
    if top_category_tag:
        cat_row = off.con.execute(f"""
            SELECT
                ROUND(AVG(list_extract(list_filter(nutriments, x -> x.name = 'sugars'), 1)."100g"), 1)        AS sugars,
                ROUND(AVG(list_extract(list_filter(nutriments, x -> x.name = 'fat'), 1)."100g"), 1)           AS fat,
                ROUND(AVG(list_extract(list_filter(nutriments, x -> x.name = 'saturated-fat'), 1)."100g"), 1) AS sat_fat,
                ROUND(AVG(list_extract(list_filter(nutriments, x -> x.name = 'salt'), 1)."100g"), 1)          AS salt,
                ROUND(AVG(list_extract(list_filter(nutriments, x -> x.name = 'proteins'), 1)."100g"), 1)      AS proteins
            FROM food
            WHERE list_contains(categories_tags, '{top_category_tag}')
              AND nutriments IS NOT NULL AND len(nutriments) > 0
              AND NOT ({like_clause})
        """).fetchone()
        if cat_row and cat_row[0] is not None:
            cat_nutrition = {
                "sugars":   cat_row[0],
                "fat":      cat_row[1],
                "sat_fat":  cat_row[2],
                "salt":     cat_row[3],
                "proteins": cat_row[4],
            }
            cat_label_short = top_category_tag.replace("en:", "").replace("-", " ").title()
            cat_label_short = f"avg. {cat_label_short} (excl. {brand})"

    # ── 7. Top additives ──────────────────────────────────────────────────
    add_rows = off.con.execute(f"""
        SELECT a, COUNT(DISTINCT code) AS n
        FROM food, UNNEST(additives_tags) AS t(a)
        WHERE {like_clause} AND additives_tags IS NOT NULL
        GROUP BY a ORDER BY n DESC LIMIT 20
    """).fetchall()
    additives = [
        {
            "tag":       r[0],
            "name":      ADDITIVE_NAMES.get(r[0], r[0].replace("en:", "").upper()),
            "count":     r[1],
            "pct":       round(100 * r[1] / total, 1),
            "concerning": r[0] in CONCERN_TAGS,
        }
        for r in add_rows
    ]

    # ── 8. Best products (Nutri-Score A/B) ────────────────────────────────
    # product_name is STRUCT(lang, text)[] — extract readable text
    best_rows = off.con.execute(f"""
        SELECT
            COALESCE(
                array_to_string(list_transform(product_name, x -> x."text"), ' / '),
                brands, '?'
            ) AS pname,
            brands,
            LOWER(nutriscore_grade) AS grade,
            nova_group,
            list_extract(list_filter(nutriments, x -> x.name = 'sugars'), 1)."100g" AS sugars,
            list_extract(list_filter(nutriments, x -> x.name = 'salt'), 1)."100g"   AS salt,
            list_extract(list_filter(nutriments, x -> x.name = 'fat'), 1)."100g"    AS fat
        FROM food
        WHERE {like_clause}
          AND LOWER(nutriscore_grade) IN ('a', 'b')
          AND len(product_name) > 0
        ORDER BY grade ASC,
                 list_extract(list_filter(nutriments, x -> x.name = 'sugars'), 1)."100g" ASC NULLS LAST
        LIMIT 8
    """).fetchall()
    best_products = [
        {"name": r[0], "brands": r[1], "grade": r[2] or "?",
         "nova": r[3], "sugars": r[4], "salt": r[5], "fat": r[6]}
        for r in best_rows
    ]

    # ── 9. Worst products (Nutri-Score D/E) ───────────────────────────────
    worst_rows = off.con.execute(f"""
        SELECT
            COALESCE(
                array_to_string(list_transform(product_name, x -> x."text"), ' / '),
                brands, '?'
            ) AS pname,
            brands,
            LOWER(nutriscore_grade) AS grade,
            nova_group,
            list_extract(list_filter(nutriments, x -> x.name = 'sugars'), 1)."100g" AS sugars,
            list_extract(list_filter(nutriments, x -> x.name = 'salt'), 1)."100g"   AS salt,
            list_extract(list_filter(nutriments, x -> x.name = 'fat'), 1)."100g"    AS fat
        FROM food
        WHERE {like_clause}
          AND LOWER(nutriscore_grade) IN ('d', 'e')
          AND len(product_name) > 0
        ORDER BY grade DESC,
                 list_extract(list_filter(nutriments, x -> x.name = 'sugars'), 1)."100g" DESC NULLS LAST
        LIMIT 8
    """).fetchall()
    worst_products = [
        {"name": r[0], "brands": r[1], "grade": r[2] or "?",
         "nova": r[3], "sugars": r[4], "salt": r[5], "fat": r[6]}
        for r in worst_rows
    ]

    # ── 10. Coverage stats ────────────────────────────────────────────────
    ns_coverage  = round(100 * has_ns / total, 1) if total else 0
    nova_coverage = round(100 * has_nova / total, 1) if total else 0

    return {
        "brand":            brand,
        "slug":             _slug(brand),
        "query_date":       date.today().isoformat(),
        "source_label":     off.path.name,
        # Counts
        "total":            total,
        "has_nutriscore":   has_ns,
        "has_nova":         has_nova,
        "ns_coverage":      ns_coverage,
        "nova_coverage":    nova_coverage,
        # Nutri-Score
        "ns_counts":        ns_counts,
        "ns_missing":       ns_missing,
        "dominant_ns":      dominant_ns,
        # NOVA
        "nova_counts":      nova_counts,
        "nova_missing":     nova_missing,
        "nova4_pct":        nova4_pct,
        # Nutrition
        "brand_nutrition":  brand_nutrition,
        "cat_nutrition":    cat_nutrition,
        "cat_label_short":  cat_label_short,
        # Supporting data
        "countries":        countries[:12],
        "categories":       categories[:10],
        "additives":        additives,
        "best_products":    best_products,
        "worst_products":   worst_products,
    }


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def _fmt(val, suffix="", fallback="—") -> str:
    """Format a nullable number for display."""
    if val is None:
        return fallback
    return f"{val:g}{suffix}"


def _product_rows_html(products: list[dict]) -> str:
    if not products:
        return (
            "<tr><td colspan='5' "
            "style='text-align:center;color:var(--fg-muted);font-style:italic;padding:20px 14px'>"
            "No products with score data"
            "</td></tr>"
        )
    rows = []
    for p in products:
        grade = (p.get("grade") or "?").lower()
        nova  = p.get("nova")
        nova_label = NOVA_LABELS.get(nova, "—") if nova else "—"
        rows.append(
            f"<tr>"
            f"<td class='prod-name mono'>{p['name'] or '—'}</td>"
            f"<td><span class='ns-badge ns-{grade}'>{grade.upper()}</span></td>"
            f"<td><span class='nova-badge nova-{nova or 0}'>{nova or '—'}</span>"
            f" <small style='color:var(--fg-muted)'>{nova_label}</small></td>"
            f"<td class='num'>{_fmt(p.get('sugars'), 'g')}</td>"
            f"<td class='num'>{_fmt(p.get('salt'), 'g')}</td>"
            f"</tr>"
        )
    return "\n".join(rows)


def _render_html(data: dict) -> str:
    brand       = data["brand"]
    total       = data["total"]
    nova4_pct   = data["nova4_pct"]
    dominant_ns = data["dominant_ns"]
    ns_counts   = data["ns_counts"]
    ns_missing  = data["ns_missing"]
    nova_counts = data["nova_counts"]
    nova_miss   = data["nova_missing"]
    bn          = data["brand_nutrition"]
    cn          = data.get("cat_nutrition") or {}
    countries   = data["countries"]
    categories  = data["categories"]
    additives   = data["additives"]

    # ── Chart data (JSON) ─────────────────────────────────────────────────
    ns_labels  = ["A", "B", "C", "D", "E", "No score"]
    ns_values  = [ns_counts.get(g, 0) for g in "abcde"] + [ns_missing]
    ns_colors  = [NS_COLORS[g] for g in "abcde"] + [NS_COLORS["?"]]

    nova_order = [1, 2, 3, 4]
    nova_labels_chart = [NOVA_LABELS[g] for g in nova_order] + ["No data"]
    nova_values = [nova_counts.get(g, 0) for g in nova_order] + [nova_miss]
    nova_colors_chart = [NOVA_COLORS[g] for g in nova_order] + [NOVA_COLORS["?"]]

    cat_labels = [c[0] for c in categories]
    cat_values = [c[1] for c in categories]

    nutr_metrics = ["sugars", "fat", "sat_fat", "salt", "proteins"]
    nutr_labels  = ["Sugars", "Fat", "Sat. fat", "Salt", "Proteins"]
    brand_nutr_vals = [bn.get(m) for m in nutr_metrics]
    cat_nutr_vals   = [cn.get(m) for m in nutr_metrics] if cn else []
    has_nutr_compare = any(v is not None for v in brand_nutr_vals)

    # ── Country pills ─────────────────────────────────────────────────────
    country_pills = "".join(
        f'<span class="country-pill">{c[0]} <small>({c[1]})</small></span>'
        for c in countries
    )

    # ── Additive rows ─────────────────────────────────────────────────────
    add_rows_html = ""
    for a in additives:
        flag = "⚠️" if a["concerning"] else ""
        cls  = "add-concern" if a["concerning"] else ""
        add_rows_html += (
            f'<tr class="{cls}">'
            f'<td>{flag} {a["name"]}</td>'
            f'<td class="num">{a["count"]}</td>'
            f'<td class="num">{a["pct"]}%</td>'
            f'</tr>'
        )

    # ── NOVA 4 stat class (design-system semantic colours) ────────────────
    if nova4_pct >= 60:
        nova4_stat_cls = "fail"
    elif nova4_pct >= 35:
        nova4_stat_cls = "warn"
    else:
        nova4_stat_cls = "ok"

    # ── NS hero colour (official NS palette, design-system exception) ─────
    ns_hero_color = NS_COLORS.get(dominant_ns, NS_COLORS["?"])
    ns_hero_text  = "color:#333;" if dominant_ns == "c" else ""

    # ── Sugar stat hint ───────────────────────────────────────────────────
    if cn and cn.get("sugars") is not None:
        sugar_hint = f"vs. {_fmt(cn.get('sugars'), 'g')} {data['cat_label_short']}"
    else:
        sugar_hint = f"from {bn.get('n', 0)} products with nutrition data"

    # ── Nutrition section (chart or table fallback) ───────────────────────
    if has_nutr_compare:
        nutr_section = (
            f'<div class="card">'
            f'<h3>Nutrition per 100g — {data["cat_label_short"]}</h3>'
            f'<div class="chart-wrap"><canvas id="nutrChart"></canvas></div>'
            f'</div>'
        )
    else:
        nutr_rows = "".join(
            f'<tr><td>{lbl}</td><td class="num">{_fmt(bn.get(m), "g/100g")}</td></tr>'
            for lbl, m in zip(nutr_labels, nutr_metrics)
        )
        nutr_section = (
            f'<div class="card">'
            f'<h3>Average nutrition per 100g</h3>'
            f'<table class="data">'
            f'<thead><tr><th>Nutrient</th><th class="num">Brand avg.</th></tr></thead>'
            f'<tbody>{nutr_rows}</tbody>'
            f'</table>'
            f'</div>'
        )

    # ── Additives section ─────────────────────────────────────────────────
    if add_rows_html:
        additive_section = (
            f'<table class="data">'
            f'<thead><tr><th>Additive</th><th class="num">Products</th>'
            f'<th class="num">% of range</th></tr></thead>'
            f'<tbody>{add_rows_html}</tbody>'
            f'</table>'
        )
    else:
        additive_section = '<p class="hint">No additive data found</p>'

    best_rows_html  = _product_rows_html(data["best_products"])
    worst_rows_html = _product_rows_html(data["worst_products"])

    chart_data = {
        "ns":    {"labels": ns_labels,  "values": ns_values,       "colors": ns_colors},
        "nova":  {"labels": nova_labels_chart, "values": nova_values, "colors": nova_colors_chart},
        "cats":  {"labels": cat_labels, "values": cat_values},
        "nutr":  {
            "labels":      nutr_labels,
            "brand":       brand_nutr_vals,
            "category":    cat_nutr_vals,
            "cat_label":   data["cat_label_short"],
            "has_compare": has_nutr_compare and bool(cat_nutr_vals),
        },
    }

    source_note = (
        f"Based on {total:,} products in <strong>{data['source_label']}</strong> · "
        f"Nutri-Score data: {data['ns_coverage']}% of products · "
        f"NOVA data: {data['nova_coverage']}% · "
        f"Retrieved {data['query_date']}"
    )

    ns_pct_missing   = round(100 * ns_missing / total) if total else 0
    nova_pct_missing = round(100 * nova_miss  / total) if total else 0
    n_countries      = len(countries)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{brand} — Food Profile · Open Food Facts</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    /* ── tokens (workshop design system) ────────────────────────────────── */
    :root {{
      --font-sans: "Inter", system-ui, sans-serif;
      --font-mono: "JetBrains Mono", ui-monospace, Menlo, monospace;

      --bg:           #fafafa;
      --bg-elev:      #ffffff;
      --bg-subtle:    #f4f4f5;
      --fg:           #18181b;
      --fg-dim:       #71717a;
      --fg-muted:     #a1a1aa;
      --border:       #e4e4e7;
      --border-soft:  #f4f4f5;

      --accent:       #00C853;
      --accent-soft:  rgba(0, 200, 83, 0.10);
      --ok:           #16a34a;
      --warn:         #d97706;
      --fail:         #dc2626;

      --radius: 4px;
      --pad:    16px;
      --gap:    10px;
    }}

    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg:          #0a0a0a;
        --bg-elev:     #0f0f10;
        --bg-subtle:   #131316;
        --fg:          #d4d4d8;
        --fg-dim:      #a1a1aa;
        --fg-muted:    #71717a;
        --border:      #27272a;
        --border-soft: #1c1c1f;
        --accent-soft: rgba(0, 200, 83, 0.14);
      }}
    }}

    /* ── reset & base ────────────────────────────────────────────────────── */
    *, *::before, *::after {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      background: var(--bg);
      color: var(--fg);
      font-family: var(--font-sans);
      font-size: 15px;
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }}
    body {{ display: flex; flex-direction: column; min-height: 100vh; }}
    main {{
      flex: 1;
      width: 100%;
      max-width: 1200px;
      margin: 0 auto;
      padding: 32px var(--pad);
    }}

    /* ── typography ──────────────────────────────────────────────────────── */
    h1 {{ font-size: 20px; font-weight: 600; letter-spacing: -0.02em; margin: 0 0 var(--pad); }}
    h2 {{ font-size: 11px; font-weight: 500; text-transform: uppercase;
         letter-spacing: 0.08em; color: var(--fg-dim);
         margin: 28px 0 var(--gap); }}
    h3 {{ font-size: 15px; font-weight: 600; margin: var(--pad) 0 var(--gap); }}
    p  {{ margin: var(--gap) 0; }}
    .hint {{ color: var(--fg-dim); font-size: 13px; }}
    code, .mono {{ font-family: var(--font-mono); font-size: 13px; }}
    code {{
      background: var(--bg-subtle);
      border: 1px solid var(--border-soft);
      padding: 1px 5px;
      border-radius: var(--radius);
    }}

    /* ── data tables ─────────────────────────────────────────────────────── */
    table.data {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      overflow: hidden;
      font-variant-numeric: tabular-nums;
    }}
    table.data th {{
      text-align: left;
      padding: 0 14px;
      height: 38px;
      background: var(--bg-subtle);
      font-size: 11px;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--fg-dim);
      border-bottom: 1px solid var(--border);
    }}
    table.data td {{
      padding: 0 14px;
      height: 38px;
      vertical-align: middle;
      border-bottom: 1px solid var(--border-soft);
    }}
    table.data tr:last-child td {{ border-bottom: 0; }}
    table.data tbody tr:hover   {{ background: var(--bg-subtle); }}
    table.data th.num,
    table.data td.num {{ text-align: right; }}

    /* ── KPI stat tiles ──────────────────────────────────────────────────── */
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 1px;
      background: var(--border);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      overflow: hidden;
      margin: var(--pad) 0;
    }}
    .stat {{ background: var(--bg-elev); padding: var(--pad); }}
    .stat .stat-label {{
      font-size: 11px; font-weight: 500;
      text-transform: uppercase; letter-spacing: 0.08em;
      color: var(--fg-dim); margin-bottom: 6px;
    }}
    .stat .stat-value {{
      font-family: var(--font-mono);
      font-size: 24px; font-weight: 500;
      letter-spacing: -0.02em; color: var(--fg);
      font-variant-numeric: tabular-nums;
    }}
    .stat .stat-hint {{ font-size: 12px; color: var(--fg-dim); margin-top: 4px; }}
    .stat-value.ok   {{ color: var(--ok); }}
    .stat-value.warn {{ color: var(--warn); }}
    .stat-value.fail {{ color: var(--fail); }}

    /* ── badges ──────────────────────────────────────────────────────────── */
    .badge {{
      display: inline-flex; align-items: center; gap: 6px;
      padding: 0 10px; height: 22px; border-radius: var(--radius);
      font-size: 12px; font-weight: 500;
      font-family: var(--font-mono);
      border: 1px solid var(--border);
      background: var(--bg-elev); color: var(--fg-dim);
      text-transform: lowercase;
    }}
    .badge::before {{
      content: ""; display: inline-block;
      width: 6px; height: 6px;
      border-radius: 999px; background: var(--fg-muted);
    }}

    /* ── cards ───────────────────────────────────────────────────────────── */
    .card {{
      background: var(--bg-elev);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: var(--pad);
      margin: var(--gap) 0;
    }}
    .card > *:first-child {{ margin-top: 0; }}
    .card > *:last-child  {{ margin-bottom: 0; }}

    /* ── page header ─────────────────────────────────────────────────────── */
    .page-header {{
      background: var(--bg-subtle);
      border-bottom: 1px solid var(--border);
      padding: 28px var(--pad);
    }}
    .page-header-inner {{ max-width: 1200px; margin: 0 auto; }}
    .page-header h1 {{ margin-bottom: 4px; }}
    .page-subtitle {{ color: var(--fg-dim); font-size: 13px; margin: 0; }}
    .badge-row {{ display: flex; gap: 6px; margin-top: var(--gap); flex-wrap: wrap; }}

    /* ── 2-column grid ───────────────────────────────────────────────────── */
    .grid-2 {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: var(--pad);
      margin: var(--gap) 0;
    }}
    @media (max-width: 760px) {{
      .grid-2 {{ grid-template-columns: 1fr; }}
    }}

    /* ── chart containers ────────────────────────────────────────────────── */
    .chart-wrap {{ position: relative; height: 200px; margin: var(--gap) 0; }}

    /* ── Nutri-Score official badges (official colours per design system) ── */
    .ns-badge {{
      display: inline-flex; align-items: center; justify-content: center;
      width: 24px; height: 24px; border-radius: var(--radius);
      font-size: 12px; font-weight: 700; color: #fff;
    }}
    .ns-a {{ background: #038141; }}
    .ns-b {{ background: #85BB2F; }}
    .ns-c {{ background: #FECB02; color: #333; }}
    .ns-d {{ background: #EE8100; }}
    .ns-e {{ background: #E63E11; }}
    .ns-? {{ background: var(--fg-muted); }}

    /* ── NS hero display (large letter in KPI stat tile) ─────────────────── */
    .ns-hero {{
      display: inline-flex; align-items: center; justify-content: center;
      width: 48px; height: 48px; border-radius: var(--radius);
      font-size: 26px; font-weight: 800; color: #fff;
    }}

    /* ── NOVA processing badges (official colours per design system) ─────── */
    .nova-badge {{
      display: inline-flex; align-items: center; justify-content: center;
      min-width: 20px; height: 20px; border-radius: var(--radius);
      font-size: 11px; font-weight: 700; color: #fff;
      padding: 0 5px; font-family: var(--font-mono);
    }}
    .nova-1 {{ background: #4CAF50; }}
    .nova-2 {{ background: #8BC34A; }}
    .nova-3 {{ background: #FF9800; }}
    .nova-4 {{ background: #F44336; }}
    .nova-0 {{ background: var(--fg-muted); }}

    /* ── country pills ───────────────────────────────────────────────────── */
    .country-pills {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }}
    .country-pill {{
      background: var(--bg-subtle);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 2px 10px;
      font-size: 12px;
      font-family: var(--font-mono);
      color: var(--fg-dim);
    }}
    .country-pill small {{ color: var(--fg-muted); }}

    /* ── additive concern rows ───────────────────────────────────────────── */
    .add-concern td {{ color: var(--fail); }}
    .add-concern td:first-child {{ font-weight: 600; }}

    /* ── product name cell ───────────────────────────────────────────────── */
    td.prod-name {{
      max-width: 220px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    /* ── page footer ─────────────────────────────────────────────────────── */
    .page-footer {{
      background: var(--bg-subtle);
      border-top: 1px solid var(--border);
      padding: var(--pad);
      font-size: 12px;
      color: var(--fg-dim);
      line-height: 1.8;
    }}
    .page-footer .footer-inner {{ max-width: 1200px; margin: 0 auto; }}
    .page-footer strong {{ color: var(--fg); }}
    .page-footer .caveat {{
      color: var(--fg-muted);
      font-size: 11px;
      margin-top: 6px;
    }}
  </style>
</head>
<body>

<!-- ── Page header ─────────────────────────────────────────────────────── -->
<div class="page-header">
  <div class="page-header-inner">
    <h1>{brand} — Food Profile</h1>
    <p class="page-subtitle">Open Food Facts · {data['query_date']} · {data['source_label']}</p>
    <div class="badge-row">
      <span class="badge">{total:,} products</span>
      <span class="badge">{n_countries} {'country' if n_countries == 1 else 'countries'}</span>
      <span class="badge">{data['ns_coverage']}% nutri-score coverage</span>
      <span class="badge">{data['nova_coverage']}% nova coverage</span>
    </div>
  </div>
</div>

<!-- ── Main ────────────────────────────────────────────────────────────── -->
<main>

  <h2>Overview</h2>
  <div class="stats">

    <div class="stat">
      <div class="stat-label">Products in database</div>
      <div class="stat-value">{total:,}</div>
      <div class="stat-hint">across {n_countries} {'country' if n_countries == 1 else 'countries'}</div>
    </div>

    <div class="stat">
      <div class="stat-label">Dominant Nutri-Score</div>
      <div class="stat-value" style="font-size:16px;margin-top:4px">
        <span class="ns-hero" style="background:{ns_hero_color};{ns_hero_text}">{dominant_ns.upper()}</span>
      </div>
      <div class="stat-hint">{data['ns_coverage']}% of products scored</div>
    </div>

    <div class="stat">
      <div class="stat-label">Ultra-processed (NOVA 4)</div>
      <div class="stat-value {nova4_stat_cls}">{nova4_pct}%</div>
      <div class="stat-hint">of products with NOVA data</div>
      <div class="stat-hint">{data['nova_coverage']}% coverage</div>
    </div>

    <div class="stat">
      <div class="stat-label">Avg. sugar per 100g</div>
      <div class="stat-value">{_fmt(bn.get('sugars'), 'g')}</div>
      <div class="stat-hint">{sugar_hint}</div>
    </div>

  </div>

  <h2>Nutrition &amp; Processing Scores</h2>
  <div class="grid-2">

    <div class="card">
      <h3>Nutri-Score distribution</h3>
      <div class="chart-wrap"><canvas id="nsChart"></canvas></div>
      <p class="hint">A = best nutrition · E = worst · {ns_missing} products unscored ({ns_pct_missing}%)</p>
    </div>

    <div class="card">
      <h3>NOVA processing level</h3>
      <div class="chart-wrap"><canvas id="novaChart"></canvas></div>
      <p class="hint">NOVA 4 = ultra-processed · {nova_miss} products without NOVA data ({nova_pct_missing}%)</p>
    </div>

  </div>

  <div class="grid-2">
    {nutr_section}
    <div class="card">
      <h3>Top product categories</h3>
      <div class="chart-wrap"><canvas id="catChart"></canvas></div>
    </div>
  </div>

  <h2>Additives &amp; Geography</h2>
  <div class="grid-2">

    <div class="card">
      <h3>Most common additives (E-numbers)</h3>
      {additive_section}
      <p class="hint" style="margin-top:var(--pad)">⚠️ = additives of journalistic concern (controversial or EU-regulated)</p>
    </div>

    <div class="card">
      <h3>Countries where products are sold</h3>
      <div class="country-pills">{country_pills}</div>
      <p class="hint" style="margin-top:var(--pad)">Based on <code>countries_tags</code> in Open Food Facts. One product may appear in multiple countries.</p>
    </div>

  </div>

  <h2>Product Spotlight</h2>
  <div class="grid-2">

    <div class="card">
      <h3>Best products — Nutri-Score A or B</h3>
      <table class="data">
        <thead><tr>
          <th>Product</th><th>Score</th><th>NOVA</th>
          <th class="num">Sugar</th><th class="num">Salt</th>
        </tr></thead>
        <tbody>{best_rows_html}</tbody>
      </table>
    </div>

    <div class="card">
      <h3>Lowest-scoring products — D or E</h3>
      <table class="data">
        <thead><tr>
          <th>Product</th><th>Score</th><th>NOVA</th>
          <th class="num">Sugar</th><th class="num">Salt</th>
        </tr></thead>
        <tbody>{worst_rows_html}</tbody>
      </table>
    </div>

  </div>

</main>

<!-- ── Footer ──────────────────────────────────────────────────────────── -->
<footer class="page-footer">
  <div class="footer-inner">
    <strong>Data: Open Food Facts (openfoodfacts.org), ODbL v1.0</strong><br>
    {source_note}
    <div class="caveat">
      Caveats: Open Food Facts is a crowdsourced database — coverage and data completeness vary.
      Brand name matching uses fuzzy search; results may include products from related brands.
      Nutri-Score and NOVA values are as recorded in OFF and may not reflect the current product formulation.
      This analysis is for journalistic investigation and should be verified against official product data before publication.
    </div>
  </div>
</footer>

<!-- ── Charts ──────────────────────────────────────────────────────────── -->
<script>
const DATA = {json.dumps(chart_data, ensure_ascii=False)};

// Design-system chart palette (zinc neutrals + green accent)
const DS_BORDER  = '#e4e4e7';  // --border
const DS_FG_DIM  = '#71717a';  // --fg-dim
const DS_ACCENT  = '#00C853';  // --accent
const DS_NEUTRAL = '#a1a1aa';  // --fg-muted

// Nutri-Score horizontal stacked bar
new Chart(document.getElementById('nsChart'), {{
  type: 'bar',
  data: {{
    labels: ['Nutri-Score'],
    datasets: DATA.ns.labels.map((label, i) => ({{
      label: label,
      data: [DATA.ns.values[i]],
      backgroundColor: DATA.ns.colors[i],
    }}))
  }},
  options: {{
    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }}, color: DS_FG_DIM }} }},
      tooltip: {{
        callbacks: {{
          label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.raw}} products (${{Math.round(100 * ctx.raw / {total} || 0)}}%)`
        }}
      }}
    }},
    scales: {{
      x: {{ stacked: true, grid: {{ color: DS_BORDER }}, ticks: {{ font: {{ size: 10 }}, color: DS_FG_DIM }} }},
      y: {{ stacked: true, display: false }}
    }}
  }}
}});

// NOVA doughnut
new Chart(document.getElementById('novaChart'), {{
  type: 'doughnut',
  data: {{
    labels: DATA.nova.labels,
    datasets: [{{ data: DATA.nova.values, backgroundColor: DATA.nova.colors, borderWidth: 0 }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    cutout: '62%',
    plugins: {{
      legend: {{ position: 'right', labels: {{ boxWidth: 12, font: {{ size: 11 }}, color: DS_FG_DIM }} }},
      tooltip: {{
        callbacks: {{
          label: ctx => ` ${{ctx.label}}: ${{ctx.raw}} (${{Math.round(100 * ctx.raw / {total} || 0)}}%)`
        }}
      }}
    }}
  }}
}});

// Categories horizontal bar (accent colour — primary data series)
new Chart(document.getElementById('catChart'), {{
  type: 'bar',
  data: {{
    labels: DATA.cats.labels,
    datasets: [{{ label: 'Products', data: DATA.cats.values,
                  backgroundColor: DS_ACCENT, borderRadius: 2 }}]
  }},
  options: {{
    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: {{ color: DS_BORDER }}, ticks: {{ font: {{ size: 10 }}, color: DS_FG_DIM }} }},
      y: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 10 }}, color: DS_FG_DIM }} }}
    }}
  }}
}});

// Nutrition comparison (brand = neutral, category = accent)
if (DATA.nutr.has_compare && document.getElementById('nutrChart')) {{
  new Chart(document.getElementById('nutrChart'), {{
    type: 'bar',
    data: {{
      labels: DATA.nutr.labels,
      datasets: [
        {{ label: {json.dumps(brand)},      data: DATA.nutr.brand,    backgroundColor: DS_NEUTRAL, borderRadius: 2 }},
        {{ label: DATA.nutr.cat_label, data: DATA.nutr.category, backgroundColor: DS_ACCENT,  borderRadius: 2 }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }}, color: DS_FG_DIM }} }},
        tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.raw}}g/100g` }} }}
      }},
      scales: {{
        x: {{ ticks: {{ font: {{ size: 10 }}, color: DS_FG_DIM }} }},
        y: {{ grid: {{ color: DS_BORDER }}, ticks: {{ font: {{ size: 10 }}, color: DS_FG_DIM, callback: v => v + 'g' }} }}
      }}
    }}
  }});
}}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_brand_showcase(
    brand: str,
    parquet_path: str = f"{DEFAULT_DATA_DIR}/food.parquet",
    output_dir: str = "output",
    open_browser: bool = True,
    verbose: bool = True,
) -> Path:
    """Generate a self-contained HTML brand showcase page.

    Args:
        brand:        Food brand name (e.g. "Kellogg's", "Alpro", "Danone").
        parquet_path: Path to the parquet file. Default: "data/food.parquet".
        output_dir:   Directory to save the HTML file. Default: "output/".
        open_browser: Automatically open the page in the default browser.
        verbose:      Print progress messages.

    Returns:
        Path to the generated HTML file.

    Raises:
        ValueError: If no products found for the brand.
        FileNotFoundError: If the parquet file is not found.

    Example:
        path = generate_brand_showcase("Kellogg's")
        path = generate_brand_showcase("Alpro", parquet_path="data/food.parquet")
        path = generate_brand_showcase("Nestlé", open_browser=False)
    """
    if verbose:
        print(f"🔍  Loading data for '{brand}'...")

    off = OFFParquet(parquet_path, verbose=verbose)

    if verbose:
        print(f"📊  Running brand analysis queries...")

    data = _collect_data(off, brand)

    if verbose:
        print(
            f"✓  Found {data['total']:,} products · "
            f"Nutri-Score {data['ns_coverage']}% coverage · "
            f"NOVA 4: {data['nova4_pct']}%"
        )
        print(f"🎨  Rendering HTML...")

    html = _render_html(data)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{data['slug']}_showcase.html"
    out_path.write_text(html, encoding="utf-8")

    if verbose:
        print(f"✓  Saved: {out_path}")

    if open_browser:
        webbrowser.open(out_path.resolve().as_uri())

    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate a brand food-profile HTML page from Open Food Facts data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/brand_showcase.py "Kellogg's"
  python scripts/brand_showcase.py "Nestlé" --no-browser
  python scripts/brand_showcase.py "Danone" --output-dir reports/
  python scripts/brand_showcase.py "Alpro" --parquet-path ~/data/openfoodfacts/food.parquet
        """,
    )
    parser.add_argument("brand", help="Brand name (e.g. \"Kellogg's\", \"Alpro\")")
    parser.add_argument("--parquet-path", "-p", default=f"{DEFAULT_DATA_DIR}/food.parquet",
                        help="Path to parquet file. Default: data/food.parquet")
    parser.add_argument("--output-dir", "-o", default="output",
                        help="Output directory. Default: output/")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't open in browser after generating")

    args = parser.parse_args()

    try:
        path = generate_brand_showcase(
            brand=args.brand,
            parquet_path=args.parquet_path,
            output_dir=args.output_dir,
            open_browser=not args.no_browser,
        )
        print(f"\n✅  {path}")
    except (ValueError, FileNotFoundError) as e:
        print(f"\n❌  {e}")
        sys.exit(1)
