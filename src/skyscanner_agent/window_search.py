from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from skyscanner_agent.agent import FlightSearchAgent
from skyscanner_agent.client import SkyscannerClient
from skyscanner_agent.models import FlightQuote, SearchConfig
from skyscanner_agent.parser import parse_indicative_response
from skyscanner_agent.trip_windows import TripWindow, generate_trip_windows, sample_windows_per_month

logger = logging.getLogger(__name__)

DEFAULT_DESTINATION_GROUPS: dict[str, list[str]] = {
    "Japan": ["Japan"],
    "Thailand": ["Thailand"],
    "Denpasar": ["Denpasar"],
    "Europe": ["France", "United Kingdom", "Italy", "Spain", "Netherlands", "Germany"],
}

DEFAULT_INDIA_ORIGINS = ["BLR", "MAA", "TRZ", "COK", "BOM", "PNQ", "HYD"]


@dataclass
class WindowSearchResult:
    quote: FlightQuote
    destination_group: str
    trip_days: int


def _fixed_date(d: date) -> dict[str, Any]:
    return {"fixedDate": {"year": d.year, "month": d.month, "day": d.day}}


class RoundTripWindowSearch:
    def __init__(self, client: SkyscannerClient, agent: FlightSearchAgent) -> None:
        self._client = client
        self._agent = agent

    def search(
        self,
        *,
        config: SearchConfig,
        windows: list[TripWindow],
        destination_groups: dict[str, list[str]] | None = None,
        europe_continent_filter: bool = True,
    ) -> list[WindowSearchResult]:
        groups = destination_groups or DEFAULT_DESTINATION_GROUPS
        results: list[WindowSearchResult] = []

        resolved: dict[str, list[dict[str, Any]]] = {}
        for label, terms in groups.items():
            resolved[label] = []
            for term in terms:
                place = self._agent.resolve_place(term, config=config)
                resolved[label].append(SkyscannerClient.destination_from_place(place))

        for origin in config.origins:
            for label, destinations in resolved.items():
                for destination in destinations:
                    for window in windows:
                        query = {
                            "market": config.market,
                            "locale": config.locale,
                            "currency": config.currency,
                            "queryLegs": [
                                {
                                    "originPlace": {"queryPlace": {"iata": origin.upper()}},
                                    "destinationPlace": destination,
                                    **_fixed_date(window.outbound),
                                },
                                {
                                    "originPlace": destination,
                                    "destinationPlace": {"queryPlace": {"iata": origin.upper()}},
                                    **_fixed_date(window.inbound),
                                },
                            ],
                        }
                        logger.info(
                            "%s %s -> %s %s–%s",
                            origin,
                            label,
                            FlightSearchAgent._destination_label(destination),
                            window.outbound.isoformat(),
                            window.inbound.isoformat(),
                        )
                        payload = self._client.indicative_search(query)
                        allowed_continents = {"Europe"} if label == "Europe" and europe_continent_filter else None
                        quotes = parse_indicative_response(
                            payload,
                            config=config,
                            origin_iata=origin,
                            allowed_continents=allowed_continents,
                        )
                        if not quotes:
                            continue
                        best = min(quotes, key=lambda q: q.price_amount)
                        results.append(
                            WindowSearchResult(
                                quote=best,
                                destination_group=label,
                                trip_days=window.trip_days,
                            )
                        )
        return self._rank(results, top_per_group=config.top_n)

    @staticmethod
    def _rank(results: list[WindowSearchResult], *, top_per_group: int) -> list[WindowSearchResult]:
        by_group: dict[str, list[WindowSearchResult]] = {}
        for item in results:
            by_group.setdefault(item.destination_group, []).append(item)
        ranked: list[WindowSearchResult] = []
        for group in sorted(by_group):
            group_items = sorted(by_group[group], key=lambda r: r.quote.sort_key)
            ranked.extend(group_items[:top_per_group])
        return ranked


def build_default_windows(
    *,
    year: int,
    sample_per_month: int | None = 2,
) -> list[TripWindow]:
    outbound_start = date(year, 2, 1)
    outbound_end = date(year, 7, 31)
    max_return = date(year, 8, 15)
    windows = generate_trip_windows(
        outbound_start=outbound_start,
        outbound_end=outbound_end,
        max_return=max_return,
    )
    if sample_per_month:
        return sample_windows_per_month(windows, per_month=sample_per_month)
    return windows
