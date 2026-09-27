import json
from pathlib import Path

from skyscanner_agent.config_loader import config_from_dict
from skyscanner_agent.models import SearchConfig
from skyscanner_agent.parser import merge_and_rank, parse_indicative_response


FIXTURE = Path(__file__).parent / "fixtures" / "sample_indicative_response.json"


def test_parse_and_filter_by_continent():
    payload = json.loads(FIXTURE.read_text())
    config = SearchConfig(currency="EUR", origins=["DEL"])
    quotes = parse_indicative_response(
        payload,
        config=config,
        origin_iata="DEL",
        allowed_continents={"Europe"},
    )
    assert len(quotes) == 1
    assert quotes[0].destination_code == "BCN"
    assert quotes[0].price_amount == 199.0


def test_parse_region_filter_southeast_asia():
    payload = json.loads(FIXTURE.read_text())
    config = SearchConfig(currency="EUR", origins=["DEL"])
    quotes = parse_indicative_response(
        payload,
        config=config,
        origin_iata="DEL",
        allowed_regions={"Southeast Asia"},
    )
    assert len(quotes) == 1
    assert quotes[0].destination_code == "BKK"


def test_merge_keeps_cheapest_per_route():
    from skyscanner_agent.models import FlightQuote

    quotes = [
        FlightQuote("DEL", "Delhi", "BCN", "Barcelona", "Spain", 300, "EUR", False, None, None, None),
        FlightQuote("DEL", "Delhi", "BCN", "Barcelona", "Spain", 250, "EUR", True, None, None, None),
        FlightQuote("DEL", "Delhi", "BKK", "Bangkok", "Thailand", 400, "EUR", True, None, None, None),
    ]
    merged = merge_and_rank(quotes, top_n=10)
    assert len(merged) == 2
    bcn = next(q for q in merged if q.destination_code == "BCN")
    assert bcn.price_amount == 250


def test_config_from_dict_anywhere():
    cfg = config_from_dict(
        {
            "origins": ["del", "bom"],
            "destinations": {"mode": "anywhere", "continents": ["Europe"]},
            "dates": {"mode": "anytime"},
        }
    )
    assert cfg.origins == ["DEL", "BOM"]
    assert cfg.date_mode.value == "anytime"
