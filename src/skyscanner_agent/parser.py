from __future__ import annotations

from typing import Any

from skyscanner_agent.geo import (
    REGION_COUNTRY_IDS,
    country_matches_location_filters,
    normalize_country_name,
)
from skyscanner_agent.models import FlightQuote, SearchConfig


def _parse_price(amount: str | int | float | None) -> float | None:
    if amount is None:
        return None
    try:
        return float(amount)
    except (TypeError, ValueError):
        return None


def _format_date(dt: dict[str, Any] | None) -> str | None:
    if not dt:
        return None
    y, m, d = dt.get("year"), dt.get("month"), dt.get("day")
    if y and m and d:
        return f"{y:04d}-{m:02d}-{d:02d}"
    if y and m:
        return f"{y:04d}-{m:02d}"
    return None


def _place_label(place: dict[str, Any] | None) -> tuple[str, str, str | None]:
    if not place:
        return ("?", "Unknown", None)
    code = place.get("iata") or place.get("entityId") or "?"
    name = place.get("name") or code
    country = None
    if place.get("type") == "PLACE_TYPE_COUNTRY":
        country = name
    return (str(code), str(name), country)


def _resolve_country_for_place(
    place_id: str,
    places: dict[str, Any],
    *,
    cache: dict[str, str | None],
) -> str | None:
    if place_id in cache:
        return cache[place_id]
    current = places.get(place_id)
    visited: set[str] = set()
    while current and current.get("entityId") not in visited:
        visited.add(str(current.get("entityId")))
        if current.get("type") == "PLACE_TYPE_COUNTRY":
            cache[place_id] = current.get("name")
            return cache[place_id]
        parent_id = current.get("parentId")
        if not parent_id or parent_id == current.get("entityId"):
            break
        current = places.get(str(parent_id))
    cache[place_id] = None
    return None


def _country_id_for_place(place_id: str, places: dict[str, Any]) -> str | None:
    current = places.get(place_id)
    visited: set[str] = set()
    while current and str(current.get("entityId")) not in visited:
        visited.add(str(current.get("entityId")))
        if current.get("type") == "PLACE_TYPE_COUNTRY":
            iata = current.get("iata")
            return str(iata) if iata else None
        parent_id = current.get("parentId")
        if not parent_id:
            break
        current = places.get(str(parent_id))
    return None


def parse_indicative_response(
    payload: dict[str, Any],
    *,
    config: SearchConfig,
    origin_iata: str,
    allowed_countries: set[str] | None = None,
    allowed_continents: set[str] | None = None,
    allowed_regions: set[str] | None = None,
) -> list[FlightQuote]:
    content = payload.get("content") or {}
    results = content.get("results") or {}
    quotes_map: dict[str, Any] = results.get("quotes") or {}
    places: dict[str, Any] = results.get("places") or {}
    carriers: dict[str, Any] = results.get("carriers") or {}

    country_name_cache: dict[str, str | None] = {}
    quotes: list[FlightQuote] = []

    for quote in quotes_map.values():
        min_price = quote.get("minPrice") or {}
        amount = _parse_price(min_price.get("amount"))
        if amount is None:
            continue

        outbound = quote.get("outboundLeg") or {}
        inbound = quote.get("inboundLeg") or {}
        dest_id = outbound.get("destinationPlaceId")
        orig_id = outbound.get("originPlaceId")
        if not dest_id:
            continue

        dest_place = places.get(str(dest_id))
        orig_place = places.get(str(orig_id)) if orig_id else None
        _, dest_name, dest_country_from_type = _place_label(dest_place)
        dest_code = (dest_place or {}).get("iata") or str(dest_id)

        country_name = dest_country_from_type or _resolve_country_for_place(
            str(dest_id), places, cache=country_name_cache
        )
        country_id = _country_id_for_place(str(dest_id), places)

        if allowed_countries:
            if not country_name or normalize_country_name(country_name) not in allowed_countries:
                continue
        if not country_matches_location_filters(
            country_id,
            continents=allowed_continents or set(),
            regions=allowed_regions or set(),
        ):
            if allowed_continents or allowed_regions:
                continue

        carrier_id = outbound.get("marketingCarrierId")
        carrier = carriers.get(str(carrier_id), {}) if carrier_id else {}
        carrier_name = carrier.get("name")

        origin_code = (orig_place or {}).get("iata") or origin_iata.upper()
        origin_name = (orig_place or {}).get("name") or origin_iata.upper()

        quotes.append(
            FlightQuote(
                origin_code=str(origin_code),
                origin_name=str(origin_name),
                destination_code=str(dest_code),
                destination_name=str(dest_name),
                destination_country=country_name,
                price_amount=amount,
                currency=config.currency,
                is_direct=bool(quote.get("isDirect")),
                outbound_date=_format_date(outbound.get("departureDateTime")),
                inbound_date=_format_date(inbound.get("departureDateTime")),
                carrier_name=carrier_name,
                deep_link_hint=(
                    f"https://www.skyscanner.net/transport/flights/{origin_code.lower()}/"
                    f"{str(dest_code).lower()}/"
                ),
            )
        )

    quotes.sort(key=lambda q: q.sort_key)
    return quotes[: config.max_results_per_origin]


def merge_and_rank(quotes: list[FlightQuote], top_n: int) -> list[FlightQuote]:
    by_route: dict[tuple[str, str], FlightQuote] = {}
    for quote in quotes:
        key = (quote.origin_code, quote.destination_code)
        existing = by_route.get(key)
        if existing is None or quote.price_amount < existing.price_amount:
            by_route[key] = quote
    ranked = sorted(by_route.values(), key=lambda q: q.sort_key)
    return ranked[:top_n]
