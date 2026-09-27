from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class DestinationMode(str, Enum):
    ANYWHERE = "anywhere"
    LIST = "list"


class DateMode(str, Enum):
    RANGE = "range"
    ANYTIME = "anytime"


class TripType(str, Enum):
    ONE_WAY = "one_way"
    RETURN = "return"


@dataclass
class MonthYear:
    year: int
    month: int

    @classmethod
    def parse(cls, value: str) -> MonthYear:
        parts = value.strip().split("-")
        if len(parts) != 2:
            raise ValueError(f"Expected YYYY-MM, got {value!r}")
        year, month = int(parts[0]), int(parts[1])
        if not 1 <= month <= 12:
            raise ValueError(f"Month must be 1-12, got {month}")
        return cls(year=year, month=month)

    def to_api(self) -> dict[str, int]:
        return {"year": self.year, "month": self.month}


@dataclass
class SearchConfig:
    market: str = "UK"
    locale: str = "en-GB"
    currency: str = "GBP"
    origins: list[str] = field(default_factory=list)
    destination_mode: DestinationMode = DestinationMode.ANYWHERE
    destination_places: list[str] = field(default_factory=list)
    filter_continents: list[str] = field(default_factory=list)
    filter_countries: list[str] = field(default_factory=list)
    date_mode: DateMode = DateMode.RANGE
    date_start: MonthYear | None = None
    date_end: MonthYear | None = None
    trip_type: TripType = TripType.ONE_WAY
    top_n: int = 25
    max_results_per_origin: int = 100


@dataclass
class ResolvedPlace:
    name: str
    entity_id: str | None = None
    iata: str | None = None
    place_type: str | None = None
    country_name: str | None = None


@dataclass
class FlightQuote:
    origin_code: str
    origin_name: str
    destination_code: str
    destination_name: str
    destination_country: str | None
    price_amount: float
    currency: str
    is_direct: bool
    outbound_date: str | None
    inbound_date: str | None
    carrier_name: str | None
    deep_link_hint: str | None = None

    @property
    def sort_key(self) -> tuple[float, str, str]:
        return (self.price_amount, self.origin_code, self.destination_code)


PlaceRef = Literal["iata", "entity"]
