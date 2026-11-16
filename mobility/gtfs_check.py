"""
Quick look inside a GTFS zip: which kinds of transport it covers and which dates it is valid for.

Usage (from the project root):
    .venv\\Scripts\\python -m mobility.gtfs_check
    .venv\\Scripts\\python -m mobility.gtfs_check data\\external\\gtfs\\other_feed.zip
"""
import sys
import zipfile
from pathlib import Path

import pandas as pd

DEFAULT_GTFS = Path(__file__).resolve().parents[1] / "data" / "external" / "gtfs" / "gtfs_sptrans.zip"

# GTFS route_type codes (basic + the extended codes some feeds use)
ROUTE_TYPES = {0: "tram", 1: "metro", 2: "train", 3: "bus", 4: "ferry", 7: "funicular",
               11: "trolleybus", 12: "monorail", 700: "bus", 400: "metro", 100: "train"}


def read(zf: zipfile.ZipFile, name: str) -> pd.DataFrame:
    """Read one GTFS file as text columns (keeps ids like '0123' intact)."""
    with zf.open(name) as f:
        return pd.read_csv(f, dtype=str, encoding="utf-8-sig")


def main(path: Path) -> None:
    print(f"GTFS: {path}  ({path.stat().st_size / 1e6:.1f} MB)\n")
    with zipfile.ZipFile(path) as zf:
        # 1. Files in the feed, with their uncompressed size
        print("Files:")
        for info in zf.infolist():
            print(f"  {info.filename:<20} {info.file_size / 1e6:8.1f} MB")
        names = set(zf.namelist())

        # 2. Who runs the lines
        if "agency.txt" in names:
            agency = read(zf, "agency.txt")
            print(f"\nAgencies ({len(agency)}):", ", ".join(agency["agency_name"].fillna("?")))

        # 3. Lines by type of transport
        routes = read(zf, "routes.txt")
        routes["kind"] = routes["route_type"].astype(int).map(ROUTE_TYPES).fillna("other")
        print(f"\nRoutes by type ({len(routes)} in total):")
        print(routes["kind"].value_counts().to_string())

        rail = routes[routes["kind"].isin(["metro", "train", "monorail"])]
        if len(rail):
            print("\nRail lines:")
            cols = [c for c in ["route_short_name", "route_long_name", "kind", "agency_id"] if c in rail]
            print(rail[cols].sort_values(cols[:1]).to_string(index=False))
        else:
            print("\nNo metro/train lines: we will need another GTFS for Metrô and CPTM.")

        # 4. Validity dates (r5py's departure date must fall inside them)
        if "calendar.txt" in names:
            cal = read(zf, "calendar.txt")
            print(f"\nCalendar: {cal['start_date'].min()} -> {cal['end_date'].max()} "
                  f"({len(cal)} service patterns)")
        if "calendar_dates.txt" in names:
            cd = read(zf, "calendar_dates.txt")
            print(f"Calendar exceptions: {len(cd)} rows, {cd['date'].min()} -> {cd['date'].max()}")
        if "feed_info.txt" in names:
            print("\nFeed info:")
            print(read(zf, "feed_info.txt").T.to_string(header=False))

        # 5. Size of the network
        stops = read(zf, "stops.txt")
        trips = read(zf, "trips.txt")
        print(f"\nStops: {len(stops):,}   Trips: {len(trips):,}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_GTFS)
