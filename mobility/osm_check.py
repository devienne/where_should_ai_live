"""
Quick look inside an OpenStreetMap .pbf file: does its road network cover the whole city,
how many km of each kind of street does it have, and how recent is the data?

Usage (from the project root):
    .venv\\Scripts\\python -m mobility.osm_check
    .venv\\Scripts\\python -m mobility.osm_check data\\external\\osm\\other.osm.pbf
"""
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import osmium
from osmium.geom import haversine_distance

DEFAULT_PBF = Path(__file__).resolve().parents[1] / "data" / "external" / "osm" / "sao_paulo.osm.pbf"

# Approximate limits of São Paulo municipality (Marsilac is the southern tip)
CITY = {"south": -24.008, "north": -23.357, "west": -46.826, "east": -46.365}

# Street types, grouped by who can use them
DRIVABLE = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
            "living_street", "service", "motorway_link", "trunk_link", "primary_link",
            "secondary_link", "tertiary_link"}
FOOT_ONLY = {"footway", "pedestrian", "path", "steps", "corridor", "track", "cycleway"}


def main(path: Path) -> None:
    print(f"OSM file: {path}  ({path.stat().st_size / 1e6:.0f} MB)\n")

    # 1. The file header: the box it claims to cover and, if present, its data date
    reader = osmium.io.Reader(str(path), osmium.osm.osm_entity_bits.NOTHING)
    header = reader.header()
    reader.close()
    box = header.box()
    if box.valid():
        print(f"Header box: lat {box.bottom_left.lat:.3f} .. {box.top_right.lat:.3f}, "
              f"lon {box.bottom_left.lon:.3f} .. {box.top_right.lon:.3f}")
    stamp = header.get("osmosis_replication_timestamp") or header.get("timestamp")
    print(f"Header date: {stamp or 'not given'}")

    # 2. Walk through every street (way with a highway=* tag), with node locations attached
    km = Counter()                                   # km of street per highway type
    south, north, west, east = 90.0, -90.0, 180.0, -180.0
    newest = None                                    # most recent edit among the streets
    broken = 0                                       # ways with nodes missing from the file
    processor = (osmium.FileProcessor(str(path))
                 .with_locations()                   # store node coordinates so ways get geometry
                 .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
                 .with_filter(osmium.filter.KeyFilter("highway")))
    for way in processor:
        try:
            km[way.tags["highway"]] += haversine_distance(way.nodes) / 1000
            for node in way.nodes:
                south, north = min(south, node.lat), max(north, node.lat)
                west, east = min(west, node.lon), max(east, node.lon)
        except osmium.InvalidLocationError:          # a node outside the extract
            broken += 1
            continue
        if way.timestamp.year > 1970:                # 1970 = no metadata in the file
            newest = max(newest, way.timestamp) if newest else way.timestamp

    # 3. Does the road network reach beyond every edge of the city?
    print(f"\nRoad network extent: lat {south:.3f} .. {north:.3f}, lon {west:.3f} .. {east:.3f}")
    checks = {"south": south <= CITY["south"], "north": north >= CITY["north"],
              "west": west <= CITY["west"], "east": east >= CITY["east"]}
    for side, ok in checks.items():
        print(f"  {side:<5} edge of the city covered: {'yes' if ok else 'NO'}")

    # 4. How much street of each kind
    total = sum(km.values())
    drive = sum(v for k, v in km.items() if k in DRIVABLE)
    foot = sum(v for k, v in km.items() if k in FOOT_ONLY)
    print(f"\nStreets: {total:,.0f} km in total "
          f"({drive:,.0f} km drivable, {foot:,.0f} km foot/bike only)")
    print("Top 12 highway types (km):")
    for kind, value in km.most_common(12):
        print(f"  {kind:<16} {value:9,.0f}")
    print(f"Ways with missing nodes (cut at the border): {broken:,}")
    print(f"\nNewest street edit: {newest:%Y-%m-%d}" if newest else "\nNo edit dates in the file")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PBF)
