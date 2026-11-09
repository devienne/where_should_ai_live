"""Tests for scraper.clean (synthetic data only)."""
import math

import pandas as pd
import pytest

from scraper.clean import (area_from_title, bedrooms_from_title, clean, floor_min, haversine_km,
                           parking_from_title, parse_price)


@pytest.mark.parametrize("value, expected", [
    ("R$\xa01.400", 1400.0),
    ("R$ 350", 350.0),
    ("Incluso", 0.0),
])
def test_parse_price(value, expected):
    assert parse_price(value) == expected


def test_parse_price_missing():
    assert math.isnan(parse_price(None))


@pytest.mark.parametrize("title, parking, bedrooms, area", [
    ("Studio para alugar com 31m², 1 quarto e sem vaga", 0, 1, 31),
    ("Casa para alugar com 315m², 3 quartos e 3 vagas", 3, 3, 315),
    ("Apartamento para alugar com 40m², 1 quarto e 1 vaga", 1, 1, 40),
])
def test_values_from_title(title, parking, bedrooms, area):
    assert parking_from_title(title) == parking
    assert bedrooms_from_title(title) == bedrooms
    assert area_from_title(title) == area


@pytest.mark.parametrize("value, expected", [
    ("Até 3° andar", 0),
    ("4° a 7° andar", 4),
    ("20° a 23° andar", 20),
])
def test_floor_min(value, expected):
    assert floor_min(value) == expected


def test_haversine_known_distance():
    # Praça da Sé -> Av. Paulista/MASP is about 2.9 km
    assert 2.5 < haversine_km(-23.5503, -46.6339, -23.5614, -46.6559) < 3.2


def _row(**overrides):
    row = {
        "url": "https://www.quintoandar.com.br/imovel/1/alugar/apartamento-1-quarto-liberdade-sao-paulo",
        "bairro": "liberdade", "zone": "Centro", "scraped_at": "2026-11-09T16:00:00", "status": "success",
        "title": "Apartamento para alugar com 40m², 1 quarto e sem vaga",
        "address_street": "Rua Teste", "lat": -23.56, "lon": -46.63, "area": 40.0, "quartos": 1.0,
        "suite": 0, "banheiros": 1.0, "vagas": None, "andar": "4° a 7° andar",
        "pet": "sim", "mobiliado": "não", "metro_proximo": "sim",
        "Aluguel": "R$ 2.000", "Condomínio": "Incluso", "IPTU": "R$ 50",
        "Seguro incêndio": "R$ 30", "Taxa de serviço": "R$ 80", "Total": "R$ 2.160",
    }
    row.update(overrides)
    return row


def test_clean_end_to_end():
    raw = pd.DataFrame([
        _row(),                                                         # kept
        _row(url=_row()["url"].replace("/1/", "/2/"), status="fetch_failed"),   # dropped
        _row(url="https://www.quintoandar.com.br/imovel/3/alugar/apartamento-2-quartos-vila-maria-guarulhos"),
        _row(url=_row()["url"].replace("/1/", "/4/"), lat=None, lon=None),      # dropped: no coords
        _row(url=_row()["url"].replace("/1/", "/5/"), area=2.0, title="x"),     # dropped: area
    ])
    df, report = clean(raw)

    assert len(df) == 1
    r = df.iloc[0]
    assert r.listing_id == "1"
    assert r.parking == 0                      # filled from the title
    assert r.condo_fee == 0 and r.condo_included
    assert r.total_cost == 2160
    assert r.cost_per_m2 == 54.0
    assert r.floor_min == 4
    assert r.property_type == "apartamento"
    assert bool(r.pet_friendly) and not bool(r.furnished)
    assert report.rows_removed.sum() == 4
