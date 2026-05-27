#!/usr/bin/env python3
"""
download_data.py — Download the Open Food Facts parquet file

Run this once during VM provisioning (or before a workshop).
The file lands in data/ relative to your current working directory.

Usage:
    python scripts/download_data.py               # download food.parquet
    python scripts/download_data.py --data-dir /path/to/data

Expected output structure:
    data/
    └── food.parquet        ~7.5 GB  full dataset (4.5M+ products)

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

from off_parquet import DEFAULT_DATA_DIR, PARQUET_URL


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


def print_status(data_dir: str = DEFAULT_DATA_DIR) -> None:
    """Show whether the parquet file is downloaded."""
    p = Path(data_dir) / "food.parquet"
    print(f"\nStatus of {data_dir}/")
    print("-" * 50)
    if p.exists():
        size = p.stat().st_size / 1e9
        print(f"  ✓ food.parquet  {size:.2f} GB")
    else:
        print(f"  ✗ food.parquet  (not found)")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download the Open Food Facts parquet file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/download_data.py                    # download food.parquet to data/
  python scripts/download_data.py --data-dir ~/data  # download to a custom directory
  python scripts/download_data.py --status           # show whether the file exists
        """,
    )
    parser.add_argument(
        "--data-dir",
        default=DEFAULT_DATA_DIR,
        help=f"Directory for parquet files. Default: {DEFAULT_DATA_DIR}",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show whether food.parquet is already downloaded and exit.",
    )

    args = parser.parse_args()

    if args.status:
        print_status(args.data_dir)
        return

    print("Open Food Facts — Data Setup")
    print("=" * 50)

    download_full(args.data_dir)
    print_status(args.data_dir)
    print("✓ Setup complete. Load data with:")
    print("  from off_parquet import OFFParquet")
    print("  off = OFFParquet('data/food.parquet')")


if __name__ == "__main__":
    main()
