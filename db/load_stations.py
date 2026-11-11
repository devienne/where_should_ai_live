"""Load metro / monorail / train stations of São Paulo city into the `stations` table.

Source: OpenStreetMap via the Overpass API (© OpenStreetMap contributors, ODbL).
Two stages:
  1. download -> data/external/stations_osm.json   (only if missing, or with --refresh)
  2. parse, de-duplicate, replace the `stations` table

    python -m db.load_stations            # uses the saved file if it exists
    python -m db.load_stations --refresh  # downloads again
"""

import argparse
import json
import math
import unicodedata
from pathlib import Path

import httpx
import pandas as pd
from sqlalchemy import text

from .connection import get_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_FILE = PROJECT_ROOT / "data" / "external" / "stations_osm.json"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "where-should-ai-live/0.1 (portfolio project)"
MERGE_DISTANCE_M = 500          # same name + same kind closer than this = one station

# All railway stations inside the São Paulo municipality boundary (admin_level 8).
# "out center" gives a single point even for stations drawn as areas.
OVERPASS_QUERY = """
[out:json][timeout:90];
area["boundary"="administrative"]["admin_level"="8"]["name"="São Paulo"]->.sp;
nwr["railway"="station"](area.sp);
out center tags;
"""


# --------------------------------------------------------------------------
# 1. Download
# --------------------------------------------------------------------------
def download(path: Path = RAW_FILE) -> None:
    print(f"Downloading stations from Overpass ({OVERPASS_URL}) ...")
    response = httpx.post(OVERPASS_URL, data={"data": OVERPASS_QUERY},
                          headers={"User-Agent": USER_AGENT}, timeout=120)
    response.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(response.text, encoding="utf-8")
    print(f"Saved {len(response.json()['elements'])} OSM elements to {path.relative_to(PROJECT_ROOT)}")


# --------------------------------------------------------------------------
# 2. Parse
# --------------------------------------------------------------------------
def station_kind(tags: dict):
    """'metro' / 'monorail' / 'train', or None for stations we don't want."""
    if any(k in tags for k in ("construction", "proposed", "disused", "abandoned")):
        return None
    station = tags.get("station", "")
    if station == "monorail" or tags.get("monorail") == "yes":
        return "monorail"
    if station in ("subway", "light_rail") or tags.get("subway") == "yes":
        return "metro"
    if station == "train" or tags.get("train") == "yes":
        return "train"
    return None                                  # e.g. heritage / tourist railways


def normalise_name(name: str) -> str:
    """'Estação Sé' -> 'se', 'Portuguesa - Tietê' -> 'portuguesatiete' (for matching duplicates only)."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    name = name.removeprefix("estacao ")
    return "".join(ch for ch in name if ch.isalnum())     # ignore spaces, hyphens, dots


def metres_between(lat1, lon1, lat2, lon2) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(a))


def parse(path: Path = RAW_FILE) -> pd.DataFrame:
    elements = json.loads(path.read_text(encoding="utf-8"))["elements"]
    rows = []
    for el in elements:
        tags = el.get("tags", {})
        kind = station_kind(tags)
        name = tags.get("name")
        if kind is None or not name:
            continue
        point = el if el["type"] == "node" else el.get("center")    # ways/relations come with a center
        if not point:
            continue
        rows.append({
            "station_id": f"{el['type']}/{el['id']}",
            "name": name,
            "kind": kind,
            "operator": tags.get("operator"),
            "lat": point["lat"],
            "lon": point["lon"],
        })
    print(f"{len(elements)} OSM elements -> {len(rows)} active metro/monorail/train stations with a name")
    return deduplicate(pd.DataFrame(rows))


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """Merge rows with the same normalised name and kind lying within MERGE_DISTANCE_M."""
    df = df.sort_values("station_id").reset_index(drop=True)   # nodes first: stable, predictable ids
    df["key"] = df["name"].map(normalise_name) + "|" + df["kind"]
    keep = []
    for _, group in df.groupby("key", sort=False):
        kept_here = []
        for _, row in group.iterrows():
            if all(metres_between(row.lat, row.lon, k.lat, k.lon) > MERGE_DISTANCE_M for k in kept_here):
                kept_here.append(row)
        keep.extend(kept_here)
    out = pd.DataFrame(keep).drop(columns="key").reset_index(drop=True)
    print(f"After merging duplicates: {len(out)} stations "
          f"({', '.join(f'{k}: {n}' for k, n in out['kind'].value_counts().items())})")
    return out


# --------------------------------------------------------------------------
# 3. Load
# --------------------------------------------------------------------------
def load(df: pd.DataFrame) -> None:
    engine = get_engine()
    with engine.begin() as conn:                      # one transaction: old rows stay if anything fails
        conn.execute(text("DELETE FROM stations"))    # small reference table: rebuild it completely
        conn.execute(
            text("""INSERT INTO stations (station_id, name, kind, operator, lat, lon)
                    VALUES (:station_id, :name, :kind, :operator, :lat, :lon)"""),
            df.where(df.notna(), None).to_dict("records"),
        )
        n = conn.execute(text("SELECT COUNT(*) FROM stations")).scalar()
    print(f"stations table: {n} rows")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="download again even if the file exists")
    args = parser.parse_args()

    if args.refresh or not RAW_FILE.exists():
        download()
    else:
        print(f"Using saved {RAW_FILE.relative_to(PROJECT_ROOT)} (pass --refresh to download again)")
    load(parse())


if __name__ == "__main__":
    main()
