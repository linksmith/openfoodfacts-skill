#!/usr/bin/env python3
"""
split_parquet.py — Split food.parquet into per-country and regional files

Creates smaller, country-specific parquet files from the full Open Food Facts
dataset. These are served from Hetzner Object Storage so VMs can download only
what they need (faster than pulling 7.5 GB from HuggingFace every time).

Output files (written to <data_dir>/split/):
  food_eu_all.parquet          — All EU-27 countries combined (deduped by code)
  food_us.parquet              — United States
  food_<slug>.parquet          — One file per EU and non-EU European country
  manifest.json                — Row counts, file sizes, generated timestamp

Usage:
    python scripts/split_parquet.py
    python scripts/split_parquet.py --data-dir ~/data/openfoodfacts
    python scripts/split_parquet.py --dry-run        # show what would be created

Runtime: ~30–60 min on a modern laptop (DuckDB reads parquet lazily, no RAM load).

Requirements: pip install duckdb tqdm
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Country definitions — OFF tag slugs (without "en:" prefix)
# ---------------------------------------------------------------------------

# EU-27 member states (as of 2024)
EU_27: dict[str, str] = {
    "austria":         "en:austria",
    "belgium":         "en:belgium",
    "bulgaria":        "en:bulgaria",
    "croatia":         "en:croatia",
    "cyprus":          "en:cyprus",
    "czech-republic":  "en:czech-republic",
    "denmark":         "en:denmark",
    "estonia":         "en:estonia",
    "finland":         "en:finland",
    "france":          "en:france",
    "germany":         "en:germany",
    "greece":          "en:greece",
    "hungary":         "en:hungary",
    "ireland":         "en:ireland",
    "italy":           "en:italy",
    "latvia":          "en:latvia",
    "lithuania":       "en:lithuania",
    "luxembourg":      "en:luxembourg",
    "malta":           "en:malta",
    "netherlands":     "en:netherlands",
    "poland":          "en:poland",
    "portugal":        "en:portugal",
    "romania":         "en:romania",
    "slovakia":        "en:slovakia",
    "slovenia":        "en:slovenia",
    "spain":           "en:spain",
    "sweden":          "en:sweden",
}

# All geographically European countries NOT in the EU
NON_EU_EUROPE: dict[str, str] = {
    "albania":                  "en:albania",
    "andorra":                  "en:andorra",
    "armenia":                  "en:armenia",
    "azerbaijan":               "en:azerbaijan",
    "belarus":                  "en:belarus",
    "bosnia-and-herzegovina":   "en:bosnia-and-herzegovina",
    "georgia":                  "en:georgia",
    "iceland":                  "en:iceland",
    "kosovo":                   "en:kosovo",
    "liechtenstein":            "en:liechtenstein",
    "moldova":                  "en:moldova",
    "monaco":                   "en:monaco",
    "montenegro":               "en:montenegro",
    "north-macedonia":          "en:north-macedonia",
    "norway":                   "en:norway",
    "russia":                   "en:russia",
    "san-marino":               "en:san-marino",
    "serbia":                   "en:serbia",
    "switzerland":              "en:switzerland",
    "turkey":                   "en:turkey",
    "ukraine":                  "en:ukraine",
    "united-kingdom":           "en:united-kingdom",
    "vatican-city":             "en:vatican-city",
}

# Americas
AMERICAS: dict[str, str] = {
    "united-states": "en:united-states",
}

# Combined: all individual country files to generate
ALL_COUNTRIES: dict[str, str] = {**EU_27, **NON_EU_EUROPE, **AMERICAS}

# EU tag list (for the combined eu_all query)
EU_27_TAGS: list[str] = list(EU_27.values())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tag_list_sql(tags: list[str]) -> str:
    """Format a list of OFF tags as a SQL array literal for DuckDB."""
    items = ", ".join(f"'{t}'" for t in tags)
    return f"[{items}]"


def _count_rows(con, parquet_path: str, where: str) -> int:
    """Count matching rows in parquet without full scan of all columns."""
    row = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{parquet_path}') WHERE {where}"
    ).fetchone()
    return row[0]


def _write_country_file(
    con,
    *,
    parquet_path: str,
    out_path: Path,
    where: str,
    label: str,
    dry_run: bool,
) -> dict:
    """Write one country parquet file. Returns manifest entry dict."""
    t0 = time.time()

    if dry_run:
        rows = _count_rows(con, parquet_path, where)
        print(f"  [dry-run] {out_path.name}: {rows:,} rows")
        return {"rows": rows, "size_bytes": 0, "elapsed_s": 0}

    con.execute(f"""
        COPY (
            SELECT * FROM read_parquet('{parquet_path}')
            WHERE {where}
        ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

    elapsed = time.time() - t0
    size = out_path.stat().st_size
    rows = con.execute(f"SELECT COUNT(*) FROM read_parquet('{out_path}')").fetchone()[0]

    size_mb = size / 1e6
    print(f"  ✓ {out_path.name}: {rows:,} rows  {size_mb:.1f} MB  ({elapsed:.0f}s)")
    return {"rows": rows, "size_bytes": size, "elapsed_s": round(elapsed, 1)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def split(data_dir: str = "data", *, dry_run: bool = False) -> None:
    import duckdb

    base = Path(data_dir)
    parquet = base / "food.parquet"
    out_dir = base / "split"

    if not parquet.exists():
        print(
            f"ERROR: {parquet} not found.\n"
            "Download it first:\n"
            "  python scripts/download_data.py --data-dir data\n"
            "(~7.5 GB, ~10 min on 100 Mbit)"
        )
        sys.exit(1)

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    parquet_str = str(parquet)
    manifest: dict[str, dict] = {}

    print(f"\nOpen Food Facts — Parquet splitting")
    print(f"  Source : {parquet}  ({parquet.stat().st_size / 1e9:.2f} GB)")
    print(f"  Output : {out_dir}")
    print(f"  Dry run: {dry_run}")
    print(f"  Total  : {len(ALL_COUNTRIES)} country files + 1 EU combined")
    print()

    # Use a single DuckDB connection for all queries (reuses file handle)
    con = duckdb.connect()
    # Tune for laptop workloads: use multiple threads, cache parquet metadata
    con.execute("SET threads TO 4")
    con.execute("SET enable_progress_bar = false")

    # -----------------------------------------------------------------------
    # 1. EU combined (eu_all) — deduped UNION across all 27 EU countries
    # -----------------------------------------------------------------------
    print(f"[1/{len(ALL_COUNTRIES) + 1}] EU combined (all 27 member states)...")
    eu_tags_sql = _tag_list_sql(EU_27_TAGS)
    eu_where = f"list_has_any(countries_tags, {eu_tags_sql})"
    eu_path = out_dir / "food_eu_all.parquet"
    entry = _write_country_file(
        con,
        parquet_path=parquet_str,
        out_path=eu_path,
        where=eu_where,
        label="EU-27 combined",
        dry_run=dry_run,
    )
    manifest["eu_all"] = {
        "file": "food_eu_all.parquet",
        "label": "EU-27 combined",
        "region": "eu_combined",
        **entry,
    }

    # -----------------------------------------------------------------------
    # 2. Individual country files
    # -----------------------------------------------------------------------
    total = len(ALL_COUNTRIES)
    for i, (slug, tag) in enumerate(ALL_COUNTRIES.items(), start=2):
        region = (
            "eu" if slug in EU_27
            else "non_eu_europe" if slug in NON_EU_EUROPE
            else "americas"
        )
        label = f"{slug} ({tag})"
        out_path = out_dir / f"food_{slug}.parquet"
        where = f"list_contains(countries_tags, '{tag}')"

        print(f"[{i}/{total + 1}] {label}...")
        entry = _write_country_file(
            con,
            parquet_path=parquet_str,
            out_path=out_path,
            where=where,
            label=label,
            dry_run=dry_run,
        )
        manifest[slug] = {
            "file": f"food_{slug}.parquet",
            "label": slug.replace("-", " ").title(),
            "tag": tag,
            "region": region,
            **entry,
        }

    con.close()

    # -----------------------------------------------------------------------
    # 3. Write manifest.json
    # -----------------------------------------------------------------------
    manifest_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_parquet": str(parquet),
        "source_size_bytes": parquet.stat().st_size,
        "total_files": len(manifest),
        "files": manifest,
    }

    if not dry_run:
        manifest_path = out_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest_data, indent=2))
        print(f"\n✓ manifest.json written to {manifest_path}")

    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    total_rows = sum(v["rows"] for v in manifest.values())
    total_size = sum(v["size_bytes"] for v in manifest.values())
    print(f"  Files    : {len(manifest)}")
    print(f"  Total rows: {total_rows:,}  (sum across files; products in multiple countries counted multiple times)")
    print(f"  Total size: {total_size / 1e9:.2f} GB")

    # Countries with low data
    thin = [(k, v) for k, v in manifest.items() if v["rows"] < 5_000 and k != "eu_all"]
    if thin:
        print(f"\n  ⚠ Low-data countries (<5,000 rows):")
        for slug, v in sorted(thin, key=lambda x: x[1]["rows"]):
            print(f"    {slug}: {v['rows']:,} rows")

    if dry_run:
        print("\n[dry-run] No files written. Remove --dry-run to generate files.")
    else:
        print(f"\n✓ All files written to {out_dir}/")
        print("Next step: upload to S3 with scripts/upload_parquet.py")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split food.parquet into per-country parquet files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/split_parquet.py
  python scripts/split_parquet.py --data-dir ~/data/openfoodfacts
  python scripts/split_parquet.py --dry-run
        """,
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory containing food.parquet. Split files go to <data-dir>/split/. Default: data",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count rows and show what would be created without writing files.",
    )
    args = parser.parse_args()
    split(args.data_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
