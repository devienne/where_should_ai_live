"""
Tests for the scraper parser and models.

- Unit tests use small synthetic inputs and always run.
- Fixture tests parse real saved pages from tests/fixtures/ (git-ignored, created with
  `python -m tests.save_fixtures`). They are skipped when the fixtures are missing.
"""
import json
import re
from pathlib import Path

import pytest

from scraper.data_parser import _find_coords, parse_property_html
from scraper.models import PropertyInfo
from scraper.url_collector import clean_listing_url

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURES = sorted(FIXTURES_DIR.glob("*.html")) if FIXTURES_DIR.exists() else []


# --------------------------------------------------------------------------
# Unit tests (synthetic data)
# --------------------------------------------------------------------------
def test_clean_listing_url_drops_query_and_fragment():
    url = "https://www.quintoandar.com.br/imovel/123/alugar/apartamento?search_id=%22x%22&rank=1#top"
    assert clean_listing_url(url) == "https://www.quintoandar.com.br/imovel/123/alugar/apartamento"


@pytest.mark.parametrize("text, field, expected", [
    ("45 m²", "area", "45"),
    ("2 quartos (1 suíte)", "quartos", 2),
    ("2 quartos (1 suíte)", "suite", 1),
    ("1 banheiro", "banheiros", 1),
    ("2 vagas", "vagas", 2),
    ("Sem vaga", "vagas", 0),
    ("Aceita pet", "pet", "sim"),
    ("Sem mobília", "mobiliado", "não"),
    ("Metrô próximo", "metro_proximo", "sim"),
])
def test_update_from_text(text, field, expected):
    p = PropertyInfo(url="u")
    p.update_from_text(text)
    assert getattr(p, field) == expected


def test_find_coords_nested_dict():
    data = {"props": {"pageProps": {"house": {"address": {"lat": "-23.56", "lng": "-46.63"}}}}}
    assert _find_coords(data) == (-23.56, -46.63)


def test_find_coords_json_ld_geo():
    data = {"@type": "Apartment", "geo": {"latitude": -23.55, "longitude": -46.64}}
    assert _find_coords(data) == (-23.55, -46.64)


def test_find_coords_ignores_values_outside_sao_paulo():
    assert _find_coords({"lat": 0, "lng": 0}) is None
    assert _find_coords({"latitude": 40.7, "longitude": -74.0}) is None


def test_parse_minimal_html():
    ld = {"@type": "Apartment", "address": {"streetAddress": "Rua Teste", "addressLocality": "São Paulo"},
          "geo": {"latitude": -23.56, "longitude": -46.63}}
    html = f"""<html><head>
        <script type="application/ld+json">{json.dumps(ld)}</script></head>
        <body><h1 data-testid="listing-title">Apartamento com 2 quartos</h1></body></html>"""
    p = parse_property_html(html, "https://example.com/imovel/1")
    assert p.status == "success"
    assert p.address_street == "Rua Teste, São Paulo"
    assert (p.lat, p.lon) == (-23.56, -46.63)
    assert p.quartos == 2


def test_parse_empty_html():
    assert parse_property_html("", "u").status == "no_content"


# --------------------------------------------------------------------------
# Fixture tests (real saved pages; skipped if absent)
# --------------------------------------------------------------------------
@pytest.mark.skipif(not FIXTURES, reason="no fixtures: run `python -m tests.save_fixtures`")
@pytest.mark.parametrize("html_file", FIXTURES, ids=lambda p: p.stem)
def test_real_listing_page(html_file):
    url = html_file.with_suffix(".url").read_text(encoding="utf-8").strip()
    p = parse_property_html(html_file.read_text(encoding="utf-8"), url)

    assert p.status == "success"
    # price table
    assert "Aluguel" in p.prices and "Total" in p.prices
    assert re.match(r"R\$\s?[\d.]+", p.prices["Total"])
    # property details
    assert p.area is not None and int(p.area) > 0
    assert p.quartos is not None and 1 <= p.quartos <= 10
    assert p.banheiros is not None and 1 <= p.banheiros <= 10
    # location
    assert p.address_street
    assert -24.1 < p.lat < -23.3 and -47.0 < p.lon < -46.3
