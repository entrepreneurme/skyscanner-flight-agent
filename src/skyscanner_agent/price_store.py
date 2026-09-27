from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class PriceRecord:
    checked_at: str
    origin: str
    destination_query: str
    region: str
    outbound: str
    inbound: str
    trip_days: int
    cheapest_price: float | None
    cheapest_display: str | None
    currency_hint: str | None
    airline: str | None
    stops: str | None
    url: str
    error: str | None = None

    def key(self) -> str:
        return f"{self.origin}|{self.region}|{self.outbound}|{self.inbound}"


class PriceHistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._latest: dict[str, PriceRecord] = {}
        self._load_latest()

    def _load_latest(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                rec = PriceRecord(**row)
            except (json.JSONDecodeError, TypeError):
                continue
            self._latest[rec.key()] = rec

    def append(self, record: PriceRecord) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        self._latest[record.key()] = record

    def previous(self, record: PriceRecord) -> PriceRecord | None:
        return self._latest.get(record.key())

    def price_delta(self, record: PriceRecord) -> float | None:
        prev = self.previous(record)
        if prev is None or prev.cheapest_price is None or record.cheapest_price is None:
            return None
        return record.cheapest_price - prev.cheapest_price
