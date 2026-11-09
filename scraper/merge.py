"""
Merge the per-bairro CSVs into one file.

Takes the most recent CSV of each bairro in data/raw/ (old snapshots in subfolders such as
data/raw/sep2025/ are ignored), concatenates them and drops duplicate listings (same URL).

Usage (from the project root):
    .venv\\Scripts\\python -m scraper.merge

Output:
    data/processed/listings_raw.csv
"""
import pandas as pd

from .config import BAIRROS, PROJECT_ROOT, RAW_DATA_DIR

OUTPUT_FILE = PROJECT_ROOT / "data" / "processed" / "listings_raw.csv"


def latest_file_per_bairro() -> dict:
    latest = {}
    for path in sorted(RAW_DATA_DIR.glob("quintoandar_*_*.csv")):
        slug = path.stem[len("quintoandar_"):].rsplit("_", 1)[0]
        latest[slug] = path  # sorted by name -> the last one is the most recent date
    return latest


def merge() -> pd.DataFrame:
    files = latest_file_per_bairro()
    missing = [b for b in BAIRROS if b not in files]

    frames = [pd.read_csv(path) for path in files.values()]
    df = pd.concat(frames, ignore_index=True)
    n_before = len(df)

    # A listing can show up in two neighbouring bairros' search pages: keep the first
    df = df.drop_duplicates(subset="url", keep="first").reset_index(drop=True)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")

    print(f"Merged {len(files)} file(s): {n_before} rows -> {len(df)} after removing "
          f"{n_before - len(df)} duplicate URL(s)")
    print(f"Saved: {OUTPUT_FILE.relative_to(PROJECT_ROOT)}")
    if missing:
        print(f"WARNING: no CSV for {len(missing)} bairro(s): {missing}")
    print("\nListings per zone / bairro:")
    print(df.groupby(["zone", "bairro"]).size().to_string())
    return df


if __name__ == "__main__":
    merge()
