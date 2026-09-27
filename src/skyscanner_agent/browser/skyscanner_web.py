from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from playwright.sync_api import BrowserContext, Page, sync_playwright

# Region labels → keywords matched against destination text from Skyscanner explore results
REGION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Japan": ("japan", "tokyo", "osaka", "nrt", "hnd", "kix"),
    "Thailand": ("thailand", "bangkok", "bkk", "phuket", "cnx"),
    "Denpasar": ("bali", "denpasar", "dps", "indonesia"),
    "Europe": (
        "europe",
        "france",
        "paris",
        "united kingdom",
        "london",
        "italy",
        "rome",
        "spain",
        "barcelona",
        "madrid",
        "germany",
        "berlin",
        "netherlands",
        "amsterdam",
        "portugal",
        "lisbon",
        "switzerland",
        "zurich",
        "greece",
        "athens",
        "vienna",
        "prague",
        "copenhagen",
        "dublin",
        "brussels",
        "warsaw",
        "budapest",
        "oslo",
        "stockholm",
        "helsinki",
    ),
}

EXPLORE_EXTRACT_SCRIPT = """
() => {
  const priceRe = /(₹|\\$|€|£)\\s?[\\d,]+(?:\\.\\d+)?/;
  const results = [];
  const seen = new Set();
  const nodes = document.querySelectorAll('a, button, [role="button"], li, article, div');
  for (const node of nodes) {
    const text = (node.innerText || '').trim();
    if (!text || text.length > 180) continue;
    const priceMatch = text.match(priceRe);
    if (!priceMatch) continue;
    const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
    if (lines.length < 2) continue;
    const price = priceMatch[0].replace(/\\s+/g, ' ');
    const destLine = lines.find(l => !priceRe.test(l)) || lines[0];
    if (!destLine || destLine.length < 2) continue;
    const key = destLine + '|' + price;
    if (seen.has(key)) continue;
    seen.add(key);
    let href = null;
    const link = node.closest('a') || (node.tagName === 'A' ? node : null);
    if (link && link.href) href = link.href;
    results.push({ destination: destLine, price, href });
  }
  return results.slice(0, 120);
}
"""

DAY_VIEW_EXTRACT_SCRIPT = """
() => {
  const priceRe = /^(₹|\\$|€|£)\\s?[\\d,]+(?:\\.\\d+)?$/;
  const offers = [];
  for (const el of document.querySelectorAll('span,div')) {
    const t = (el.textContent || '').trim();
    if (!priceRe.test(t)) continue;
    const row = el.closest('li') || el.closest('[data-testid]') || el.parentElement;
    const rowText = row ? row.innerText.slice(0, 500) : t;
    offers.push({ price: t, rowText });
  }
  const seen = new Set();
  return offers.filter(o => {
    const k = o.price + o.rowText.slice(0, 40);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  }).slice(0, 40);
}
"""


@dataclass
class SkyscannerOffer:
    destination: str
    price_display: str
    price_amount: float | None
    currency_hint: str | None
    url: str | None
    region: str | None = None


@dataclass
class SkyscannerScrapeResult:
    url: str
    offers: list[SkyscannerOffer]
    error: str | None = None


def skyscanner_site(market_site: str) -> str:
    site = market_site.strip().lower().replace("https://", "").rstrip("/")
    if not site.startswith("www."):
        site = f"www.{site}" if "." in site else f"www.skyscanner.{site}"
    return site


def format_skyscanner_date(d: date) -> str:
    return f"{d.year % 100:02d}{d.month:02d}{d.day:02d}"


def format_skyscanner_month_ym(year: int, month: int) -> str:
    return f"{year % 100:02d}{month:02d}"


def build_explore_anywhere_url(
    *,
    origin_iata: str,
    year: int,
    month: int,
    market_site: str = "skyscanner.co.in",
    trip_type: str = "return",
) -> str:
    """Skyscanner 'Everywhere' style browse from an origin with a whole-month hint (oym)."""
    host = skyscanner_site(market_site)
    origin = origin_iata.lower()
    oym = format_skyscanner_month_ym(year, month)
    params = urlencode(
        {
            "adultsv2": "1",
            "cabinclass": "economy",
            "oym": oym,
            "rtn": "1" if trip_type == "return" else "0",
            "preferdirects": "false",
        }
    )
    return f"https://{host}/transport/flights-from/{origin}/cheap-flights-anywhere.html?{params}"


def build_roundtrip_url(
    *,
    origin_iata: str,
    destination_iata: str,
    outbound: date,
    inbound: date,
    market_site: str = "skyscanner.co.in",
) -> str:
    host = skyscanner_site(market_site)
    params = urlencode(
        {
            "adultsv2": "1",
            "cabinclass": "economy",
            "rtn": "1",
            "preferdirects": "false",
        }
    )
    path = (
        f"/transport/flights/{origin_iata.lower()}/{destination_iata.lower()}/"
        f"{format_skyscanner_date(outbound)}/{format_skyscanner_date(inbound)}/"
    )
    return f"https://{host}{path}?{params}"


def parse_price(display: str) -> tuple[float | None, str | None]:
    display = display.strip().replace("\u202f", " ")
    match = re.match(r"^([₹$€£])\s?([\d,]+(?:\.\d+)?)$", display)
    if not match:
        return None, None
    symbol, amount = match.groups()
    try:
        return float(amount.replace(",", "")), symbol
    except ValueError:
        return None, symbol


def match_region(destination: str, allowed_regions: set[str]) -> str | None:
    text = destination.lower()
    for region in allowed_regions:
        for keyword in REGION_KEYWORDS.get(region, (region.lower(),)):
            if keyword in text:
                return region
    return None


def filter_offers_by_regions(
    offers: list[SkyscannerOffer],
    allowed_regions: set[str],
) -> list[SkyscannerOffer]:
    filtered: list[SkyscannerOffer] = []
    for offer in offers:
        region = match_region(offer.destination, allowed_regions)
        if region:
            offer.region = region
            filtered.append(offer)
    return filtered


def pick_cheapest_per_region(offers: list[SkyscannerOffer]) -> list[SkyscannerOffer]:
    best: dict[str, SkyscannerOffer] = {}
    for offer in offers:
        if not offer.region:
            continue
        amount = offer.price_amount
        if amount is None:
            continue
        existing = best.get(offer.region)
        if existing is None or (
            existing.price_amount is not None and amount < existing.price_amount
        ):
            best[offer.region] = offer
    return sorted(best.values(), key=lambda o: (o.region or "", o.price_amount or 0))


def is_captcha_page(page: Page) -> bool:
    url = page.url.lower()
    if "captcha" in url or "/sttc/px/" in url:
        return True
    body = page.inner_text("body").lower()
    return "are you a person or a robot" in body


def dismiss_cookie_banner(page: Page) -> None:
    for label in (
        "Accept all",
        "Accept All",
        "Yes, I agree",
        "Agree",
        "Got it",
    ):
        try:
            page.get_by_role("button", name=re.compile(label, re.I)).first.click(timeout=2500)
            page.wait_for_timeout(500)
            return
        except Exception:
            continue


def _launch_context(
    *,
    headless: bool,
    locale: str,
    user_data_dir: Path | None,
) -> tuple[Any, BrowserContext]:
    playwright = sync_playwright().start()
    args = ["--disable-blink-features=AutomationControlled"]
    if user_data_dir:
        user_data_dir.mkdir(parents=True, exist_ok=True)
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=headless,
            locale=locale,
            viewport={"width": 1400, "height": 900},
            args=args,
        )
        return playwright, context
    browser = playwright.chromium.launch(headless=headless, args=args)
    context = browser.new_context(
        locale=locale,
        viewport={"width": 1400, "height": 900},
        timezone_id="Asia/Kolkata",
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    )
    return playwright, context


def _rows_to_offers(rows: list[dict[str, Any]], page_url: str) -> list[SkyscannerOffer]:
    offers: list[SkyscannerOffer] = []
    for row in rows:
        price_display = row.get("price", "").strip()
        amount, symbol = parse_price(price_display)
        offers.append(
            SkyscannerOffer(
                destination=row.get("destination") or row.get("rowText", "")[:80],
                price_display=price_display,
                price_amount=amount,
                currency_hint=symbol,
                url=row.get("href") or page_url,
            )
        )
    return offers


def scrape_explore_anywhere(
    *,
    origin_iata: str,
    year: int,
    month: int,
    allowed_regions: set[str],
    market_site: str = "skyscanner.co.in",
    headless: bool = True,
    user_data_dir: Path | None = None,
    timeout_ms: int = 90_000,
) -> SkyscannerScrapeResult:
    url = build_explore_anywhere_url(
        origin_iata=origin_iata,
        year=year,
        month=month,
        market_site=market_site,
    )
    playwright = None
    context = None
    try:
        playwright, context = _launch_context(
            headless=headless,
            locale="en-IN",
            user_data_dir=user_data_dir,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(4000)
        dismiss_cookie_banner(page)
        if is_captcha_page(page):
            return SkyscannerScrapeResult(
                url=page.url,
                offers=[],
                error=(
                    "Skyscanner CAPTCHA blocked automation. Run with headless: false and "
                    "browser_user_data_dir set, solve CAPTCHA once, then retry."
                ),
            )
        page.wait_for_timeout(6000)
        rows: list[dict] = page.evaluate(EXPLORE_EXTRACT_SCRIPT)
        offers = _rows_to_offers(rows, page.url)
        offers = filter_offers_by_regions(offers, allowed_regions)
        return SkyscannerScrapeResult(url=page.url, offers=offers)
    except Exception as exc:  # noqa: BLE001
        return SkyscannerScrapeResult(url=url, offers=[], error=str(exc))
    finally:
        if context:
            context.close()
        if playwright:
            playwright.stop()


def scrape_roundtrip_day_view(
    *,
    origin_iata: str,
    destination_iata: str,
    outbound: date,
    inbound: date,
    market_site: str = "skyscanner.co.in",
    headless: bool = True,
    user_data_dir: Path | None = None,
    timeout_ms: int = 90_000,
) -> SkyscannerScrapeResult:
    url = build_roundtrip_url(
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        outbound=outbound,
        inbound=inbound,
        market_site=market_site,
    )
    playwright = None
    context = None
    try:
        playwright, context = _launch_context(
            headless=headless,
            locale="en-IN",
            user_data_dir=user_data_dir,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(5000)
        dismiss_cookie_banner(page)
        if is_captcha_page(page):
            return SkyscannerScrapeResult(
                url=page.url,
                offers=[],
                error=(
                    "Skyscanner CAPTCHA blocked automation. Use a persistent browser profile "
                    "and headed mode to pass verification locally."
                ),
            )
        page.wait_for_timeout(8000)
        rows: list[dict] = page.evaluate(DAY_VIEW_EXTRACT_SCRIPT)
        offers: list[SkyscannerOffer] = []
        for row in rows:
            price_display = row.get("price", "")
            amount, symbol = parse_price(price_display)
            offers.append(
                SkyscannerOffer(
                    destination=destination_iata.upper(),
                    price_display=price_display,
                    price_amount=amount,
                    currency_hint=symbol,
                    url=page.url,
                )
            )
        return SkyscannerScrapeResult(url=page.url, offers=offers)
    except Exception as exc:  # noqa: BLE001
        return SkyscannerScrapeResult(url=url, offers=[], error=str(exc))
    finally:
        if context:
            context.close()
        if playwright:
            playwright.stop()
