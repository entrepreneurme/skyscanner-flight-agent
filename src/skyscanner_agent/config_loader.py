from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from skyscanner_agent.geo import normalize_continent
from skyscanner_agent.models import DateMode, DestinationMode, MonthYear, SearchConfig, TripType


def load_search_config(path: Path) -> SearchConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return config_from_dict(raw)


def config_from_dict(raw: dict[str, Any]) -> SearchConfig:
    origins = [str(o).upper() for o in raw.get("origins", [])]
    if not origins:
        raise ValueError("At least one origin airport IATA code is required")

    dest_raw = raw.get("destinations", {})
    if isinstance(dest_raw, list):
        dest_raw = {"mode": "list", "places": dest_raw}

    mode_str = str(dest_raw.get("mode", "anywhere")).lower()
    destination_mode = DestinationMode(mode_str)

    dates = raw.get("dates", {})
    date_mode = DateMode(str(dates.get("mode", "range")).lower())
    date_start = MonthYear.parse(dates["start"]) if dates.get("start") else None
    date_end = MonthYear.parse(dates["end"]) if dates.get("end") else None

    trip = raw.get("trip", {})
    trip_type = TripType(str(trip.get("type", "one_way")).lower())

    output = raw.get("output", {})
    continents = [normalize_continent(c) for c in dest_raw.get("continents", [])]
    countries = [str(c).strip() for c in dest_raw.get("countries", [])]

    return SearchConfig(
        market=str(raw.get("market", "UK")),
        locale=str(raw.get("locale", "en-GB")),
        currency=str(raw.get("currency", "GBP")),
        origins=origins,
        destination_mode=destination_mode,
        destination_places=[str(p) for p in dest_raw.get("places", [])],
        filter_continents=continents,
        filter_countries=countries,
        date_mode=date_mode,
        date_start=date_start,
        date_end=date_end,
        trip_type=trip_type,
        top_n=int(output.get("top", 25)),
        max_results_per_origin=int(output.get("max_per_origin", 100)),
    )
