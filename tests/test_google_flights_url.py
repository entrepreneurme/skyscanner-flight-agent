from datetime import date

from skyscanner_agent.browser.google_flights import build_google_flights_url, parse_price


def test_build_url():
    url = build_google_flights_url(
        origin="BLR",
        destination="Tokyo",
        outbound=date(2027, 3, 5),
        inbound=date(2027, 3, 14),
    )
    assert "BLR" in url
    assert "2027-03-05" in url


def test_parse_price():
    amount, sym = parse_price("$660")
    assert amount == 660.0
    assert sym == "$"
    amount2, sym2 = parse_price("₹45,320")
    assert amount2 == 45320.0
    assert sym2 == "₹"
