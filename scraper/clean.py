"""
Clean the merged listings.

Steps:
  1. keep only successfully parsed listings
  2. keep only listings in the city of São Paulo (e.g. drop Guarulhos results)
  3. convert prices ("R$ 1.400", "Incluso") to numbers
  4. fill parking / bedrooms / area from the title when the page block was empty
     ("Studio para alugar com 31m², 1 quarto e sem vaga")
  5. derive property type (from URL) and floor (from "4° a 7° andar")
  6. normalise yes/no columns to booleans, blank the placeholder address
  7. drop rows without coordinates, implausible values, and points far from their bairro

Usage (from the project root):
    .venv\\Scripts\\python -m scraper.clean

Input:  data/processed/listings_raw.csv
Output: data/processed/listings_clean.csv
"""
import re
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from .config import PROJECT_ROOT

INPUT_FILE = PROJECT_ROOT / "data" / "processed" / "listings_raw.csv"
OUTPUT_FILE = PROJECT_ROOT / "data" / "processed" / "listings_clean.csv"

PLACEHOLDER_ADDRESS = "Condomínios em São Paulo"

# Plausibility limits
AREA_RANGE = (10, 1000)            # m²
TOTAL_COST_RANGE = (300, 50_000)   # R$ per month
COST_PER_M2_RANGE = (5, 400)       # R$ per m² per month
MAX_KM_FROM_BAIRRO_CENTER = 4.0    # distance from the median point of the bairro's listings

PRICE_COLUMNS = {
    "Aluguel": "rent",
    "Condomínio": "condo_fee",
    "IPTU": "iptu",
    "Seguro incêndio": "fire_insurance",
    "Taxa de serviço": "service_fee",
    "Total": "total_cost",
}

OUTPUT_COLUMNS = [
    "listing_id", "url", "bairro", "zone", "scraped_at", "property_type", "address",
    "lat", "lon", "area_m2", "bedrooms", "suites", "bathrooms", "parking",
    "floor_min", "pet_friendly", "furnished", "metro_near",
    "rent", "condo_fee", "condo_included", "iptu", "fire_insurance", "service_fee",
    "total_cost", "cost_per_m2",
]


# --------------------------------------------------------------------------
# Value parsers
# --------------------------------------------------------------------------
def parse_price(value) -> Optional[float]:
    """'R$\\xa01.400' -> 1400.0 ; 'Incluso' -> 0.0 ; NaN -> NaN"""
    if pd.isna(value):
        return np.nan
    text = str(value).replace("\xa0", " ").strip().lower()
    if text.startswith("inclu"):
        return 0.0
    digits = re.sub(r"[^\d,]", "", text).replace(",", ".")
    return float(digits) if digits else np.nan


def parking_from_title(title) -> Optional[float]:
    if pd.isna(title):
        return np.nan
    t = str(title).lower()
    if "sem vaga" in t:
        return 0.0
    m = re.search(r"(\d+)\s*vagas?", t)
    return float(m.group(1)) if m else np.nan


def bedrooms_from_title(title) -> Optional[float]:
    m = re.search(r"(\d+)\s*quartos?", str(title).lower()) if pd.notna(title) else None
    return float(m.group(1)) if m else np.nan


def area_from_title(title) -> Optional[float]:
    m = re.search(r"(\d+)\s*m²", str(title)) if pd.notna(title) else None
    return float(m.group(1)) if m else np.nan


def floor_min(value) -> Optional[float]:
    """'Até 3° andar' -> 0 ; '4° a 7° andar' -> 4 ; NaN (houses) -> NaN"""
    if pd.isna(value):
        return np.nan
    text = str(value).lower()
    if text.startswith("até"):
        return 0.0
    m = re.search(r"(\d+)", text)
    return float(m.group(1)) if m else np.nan


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


# --------------------------------------------------------------------------
# Cleaning
# --------------------------------------------------------------------------
def clean(raw: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Return (clean dataframe, report of how many rows each step removed)."""
    df = raw.copy()
    report = [("raw rows", len(df), 0)]

    def step(name: str, mask: pd.Series) -> None:
        nonlocal df
        removed = int((~mask).sum())
        df = df[mask].copy()
        report.append((name, len(df), removed))

    # 1-2. parse status and city
    step("parsed successfully", df["status"] == "success")
    # Long URLs end with the city ("...-vila-maria-guarulhos"); short ones ("/imovel/<id>/alugar") don't
    last_part = df["url"].str.rstrip("/").str.split("/").str[-1]
    other_city = last_part.str.contains("-") & ~last_part.str.endswith("-sao-paulo")
    step("in São Paulo city (URL doesn't name another city)", ~other_city)

    # 3. prices
    for raw_col, new_col in PRICE_COLUMNS.items():
        df[new_col] = df[raw_col].map(parse_price) if raw_col in df else np.nan
    df["condo_included"] = df["Condomínio"].astype(str).str.lower().str.startswith("inclu")

    # 4. fill gaps from the title
    df["parking"] = pd.to_numeric(df["vagas"], errors="coerce").fillna(df["title"].map(parking_from_title))
    df["bedrooms"] = pd.to_numeric(df["quartos"], errors="coerce").fillna(df["title"].map(bedrooms_from_title))
    df["area_m2"] = pd.to_numeric(df["area"], errors="coerce").fillna(df["title"].map(area_from_title))

    # 5. derived columns
    # Regular listings: /imovel/<id>/...  Partner ads ("classificados"): /classificado/<id>/alugar
    df["listing_id"] = df["url"].str.extract(r"/(?:imovel|classificado)/(\d+)")[0]
    is_classified = df["url"].str.contains("/classificado/")
    for fee in ["fire_insurance", "service_fee"]:  # partner ads don't charge these
        df.loc[is_classified, fee] = df.loc[is_classified, fee].fillna(0.0)
    type_from_title = (df["title"].str.split().str[0].str.lower()
                       .map({"apartamento": "apartamento", "kitnet": "kitnet", "studio": "kitnet",
                             "casa": "casa"}))
    df["property_type"] = df["url"].str.extract(r"/alugar/([a-z]+)-")[0].fillna(type_from_title)
    df["floor_min"] = df["andar"].map(floor_min)

    # 6. booleans, address, renames
    df["pet_friendly"] = df["pet"].eq("sim")
    df["furnished"] = df["mobiliado"].eq("sim")
    df["metro_near"] = df["metro_proximo"].eq("sim")
    df["address"] = df["address_street"].where(df["address_street"] != PLACEHOLDER_ADDRESS)
    df = df.rename(columns={"suite": "suites", "banheiros": "bathrooms"})

    # 7. drop unusable / implausible rows
    step("has coordinates", df["lat"].notna() & df["lon"].notna())
    step("has total cost", df["total_cost"].notna())
    step(f"area within {AREA_RANGE} m²", df["area_m2"].between(*AREA_RANGE))
    step(f"total cost within R$ {TOTAL_COST_RANGE}", df["total_cost"].between(*TOTAL_COST_RANGE))

    df["cost_per_m2"] = (df["total_cost"] / df["area_m2"]).round(2)
    step(f"cost per m² within {COST_PER_M2_RANGE}", df["cost_per_m2"].between(*COST_PER_M2_RANGE))

    center = df.groupby("bairro")[["lat", "lon"]].transform("median")
    df["km_from_bairro_center"] = haversine_km(df["lat"], df["lon"], center["lat"], center["lon"])
    step(f"within {MAX_KM_FROM_BAIRRO_CENTER} km of its bairro centre",
         df["km_from_bairro_center"] <= MAX_KM_FROM_BAIRRO_CENTER)

    df = df[OUTPUT_COLUMNS].reset_index(drop=True)
    for col in ["bedrooms", "bathrooms", "parking", "suites"]:
        df[col] = df[col].astype("Int64")

    report_df = pd.DataFrame(report, columns=["step", "rows_left", "rows_removed"])
    return df, report_df


def main() -> None:
    raw = pd.read_csv(INPUT_FILE)
    df, report = clean(raw)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")

    print(report.to_string(index=False))
    print(f"\nSaved: {OUTPUT_FILE.relative_to(PROJECT_ROOT)} ({len(df)} rows, {df.shape[1]} columns)")
    print("\nMissing values left:")
    missing = df.isna().sum()
    print(missing[missing > 0].to_string() if missing.any() else "  none")


if __name__ == "__main__":
    main()
