#!/usr/bin/env python3
"""
download_data.py — Download the Open Food Facts parquet and build subsets

Run this once during VM provisioning (or before a workshop).
All files land in data/ relative to your current working directory.

Usage:
    python scripts/download_data.py                  # full + all subsets
    python scripts/download_data.py --subsets eu nl  # full + specific subsets
    python scripts/download_data.py --skip-download  # subsets only (file exists)
    python scripts/download_data.py --list           # show naming schema

Expected output structure:
    data/
    ├── food.parquet        ~7.5 GB  full dataset
    ├── food_eu.parquet     ~1.5 GB  EU27 + UK
    ├── food_us.parquet     ~0.5 GB  United States
    ├── food_fr.parquet     ~0.8 GB  France
    ├── food_de.parquet     ~0.3 GB  Germany
    ├── food_nl.parquet     ~0.15 GB Netherlands
    ├── food_es.parquet     ~0.2 GB  Spain
    ├── food_it.parquet     ~0.2 GB  Italy
    └── food_be.parquet     ~0.1 GB  Belgium
    (sizes are approximate)

Requirements:
    pip install duckdb requests tqdm
"""

import argparse
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Allow running from project root without installing the package
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from off_parquet import (
    DEFAULT_DATA_DIR,
    PARQUET_URL,
    SUBSET_COUNTRY_TAGS,
    SUBSET_FILES,
    OFFParquet,
)

# ---------------------------------------------------------------------------
# Default subset build order
# eu first (largest, most useful for cross-country analysis), then US,
# then EU27 + UK individual countries ordered by OFF data richness
# ---------------------------------------------------------------------------
DEFAULT_SUBSETS = [
    # Regional first
    "eu", "us",
    # High-coverage EU countries (most OFF data)
    "fr", "de", "gb", "es", "it", "be", "nl", "pl", "se", "dk",
    # Medium-coverage
    "at", "fi", "pt", "ro", "hu", "cz", "ie", "gr",
    # Lower-coverage (fewer products in OFF but still valid)
    "sk", "hr", "bg", "si", "lt", "lv", "ee", "lu", "cy", "mt",
]


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_full(data_dir: str = DEFAULT_DATA_DIR) -> Path:
    """Download food.parquet to data_dir. Skips if already present."""
    dest = Path(data_dir) / "food.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        size_gb = dest.stat().st_size / 1e9
        print(f"✓ food.parquet already present ({size_gb:.2f} GB) — skipping download")
        return dest

    try:
        import requests
        from tqdm import tqdm
    except ImportError:
        print("ERROR: pip install requests tqdm")
        sys.exit(1)

    print(f"Downloading Open Food Facts parquet (~7.5 GB)...")
    print(f"  Source : {PARQUET_URL}")
    print(f"  Dest   : {dest}")
    print(f"  Note   : ~10 min on 100 Mbit. Start this before coffee break.\n")

    t0 = time.time()
    with requests.get(PARQUET_URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        with open(dest, "wb") as f, tqdm(
            total=total,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc="food.parquet",
        ) as bar:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
                bar.update(len(chunk))

    elapsed = time.time() - t0
    size_gb = dest.stat().st_size / 1e9
    print(f"\n✓ food.parquet saved ({size_gb:.2f} GB in {elapsed/60:.1f} min)")
    return dest


# ---------------------------------------------------------------------------
# Subset building
# ---------------------------------------------------------------------------

def build_subsets(
    keys: list[str],
    data_dir: str = DEFAULT_DATA_DIR,
    full_path: str | None = None,
) -> None:
    """Build one parquet subset file per key."""
    source = full_path or str(Path(data_dir) / "food.parquet")

    if not Path(source).exists():
        print(f"ERROR: source file not found: {source}")
        print("Run without --skip-download first.")
        sys.exit(1)

    valid = set(SUBSET_COUNTRY_TAGS.keys())
    unknown = [k for k in keys if k not in valid]
    if unknown:
        print(f"ERROR: unknown key(s): {', '.join(unknown)}")
        print(f"Valid keys: {', '.join(sorted(valid))}")
        sys.exit(1)

    print(f"\nBuilding {len(keys)} subset(s) from {source}")
    print("(This uses DuckDB — each subset takes 30s–3min depending on size)\n")

    for key in keys:
        out = Path(data_dir) / SUBSET_FILES[key]
        if out.exists():
            size_mb = out.stat().st_size / 1e6
            print(f"  {key:>4}  →  {out}  ({size_mb:.0f} MB) — already exists, skipping")
            continue

        t0 = time.time()
        print(f"  Building {key}...", end="", flush=True)
        try:
            OFFParquet.build_subset(key, source_path=source, data_dir=data_dir)
            elapsed = time.time() - t0
            print(f"  ({elapsed:.0f}s)")
        except Exception as e:
            print(f"\n  ERROR building {key}: {e}")


# ---------------------------------------------------------------------------
# Summary / listing
# ---------------------------------------------------------------------------

def print_schema() -> None:
    """Print the full naming schema."""
    # Country names for display
    COUNTRY_NAMES = {
        "eu": "EU27 + UK combined",
        "us": "United States",
        "at": "Austria", "be": "Belgium",   "bg": "Bulgaria",
        "hr": "Croatia", "cy": "Cyprus",    "cz": "Czechia",
        "dk": "Denmark", "ee": "Estonia",   "fi": "Finland",
        "fr": "France",  "de": "Germany",   "gr": "Greece",
        "hu": "Hungary", "ie": "Ireland",   "it": "Italy",
        "lv": "Latvia",  "lt": "Lithuania", "lu": "Luxembourg",
        "mt": "Malta",   "nl": "Netherlands","pl": "Poland",
        "pt": "Portugal","ro": "Romania",   "sk": "Slovakia",
        "si": "Slovenia","es": "Spain",     "se": "Sweden",
        "gb": "United Kingdom",
    }

    print("\nOpen Food Facts — Parquet naming schema")
    print("=" * 60)
    print(f"  {'Key':<6}  {'File in data/':<26}  Country / region")
    print("-" * 60)
    print(f"  {'full':<6}  {'food.parquet':<26}  Full dataset (~7.5 GB, 4.5M products)")
    print()

    # Regional
    for key in ("eu", "us"):
        filename = SUBSET_FILES[key]
        name = COUNTRY_NAMES[key]
        print(f"  {key:<6}  {filename:<26}  {name}")
    print()

    # EU27 + UK individual
    eu_keys = [k for k in DEFAULT_SUBSETS if k not in ("eu", "us")]
    for key in eu_keys:
        filename = SUBSET_FILES.get(key, "?")
        name = COUNTRY_NAMES.get(key, key)
        print(f"  {key:<6}  {filename:<26}  {name}")

    print()
    print("Load in Python:")
    print("  from off_parquet import OFFParquet")
    print("  off = OFFParquet.from_key('nl')        # Netherlands")
    print("  off = OFFParquet.from_key('eu')        # EU combined")
    print("  off = OFFParquet.from_key('fr')        # France")
    print("  off = OFFParquet('data/food.parquet')  # Full dataset")


def print_status(data_dir: str = DEFAULT_DATA_DIR) -> None:
    """Show which files are already downloaded."""
    print(f"\nStatus of {data_dir}/")
    print("-" * 50)
    data = Path(data_dir)
    all_files = {"full": "food.parquet", **SUBSET_FILES}
    for key, filename in sorted(all_files.items()):
        p = data / filename
        if p.exists():
            size = p.stat().st_size / 1e9
            print(f"  ✓ {filename:<28} {size:.2f} GB")
        else:
            print(f"  ✗ {filename:<28} (not found)")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Open Food Facts parquet and build regional subsets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/download_data.py                    # download full + build all default subsets
  python scripts/download_data.py --subsets nl be    # download full + NL and BE only
  python scripts/download_data.py --skip-download    # build subsets from existing food.parquet
  python scripts/download_data.py --list             # print naming schema
  python scripts/download_data.py --status           # show which files exist
        """,
    )
    parser.add_argument(
        "--subsets",
        nargs="*",
        metavar="KEY",
        help=(
            f"Subset keys to build. Default: {' '.join(DEFAULT_SUBSETS)}. "
            f"Valid: {', '.join(sorted(SUBSET_COUNTRY_TAGS))}. "
            "Pass --subsets with no args to skip subset building."
        ),
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip downloading food.parquet (assumes it exists in data/).",
    )
    parser.add_argument(
        "--data-dir",
        default=DEFAULT_DATA_DIR,
        help=f"Directory for parquet files. Default: {DEFAULT_DATA_DIR}",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the naming schema and exit.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show which files are already downloaded and exit.",
    )

    args = parser.parse_args()

    if args.list:
        print_schema()
        return

    if args.status:
        print_status(args.data_dir)
        return

    print("Open Food Facts — Data Setup")
    print("=" * 50)

    # Step 1: download full parquet
    if not args.skip_download:
        download_full(args.data_dir)
    else:
        full = Path(args.data_dir) / "food.parquet"
        if not full.exists():
            print(f"ERROR: --skip-download set but {full} not found.")
            sys.exit(1)
        print(f"✓ Skipping download (--skip-download). Using {full}")

    # Step 2: build subsets
    # If --subsets was passed with no arguments, skip subset building
    if args.subsets is not None and len(args.subsets) == 0:
        print("\nNo subsets requested (--subsets with no args). Done.")
        return

    keys = args.subsets if args.subsets is not None else DEFAULT_SUBSETS
    build_subsets(keys, data_dir=args.data_dir)

    # Step 3: final status
    print_status(args.data_dir)
    print("✓ Setup complete. Load data with:")
    print("  from off_parquet import OFFParquet")
    print("  off = OFFParquet.from_key('nl')   # Netherlands")
    print("  off = OFFParquet.from_key('eu')   # EU combined")
    print("  off = OFFParquet.from_key('full') # full dataset")


if __name__ == "__main__":
    main()
