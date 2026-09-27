from __future__ import annotations

import logging

from skyscanner_agent.client import SkyscannerClient
from skyscanner_agent.geo import REGION_COUNTRY_IDS, normalize_country_name
from skyscanner_agent.models import DestinationMode, ResolvedPlace, SearchConfig
from skyscanner_agent.parser import merge_and_rank, parse_indicative_response

logger = logging.getLogger(__name__)


class FlightSearchAgent:
    """Orchestrates Skyscanner indicative searches (anywhere / anytime / date ranges)."""

    def __init__(self, client: SkyscannerClient) -> None:
        self._client = client
        self._place_cache: dict[str, ResolvedPlace] = {}

    def resolve_place(self, term: str, *, config: SearchConfig) -> ResolvedPlace:
        key = term.strip().lower()
        if key in self._place_cache:
            return self._place_cache[key]
        matches = self._client.autosuggest(term, market=config.market, locale=config.locale)
        if not matches:
            raise ValueError(f"No Skyscanner place found for {term!r}")
        place = matches[0]
        self._place_cache[key] = place
        return place

    def run(self, config: SearchConfig) -> list:
        all_quotes = []
        allowed_countries = (
            {normalize_country_name(c) for c in config.filter_countries}
            if config.filter_countries
            else None
        )
        continent_filters: set[str] = set()
        region_filters: set[str] = set()
        for label in config.filter_continents:
            if label in REGION_COUNTRY_IDS:
                region_filters.add(label)
            else:
                continent_filters.add(label)
        allowed_continents = continent_filters or None
        allowed_regions = region_filters or None

        destinations = self._build_destinations(config)
        leg_dates = [self._client.leg_date_payload(config)]

        for origin in config.origins:
            for destination in destinations:
                query = self._client.build_indicative_query(
                    config,
                    origin_iata=origin,
                    destination=destination,
                    leg_dates=leg_dates,
                )
                logger.info(
                    "Searching %s -> %s (%s)",
                    origin,
                    self._destination_label(destination),
                    config.date_mode.value,
                )
                payload = self._client.indicative_search(query)
                quotes = parse_indicative_response(
                    payload,
                    config=config,
                    origin_iata=origin,
                    allowed_countries=allowed_countries,
                    allowed_continents=allowed_continents,
                    allowed_regions=allowed_regions,
                )
                all_quotes.extend(quotes)

        return merge_and_rank(all_quotes, config.top_n)

    def _build_destinations(self, config: SearchConfig) -> list[dict]:
        if config.destination_mode == DestinationMode.ANYWHERE:
            if config.destination_places:
                logger.warning(
                    "destination places are ignored when mode is 'anywhere'; "
                    "use continents/countries filters instead"
                )
            return [SkyscannerClient.destination_anywhere()]

        if not config.destination_places:
            raise ValueError("destinations.places is required when mode is 'list'")

        resolved: list[dict] = []
        for term in config.destination_places:
            place = self.resolve_place(term, config=config)
            resolved.append(SkyscannerClient.destination_from_place(place))
        return resolved

    @staticmethod
    def _destination_label(destination: dict) -> str:
        if destination.get("anywhere"):
            return "anywhere"
        qp = destination.get("queryPlace") or {}
        return qp.get("iata") or qp.get("entityId") or "place"
