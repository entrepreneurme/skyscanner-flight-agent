from __future__ import annotations

import os
from typing import Any

import httpx

from skyscanner_agent.models import MonthYear, ResolvedPlace, SearchConfig, TripType


class SkyscannerClient:
    BASE_URL = "https://partners.api.skyscanner.net"

    def __init__(self, api_key: str, *, timeout: float = 60.0) -> None:
        if not api_key:
            raise ValueError(
                "Skyscanner API key is required. Set SKYSCANNER_API_KEY or pass --api-key."
            )
        self._api_key = api_key
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SkyscannerClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @classmethod
    def from_env(cls) -> SkyscannerClient:
        return cls(os.environ.get("SKYSCANNER_API_KEY", "").strip())

    def autosuggest(self, search_term: str, *, market: str, locale: str) -> list[ResolvedPlace]:
        payload = {
            "query": {
                "market": market,
                "locale": locale,
                "searchTerm": search_term,
            }
        }
        response = self._client.post("/apiservices/v3/autosuggest/flights", json=payload)
        response.raise_for_status()
        data = response.json()
        places: list[ResolvedPlace] = []
        for item in data.get("places", []):
            places.append(
                ResolvedPlace(
                    name=item.get("name") or search_term,
                    entity_id=item.get("entityId"),
                    iata=item.get("iataCode"),
                    place_type=item.get("type"),
                    country_name=item.get("countryName") or None,
                )
            )
        return places

    def indicative_search(self, body: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(
            "/apiservices/v3/flights/indicative/search",
            json={"query": body},
        )
        response.raise_for_status()
        return response.json()

    def build_indicative_query(
        self,
        config: SearchConfig,
        *,
        origin_iata: str,
        destination: dict[str, Any],
        leg_dates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        query: dict[str, Any] = {
            "market": config.market,
            "locale": config.locale,
            "currency": config.currency,
            "queryLegs": [],
        }

        grouping = self._date_grouping(config)
        if grouping:
            query["dateTimeGroupingType"] = grouping

        for dates in leg_dates:
            leg: dict[str, Any] = {
                "originPlace": {"queryPlace": {"iata": origin_iata.upper()}},
                "destinationPlace": destination,
            }
            leg.update(dates)
            query["queryLegs"].append(leg)

        if config.trip_type == TripType.RETURN and len(query["queryLegs"]) == 1:
            inbound = {
                "originPlace": query["queryLegs"][0]["destinationPlace"],
                "destinationPlace": query["queryLegs"][0]["originPlace"],
            }
            inbound.update(leg_dates[0] if leg_dates else {"anytime": True})
            query["queryLegs"].append(inbound)

        return query

    def _date_grouping(self, config: SearchConfig) -> str | None:
        if config.date_mode.value == "anytime":
            return "DATE_TIME_GROUPING_TYPE_BY_MONTH"
        if config.date_start and config.date_end:
            if (
                config.date_start.year == config.date_end.year
                and config.date_start.month == config.date_end.month
            ):
                return "DATE_TIME_GROUPING_TYPE_BY_DATE"
            return "DATE_TIME_GROUPING_TYPE_BY_MONTH"
        return None

    def leg_date_payload(self, config: SearchConfig) -> dict[str, Any]:
        if config.date_mode.value == "anytime":
            return {"anytime": True}
        if not config.date_start or not config.date_end:
            raise ValueError("date_start and date_end are required when date_mode is range")
        return {
            "dateRange": {
                "startDate": config.date_start.to_api(),
                "endDate": config.date_end.to_api(),
            }
        }

    @staticmethod
    def destination_anywhere() -> dict[str, Any]:
        return {"anywhere": True}

    @staticmethod
    def destination_from_place(place: ResolvedPlace) -> dict[str, Any]:
        if place.entity_id and place.place_type == "PLACE_TYPE_COUNTRY":
            return {"queryPlace": {"entityId": place.entity_id}}
        if place.iata:
            return {"queryPlace": {"iata": place.iata.upper()}}
        if place.entity_id:
            return {"queryPlace": {"entityId": place.entity_id}}
        raise ValueError(f"Could not build destination for {place.name!r}")
