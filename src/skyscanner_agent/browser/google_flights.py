from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from playwright.sync_api import Page, sync_playwright

EXTRACT_SCRIPT = """
() => {
  const results = [];
  const priceRe = /^(\\$|₹|€|£)\\s?[\\d,]+$/;
  for (const el of document.querySelectorAll('span, div')) {
    const t = (el.textContent || '').trim();
    if (t.toLowerCase() !== 'round trip' || !el.previousElementSibling) continue;
    const price = (el.previousElementSibling.textContent || '').trim();
    if (!priceRe.test(price)) continue;
    let row = el.closest('li') || el.closest('[role="listitem"]');
    const rowText = row ? row.innerText : '';
    results.push({ price, rowText });
  }
  const seen = new Set();
  return results.filter(r => {
    const k = r.price + r.rowText.slice(0, 60);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}
"""


@dataclass
class FlightScrapeResult:
    url: str
    cheapest_display: str | None
    cheapest_price: float | None
    currency_hint: str | None
    airline: str | None
    stops: str | None
    offers_count: int
    error: str | None = None


def build_google_flights_url(
    *,
    origin: str,
    destination: str,
    outbound: date,
    inbound: date,
) -> str:
    query = (
        f"Flights from {origin.upper()} to {destination} "
        f"on {outbound.isoformat()} returning {inbound.isoformat()}"
    )
    return f"https://www.google.com/travel/flights?q={quote(query)}"


def parse_price(display: str) -> tuple[float | None, str | None]:
    display = display.strip()
    match = re.match(r"^([₹$€£])\s?([\d,]+(?:\.\d+)?)$", display)
    if not match:
        return None, None
    symbol, amount = match.groups()
    try:
        return float(amount.replace(",", "")), symbol
    except ValueError:
        return None, symbol


def _parse_row_metadata(row_text: str) -> tuple[str | None, str | None]:
    airline = None
    stops = None
    if "Nonstop" in row_text:
        stops = "nonstop"
    else:
        stop_match = re.search(r"(\d+)\s+stop", row_text)
        if stop_match:
            stops = f"{stop_match.group(1)} stop"
    lines = [ln.strip() for ln in row_text.splitlines() if ln.strip()]
    for line in lines:
        if re.search(r"\d+\s*hr", line):
            continue
        if re.search(r"^(AM|PM|\d)", line):
            continue
        if "CO2e" in line or "emissions" in line.lower():
            continue
        if "–" in line or line.startswith("BLR"):
            continue
        if "round trip" in line.lower():
            break
        if len(line) < 60 and not line.startswith("$") and not line.startswith("₹"):
            airline = line
            break
    return airline, stops


def scrape_google_flights(
    *,
    origin: str,
    destination: str,
    outbound: date,
    inbound: date,
    headless: bool = True,
    timeout_ms: int = 90_000,
    locale: str = "en-IN",
) -> FlightScrapeResult:
    url = build_google_flights_url(
        origin=origin,
        destination=destination,
        outbound=outbound,
        inbound=inbound,
    )
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            page = browser.new_page(viewport={"width": 1400, "height": 900}, locale=locale)
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_selector("text=Top departing flights", timeout=timeout_ms)
            page.wait_for_timeout(2000)
            rows: list[dict] = page.evaluate(EXTRACT_SCRIPT)
            browser.close()
    except Exception as exc:  # noqa: BLE001 — surface scrape failures to caller
        return FlightScrapeResult(
            url=url,
            cheapest_display=None,
            cheapest_price=None,
            currency_hint=None,
            airline=None,
            stops=None,
            offers_count=0,
            error=str(exc),
        )

    if not rows:
        return FlightScrapeResult(
            url=url,
            cheapest_display=None,
            cheapest_price=None,
            currency_hint=None,
            airline=None,
            stops=None,
            offers_count=0,
            error="No flight prices found on page (layout may have changed)",
        )

    priced: list[tuple[float, dict]] = []
    for row in rows:
        amount, symbol = parse_price(row["price"])
        if amount is not None:
            priced.append((amount, row))

    if not priced:
        first = rows[0]
        amount, symbol = parse_price(first["price"])
        airline, stops = _parse_row_metadata(first.get("rowText", ""))
        return FlightScrapeResult(
            url=url,
            cheapest_display=first["price"],
            cheapest_price=amount,
            currency_hint=symbol,
            airline=airline,
            stops=stops,
            offers_count=len(rows),
        )

    priced.sort(key=lambda x: x[0])
    cheapest_amount, cheapest_row = priced[0]
    airline, stops = _parse_row_metadata(cheapest_row.get("rowText", ""))
    return FlightScrapeResult(
        url=url,
        cheapest_display=cheapest_row["price"],
        cheapest_price=cheapest_amount,
        currency_hint=parse_price(cheapest_row["price"])[1],
        airline=airline,
        stops=stops,
        offers_count=len(rows),
    )
