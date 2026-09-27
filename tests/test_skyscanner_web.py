from datetime import date

from skyscanner_agent.browser.skyscanner_web import (
    build_explore_anywhere_url,
    build_roundtrip_url,
    filter_offers_by_regions,
    match_region,
    parse_price,
    SkyscannerOffer,
)


def test_explore_url_contains_oym():
    url = build_explore_anywhere_url(origin_iata="BLR", year=2027, month=3)
    assert "cheap-flights-anywhere" in url
    assert "oym=2703" in url
    assert "blr" in url


def test_roundtrip_url():
    url = build_roundtrip_url(
        origin_iata="BLR",
        destination_iata="DPS",
        outbound=date(2027, 3, 5),
        inbound=date(2027, 3, 14),
    )
    assert "/blr/dps/270305/270314/" in url


def test_region_filter():
    offers = [
        SkyscannerOffer("Tokyo", "₹45,000", 45000.0, "₹", None),
        SkyscannerOffer("Bangkok", "₹12,000", 12000.0, "₹", None),
    ]
    filtered = filter_offers_by_regions(offers, {"Japan"})
    assert len(filtered) == 1
    assert filtered[0].region == "Japan"


def test_match_region_denpasar():
    assert match_region("Bali (DPS)", {"Denpasar"}) == "Denpasar"
