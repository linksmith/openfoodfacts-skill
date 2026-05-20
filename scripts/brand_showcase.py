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
    generate_brand_showcase("Alpro", parquet_key="nl")

    # From the command line (project root)
    python scripts/brand_showcase.py "Kellogg's"
    python scripts/brand_showcase.py "Alpro" --parquet-key eu --no-browser
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

from off_parquet import OFFParquet, ADDITIVES_OF_CONCERN, DEFAULT_DATA_DIR, SUBSET_FILES

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
    base = re.sub(r"[''`']", "", brand.lower())   # strip apostrophes
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
        SELECT UNNEST(countries_tags) AS c, COUNT(*) AS n
        FROM food
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
        SELECT UNNEST(categories_tags) AS c, COUNT(*) AS n
        FROM food
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
    nutr_row = off.con.execute(f"""
        SELECT
            ROUND(AVG(TRY_CAST(nutriments['sugars_100g'] AS FLOAT)), 1)        AS sugars,
            ROUND(AVG(TRY_CAST(nutriments['fat_100g'] AS FLOAT)), 1)           AS fat,
            ROUND(AVG(TRY_CAST(nutriments['saturated-fat_100g'] AS FLOAT)), 1) AS sat_fat,
            ROUND(AVG(TRY_CAST(nutriments['salt_100g'] AS FLOAT)), 1)          AS salt,
            ROUND(AVG(TRY_CAST(nutriments['proteins_100g'] AS FLOAT)), 1)      AS proteins,
            ROUND(AVG(TRY_CAST(nutriments['energy-kcal_100g'] AS FLOAT)), 0)   AS kcal,
            COUNT(*) FILTER (WHERE nutriments IS NOT NULL)                     AS n_with_nutr
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
                ROUND(AVG(TRY_CAST(nutriments['sugars_100g'] AS FLOAT)), 1)        AS sugars,
                ROUND(AVG(TRY_CAST(nutriments['fat_100g'] AS FLOAT)), 1)           AS fat,
                ROUND(AVG(TRY_CAST(nutriments['saturated-fat_100g'] AS FLOAT)), 1) AS sat_fat,
                ROUND(AVG(TRY_CAST(nutriments['salt_100g'] AS FLOAT)), 1)          AS salt,
                ROUND(AVG(TRY_CAST(nutriments['proteins_100g'] AS FLOAT)), 1)      AS proteins
            FROM food
            WHERE list_contains(categories_tags, '{top_category_tag}')
              AND nutriments IS NOT NULL
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
        SELECT UNNEST(additives_tags) AS a, COUNT(DISTINCT code) AS n
        FROM food
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
    best_rows = off.con.execute(f"""
        SELECT
            product_name,
            brands,
            LOWER(nutriscore_grade) AS grade,
            nova_group,
            TRY_CAST(nutriments['sugars_100g'] AS FLOAT) AS sugars,
            TRY_CAST(nutriments['salt_100g'] AS FLOAT)   AS salt,
            TRY_CAST(nutriments['fat_100g'] AS FLOAT)    AS fat
        FROM food
        WHERE {like_clause}
          AND LOWER(nutriscore_grade) IN ('a', 'b')
          AND product_name IS NOT NULL AND product_name != ''
        ORDER BY grade ASC,
                 TRY_CAST(nutriments['sugars_100g'] AS FLOAT) ASC NULLS LAST
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
            product_name,
            brands,
            LOWER(nutriscore_grade) AS grade,
            nova_group,
            TRY_CAST(nutriments['sugars_100g'] AS FLOAT) AS sugars,
            TRY_CAST(nutriments['salt_100g'] AS FLOAT)   AS salt,
            TRY_CAST(nutriments['fat_100g'] AS FLOAT)    AS fat
        FROM food
        WHERE {like_clause}
          AND LOWER(nutriscore_grade) IN ('d', 'e')
          AND product_name IS NOT NULL AND product_name != ''
        ORDER BY grade DESC,
                 TRY_CAST(nutriments['sugars_100g'] AS FLOAT) DESC NULLS LAST
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
        return "<tr><td colspan='5' class='empty'>No products with score data</td></tr>"
    rows = []
    for p in products:
        grade = (p.get("grade") or "?").lower()
        nova  = p.get("nova")
        nova_label = NOVA_LABELS.get(nova, "—") if nova else "—"
        rows.append(f"""
        <tr>
          <td class="prod-name">{p['name'] or '—'}</td>
          <td><span class="ns-badge ns-{grade}">{grade.upper()}</span></td>
          <td><span class="nova-badge nova-{nova or 0}">{nova or '—'}</span> <small>{nova_label}</small></td>
          <td class="num">{_fmt(p.get('sugars'), 'g')}</td>
          <td class="num">{_fmt(p.get('salt'), 'g')}</td>
        </tr>""")
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

    # Nutrition comparison bars (only if both brand + category data exist)
    nutr_metrics = ["sugars", "fat", "sat_fat", "salt", "proteins"]
    nutr_labels  = ["Sugars", "Fat", "Sat. fat", "Salt", "Proteins"]
    brand_nutr_vals = [bn.get(m) for m in nutr_metrics]
    cat_nutr_vals   = [cn.get(m) for m in nutr_metrics] if cn else []
    has_nutr_compare = any(v is not None for v in brand_nutr_vals)

    # ── Country pills HTML ────────────────────────────────────────────────
    country_pills = "".join(
        f'<span class="country-pill">{c[0]} <small>({c[1]})</small></span>'
        for c in countries
    )

    # ── Additive rows HTML ────────────────────────────────────────────────
    add_rows = ""
    for a in additives:
        flag = "⚠️" if a["concerning"] else ""
        cls  = "add-concern" if a["concerning"] else ""
        add_rows += (
            f'<tr class="{cls}">'
            f'<td>{flag} {a["name"]}</td>'
            f'<td class="num">{a["count"]}</td>'
            f'<td class="num">{a["pct"]}%</td>'
            f'</tr>'
        )

    # ── NOVA 4 colour for hero card ───────────────────────────────────────
    if nova4_pct >= 60:
        nova4_hero_cls = "hero-danger"
    elif nova4_pct >= 35:
        nova4_hero_cls = "hero-warning"
    else:
        nova4_hero_cls = "hero-ok"

    # ── Dominant NS color for hero card ──────────────────────────────────
    ns_hero_color = NS_COLORS.get(dominant_ns, NS_COLORS["?"])
    ns_hero_bg    = NS_BG.get(dominant_ns, NS_BG["?"])

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

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{brand} — Food Profile · Open Food Facts</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg:       #f0f4f8;
      --card:     #ffffff;
      --hdr:      #0f172a;
      --text:     #1e293b;
      --muted:    #64748b;
      --border:   #e2e8f0;
      --radius:   10px;
      --shadow:   0 1px 3px rgba(0,0,0,.08), 0 4px 12px rgba(0,0,0,.06);
    }}
    body {{ font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background: var(--bg); color: var(--text); line-height: 1.5; }}
    /* ── Header ────────────────────────────────────────────────────── */
    header {{ background: var(--hdr); color: #f8fafc; padding: 2rem 2.5rem 1.8rem; }}
    header .brand-name {{ font-size: 2.6rem; font-weight: 800; letter-spacing: -.02em;
                          line-height: 1.1; }}
    header .subtitle {{ font-size: .95rem; color: #94a3b8; margin-top: .35rem; }}
    header .badge-row {{ display: flex; gap: .6rem; margin-top: 1rem; flex-wrap: wrap; }}
    header .badge {{ background: rgba(255,255,255,.12); border-radius: 99px;
                     font-size: .75rem; padding: .25rem .75rem; color: #cbd5e1; }}
    /* ── Layout ────────────────────────────────────────────────────── */
    main {{ max-width: 1200px; margin: 0 auto; padding: 1.8rem 1.5rem 3rem; }}
    .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; margin-bottom: 1.2rem; }}
    .grid-3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1.2rem; margin-bottom: 1.2rem; }}
    .grid-4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1.2rem; margin-bottom: 1.2rem; }}
    .span-2 {{ grid-column: span 2; }}
    @media (max-width: 780px) {{
      .grid-2, .grid-3, .grid-4 {{ grid-template-columns: 1fr; }}
      .span-2 {{ grid-column: span 1; }}
    }}
    /* ── Cards ─────────────────────────────────────────────────────── */
    .card {{ background: var(--card); border-radius: var(--radius);
             box-shadow: var(--shadow); padding: 1.4rem 1.5rem; }}
    .card-title {{ font-size: .7rem; font-weight: 700; letter-spacing: .08em;
                   text-transform: uppercase; color: var(--muted); margin-bottom: 1rem; }}
    /* ── Hero stats ─────────────────────────────────────────────────── */
    .hero-stat .stat-num {{ font-size: 2.8rem; font-weight: 800; line-height: 1; }}
    .hero-stat .stat-lbl {{ font-size: .78rem; color: var(--muted); margin-top: .3rem; }}
    .hero-stat .stat-sub {{ font-size: .72rem; color: var(--muted); margin-top: .15rem; }}
    .hero-danger .stat-num {{ color: #dc2626; }}
    .hero-warning .stat-num {{ color: #d97706; }}
    .hero-ok      .stat-num {{ color: #16a34a; }}
    /* ── Nutri-Score badge ──────────────────────────────────────────── */
    .ns-badge {{
      display: inline-block; font-size: .78rem; font-weight: 800;
      padding: .15em .45em; border-radius: 4px; color: #fff;
      vertical-align: middle;
    }}
    .ns-a {{ background: {NS_COLORS['a']}; }}
    .ns-b {{ background: {NS_COLORS['b']}; }}
    .ns-c {{ background: {NS_COLORS['c']}; color: #333; }}
    .ns-d {{ background: {NS_COLORS['d']}; }}
    .ns-e {{ background: {NS_COLORS['e']}; }}
    .ns-? {{ background: {NS_COLORS['?']}; color: #555; }}
    /* ── NOVA badge ────────────────────────────────────────────────── */
    .nova-badge {{
      display: inline-block; font-size: .78rem; font-weight: 700;
      padding: .1em .4em; border-radius: 4px; color: #fff;
      vertical-align: middle;
    }}
    .nova-1 {{ background: {NOVA_COLORS[1]}; }}
    .nova-2 {{ background: {NOVA_COLORS[2]}; }}
    .nova-3 {{ background: {NOVA_COLORS[3]}; }}
    .nova-4 {{ background: {NOVA_COLORS[4]}; }}
    .nova-0 {{ background: {NOVA_COLORS['?']}; color: #555; }}
    /* ── Large NS display in hero card ─────────────────────────────── */
    .ns-hero-display {{
      width: 72px; height: 72px; border-radius: 12px;
      display: flex; align-items: center; justify-content: center;
      font-size: 2.4rem; font-weight: 900; color: #fff;
      background: {ns_hero_color}; margin-bottom: .6rem;
    }}
    /* ── Charts ────────────────────────────────────────────────────── */
    .chart-wrap {{ position: relative; }}
    /* ── Tables ────────────────────────────────────────────────────── */
    table {{ width: 100%; border-collapse: collapse; font-size: .8rem; }}
    th {{ font-size: .68rem; font-weight: 600; letter-spacing: .05em;
          text-transform: uppercase; color: var(--muted);
          padding: .45rem .6rem; border-bottom: 2px solid var(--border);
          text-align: left; }}
    td {{ padding: .45rem .6rem; border-bottom: 1px solid var(--border);
          vertical-align: middle; }}
    tr:last-child td {{ border-bottom: none; }}
    .prod-name {{ font-weight: 500; max-width: 220px;
                  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .empty {{ color: var(--muted); font-style: italic; text-align: center;
              padding: 1.5rem 0; }}
    /* ── Additives ──────────────────────────────────────────────────── */
    .add-concern td {{ color: #b91c1c; }}
    .add-concern td:first-child {{ font-weight: 600; }}
    /* ── Country pills ──────────────────────────────────────────────── */
    .country-pills {{ display: flex; flex-wrap: wrap; gap: .45rem; margin-top: .2rem; }}
    .country-pill {{
      background: #eff6ff; color: #1d4ed8; border-radius: 99px;
      padding: .2rem .65rem; font-size: .75rem; font-weight: 500;
    }}
    .country-pill small {{ color: #93c5fd; }}
    /* ── Footer ────────────────────────────────────────────────────── */
    footer {{ background: var(--hdr); color: #94a3b8; font-size: .75rem;
              padding: 1.2rem 2.5rem; margin-top: 2rem; line-height: 1.7; }}
    footer strong {{ color: #cbd5e1; }}
    footer .caveat {{ color: #64748b; font-size: .7rem; margin-top: .4rem; }}
  </style>
</head>
<body>

<!-- ── Header ──────────────────────────────────────────────────────── -->
<header>
  <div class="brand-name">{brand}</div>
  <div class="subtitle">Food Product Profile · Open Food Facts</div>
  <div class="badge-row">
    <span class="badge">🛒 {total:,} products</span>
    <span class="badge">🌍 {len(countries)} countries</span>
    <span class="badge">📅 {data['query_date']}</span>
    <span class="badge">📂 {data['source_label']}</span>
  </div>
</header>

<!-- ── Main ────────────────────────────────────────────────────────── -->
<main>

  <!-- Hero stats row -->
  <div class="grid-4" style="margin-bottom:1.2rem">

    <!-- Total products -->
    <div class="card hero-stat">
      <div class="card-title">Products in database</div>
      <div class="stat-num">{total:,}</div>
      <div class="stat-lbl">across {len(countries)} {'country' if len(countries)==1 else 'countries'}</div>
    </div>

    <!-- Dominant Nutri-Score -->
    <div class="card hero-stat">
      <div class="card-title">Most common Nutri-Score</div>
      <div class="ns-hero-display">{dominant_ns.upper()}</div>
      <div class="stat-lbl">{data['ns_coverage']}% of products scored</div>
    </div>

    <!-- NOVA 4 % -->
    <div class="card hero-stat {nova4_hero_cls}">
      <div class="card-title">Ultra-processed (NOVA 4)</div>
      <div class="stat-num">{nova4_pct}%</div>
      <div class="stat-lbl">of products with NOVA data</div>
      <div class="stat-sub">{data['nova_coverage']}% coverage</div>
    </div>

    <!-- Avg sugar (if available) -->
    <div class="card hero-stat">
      <div class="card-title">Avg. sugar per 100g</div>
      <div class="stat-num" style="font-size:2.2rem">{_fmt(bn.get('sugars'), 'g')}</div>
      <div class="stat-lbl">{'vs. ' + _fmt(cn.get('sugars'), 'g') + ' ' + data['cat_label_short'] if cn else 'from ' + str(bn.get('n', 0)) + ' products'}</div>
    </div>

  </div>

  <!-- Nutri-Score + NOVA -->
  <div class="grid-2">

    <div class="card">
      <div class="card-title">Nutri-Score distribution</div>
      <div class="chart-wrap" style="height:180px">
        <canvas id="nsChart"></canvas>
      </div>
      <div style="font-size:.72rem;color:var(--muted);margin-top:.6rem">
        A = best nutrition &nbsp;·&nbsp; E = worst &nbsp;·&nbsp;
        {ns_missing} products have no score ({round(100*ns_missing/total) if total else 0}%)
      </div>
    </div>

    <div class="card">
      <div class="card-title">NOVA processing level</div>
      <div class="chart-wrap" style="height:180px">
        <canvas id="novaChart"></canvas>
      </div>
      <div style="font-size:.72rem;color:var(--muted);margin-top:.6rem">
        NOVA 4 = ultra-processed &nbsp;·&nbsp;
        {nova_miss} products have no NOVA data ({round(100*nova_miss/total) if total else 0}%)
      </div>
    </div>

  </div>

  <!-- Nutrition comparison + Categories -->
  <div class="grid-2">

    {'<div class="card"><div class="card-title">Nutrition per 100g — brand vs. ' + data['cat_label_short'] + '</div><div class="chart-wrap" style="height:200px"><canvas id="nutrChart"></canvas></div></div>' if has_nutr_compare else '<div class="card"><div class="card-title">Average nutrition per 100g</div><table><thead><tr><th>Nutrient</th><th class=\'num\'>Brand avg.</th></tr></thead><tbody>' + "".join(f'<tr><td>{l}</td><td class="num">{_fmt(bn.get(m), "g/100g")}</td></tr>' for l, m in zip(nutr_labels, nutr_metrics)) + '</tbody></table></div>'}

    <div class="card">
      <div class="card-title">Top product categories</div>
      <div class="chart-wrap" style="height:200px">
        <canvas id="catChart"></canvas>
      </div>
    </div>

  </div>

  <!-- Additives + Countries -->
  <div class="grid-2">

    <div class="card">
      <div class="card-title">Most common additives (E-numbers)</div>
      {'<table><thead><tr><th>Additive</th><th class="num">Products</th><th class="num">%</th></tr></thead><tbody>' + add_rows + '</tbody></table>' if additives else '<p style="color:var(--muted);font-size:.85rem;padding:.5rem 0">No additive data found</p>'}
      <div style="font-size:.7rem;color:var(--muted);margin-top:.6rem">
        ⚠️ = additives of journalistic concern (controversial or EU-regulated)
      </div>
    </div>

    <div class="card">
      <div class="card-title">Countries where products are sold</div>
      <div class="country-pills">{country_pills}</div>
      <div style="font-size:.72rem;color:var(--muted);margin-top:.8rem">
        Based on countries_tags in Open Food Facts. One product can appear in multiple countries.
      </div>
    </div>

  </div>

  <!-- Best & Worst products -->
  <div class="grid-2">

    <div class="card">
      <div class="card-title">✅ Best products (Nutri-Score A or B)</div>
      <table>
        <thead><tr>
          <th>Product</th><th>Score</th><th>NOVA</th>
          <th class="num">Sugar</th><th class="num">Salt</th>
        </tr></thead>
        <tbody>{best_rows_html}</tbody>
      </table>
    </div>

    <div class="card">
      <div class="card-title">🔴 Lowest Nutri-Score products (D or E)</div>
      <table>
        <thead><tr>
          <th>Product</th><th>Score</th><th>NOVA</th>
          <th class="num">Sugar</th><th class="num">Salt</th>
        </tr></thead>
        <tbody>{worst_rows_html}</tbody>
      </table>
    </div>

  </div>

</main>

<!-- ── Footer ──────────────────────────────────────────────────────── -->
<footer>
  <strong>Data: Open Food Facts (openfoodfacts.org), ODbL v1.0</strong><br>
  {source_note}
  <div class="caveat">
    Caveats: Open Food Facts is a crowdsourced database — coverage and data completeness vary.
    Brand name matching uses fuzzy search; results may include products from related brands.
    Nutri-Score and NOVA values are as recorded in OFF and may not reflect the current product formulation.
    This analysis is for journalistic investigation and should be verified against official product data before publication.
  </div>
</footer>

<!-- ── Charts ──────────────────────────────────────────────────────── -->
<script>
const DATA = {json.dumps(chart_data, ensure_ascii=False)};

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
      legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }},
      tooltip: {{
        callbacks: {{
          label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.raw}} products (${{Math.round(100*ctx.raw/{total}||0)}}%)`
        }}
      }}
    }},
    scales: {{
      x: {{ stacked: true, grid: {{ display: false }}, ticks: {{ font: {{ size: 10 }} }} }},
      y: {{ stacked: true, display: false }}
    }}
  }}
}});

// NOVA doughnut
new Chart(document.getElementById('novaChart'), {{
  type: 'doughnut',
  data: {{
    labels: DATA.nova.labels,
    datasets: [{{ data: DATA.nova.values, backgroundColor: DATA.nova.colors,
                  borderWidth: 2, borderColor: '#fff' }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    cutout: '62%',
    plugins: {{
      legend: {{ position: 'right', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }},
      tooltip: {{
        callbacks: {{
          label: ctx => ` ${{ctx.label}}: ${{ctx.raw}} (${{Math.round(100*ctx.raw/{total}||0)}}%)`
        }}
      }}
    }}
  }}
}});

// Categories horizontal bar
new Chart(document.getElementById('catChart'), {{
  type: 'bar',
  data: {{
    labels: DATA.cats.labels,
    datasets: [{{ label: 'Products', data: DATA.cats.values,
                  backgroundColor: '#3b82f6', borderRadius: 4 }}]
  }},
  options: {{
    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: {{ color: '#f1f5f9' }}, ticks: {{ font: {{ size: 10 }} }} }},
      y: {{ ticks: {{ font: {{ size: 10 }} }} }}
    }}
  }}
}});

// Nutrition comparison (only rendered if has_compare)
if (DATA.nutr.has_compare && document.getElementById('nutrChart')) {{
  new Chart(document.getElementById('nutrChart'), {{
    type: 'bar',
    data: {{
      labels: DATA.nutr.labels,
      datasets: [
        {{ label: '{brand}', data: DATA.nutr.brand, backgroundColor: '#0f172a', borderRadius: 3 }},
        {{ label: DATA.nutr.cat_label, data: DATA.nutr.category, backgroundColor: '#93c5fd', borderRadius: 3 }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }},
        tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.raw}}g/100g` }} }}
      }},
      scales: {{
        x: {{ ticks: {{ font: {{ size: 10 }} }} }},
        y: {{ grid: {{ color: '#f1f5f9' }}, ticks: {{ font: {{ size: 10 }}, callback: v => v+'g' }} }}
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
    parquet_key: str = "eu",
    parquet_path: Optional[str] = None,
    output_dir: str = "output",
    open_browser: bool = True,
    verbose: bool = True,
) -> Path:
    """Generate a self-contained HTML brand showcase page.

    Args:
        brand:       Food brand name (e.g. "Kellogg's", "Alpro", "Danone").
        parquet_key: Which parquet subset to query. Default "eu" (EU+UK).
                     Use "full" for global data, "fr" for France-only, etc.
        parquet_path: Explicit path to parquet file (overrides parquet_key).
        output_dir:  Directory to save the HTML file. Default: "output/".
        open_browser: Automatically open the page in the default browser.
        verbose:     Print progress messages.

    Returns:
        Path to the generated HTML file.

    Raises:
        ValueError: If no products found for the brand.
        FileNotFoundError: If the parquet file is not found.

    Example:
        path = generate_brand_showcase("Kellogg's")
        path = generate_brand_showcase("Alpro", parquet_key="nl")
        path = generate_brand_showcase("Nestlé", parquet_key="full", open_browser=False)
    """
    if verbose:
        print(f"🔍  Loading data for '{brand}'...")

    if parquet_path:
        off = OFFParquet(parquet_path, verbose=verbose)
    else:
        off = OFFParquet.from_key(parquet_key, verbose=verbose)

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
  python scripts/brand_showcase.py "Alpro" --parquet-key nl
  python scripts/brand_showcase.py "Nestlé" --parquet-key full --no-browser
  python scripts/brand_showcase.py "Danone" --output-dir reports/
        """,
    )
    parser.add_argument("brand", help="Brand name (e.g. \"Kellogg's\", \"Alpro\")")
    parser.add_argument(
        "--parquet-key", "-k", default="eu",
        help=f"Parquet subset key. Valid: {', '.join(sorted(SUBSET_FILES))}. Default: eu",
    )
    parser.add_argument("--parquet-path", "-p", default=None,
                        help="Explicit path to parquet file (overrides --parquet-key)")
    parser.add_argument("--output-dir", "-o", default="output",
                        help="Output directory. Default: output/")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't open in browser after generating")

    args = parser.parse_args()

    try:
        path = generate_brand_showcase(
            brand=args.brand,
            parquet_key=args.parquet_key,
            parquet_path=args.parquet_path,
            output_dir=args.output_dir,
            open_browser=not args.no_browser,
        )
        print(f"\n✅  {path}")
    except (ValueError, FileNotFoundError) as e:
        print(f"\n❌  {e}")
        sys.exit(1)
