"""
Smoke test for r5py on our own data: build the São Paulo transport network (OSM + GTFS)
and compute transit travel times from Sé to three well-known places.

Usage (from the project root):
    .venv\\Scripts\\python -m mobility.r5_smoke_test

The first run downloads the R5 routing engine (a Java .jar) from GitHub and caches it.
Building the network takes a few minutes and a few GB of memory.
"""
import datetime
import time
from pathlib import Path

import geopandas as gpd
import r5py
from shapely.geometry import Point

ROOT = Path(__file__).resolve().parents[1]
OSM = ROOT / "data" / "external" / "osm" / "sao_paulo.osm.pbf"
GTFS = ROOT / "data" / "external" / "gtfs" / "gtfs_sptrans.zip"

# A weekday inside the GTFS calendar (2023-10-01 .. 2027-06-01), at 8 a.m.
DEPARTURE = datetime.datetime(2026, 11, 17, 8, 0)    # a Tuesday

ORIGINS = {"Sé": (-23.5503, -46.6339)}
DESTINATIONS = {
    "MASP (Av. Paulista)": (-23.5614, -46.6559),     # ~3 km, metro Line 2 nearby
    "Itaquera": (-23.5420, -46.4710),                # ~17 km east, end of Line 3
    "Santo Amaro": (-23.6545, -46.7100),             # ~14 km south-west, Line 5
}


def points(places: dict) -> gpd.GeoDataFrame:
    """Turn {name: (lat, lon)} into the GeoDataFrame r5py expects (an 'id' column + points)."""
    return gpd.GeoDataFrame(
        {"id": list(places)},
        geometry=[Point(lon, lat) for lat, lon in places.values()],   # Point takes (x=lon, y=lat)
        crs="EPSG:4326",
    )


def main() -> None:
    start = time.time()
    print("Building the transport network (OSM streets + GTFS timetables)...")
    network = r5py.TransportNetwork(OSM, [GTFS])
    print(f"  done in {time.time() - start:.0f}s")

    start = time.time()
    ttm = r5py.TravelTimeMatrix(
        network,
        origins=points(ORIGINS),
        destinations=points(DESTINATIONS),
        departure=DEPARTURE,
        transport_modes=[r5py.TransportMode.TRANSIT, r5py.TransportMode.WALK],
    )
    print(f"Travel times computed in {time.time() - start:.1f}s "
          f"(median over departures in a 10-minute window from {DEPARTURE:%a %d %b %H:%M})\n")
    print(ttm.rename(columns={"from_id": "from", "to_id": "to",
                              "travel_time": "minutes"}).to_string(index=False))


if __name__ == "__main__":
    main()
