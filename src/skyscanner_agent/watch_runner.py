from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from skyscanner_agent.browser.google_flights import scrape_google_flights
from skyscanner_agent.price_store import PriceHistoryStore, PriceRecord
from skyscanner_agent.trip_windows import TripWindow, generate_trip_windows, sample_windows_per_month
from skyscanner_agent.window_search import DEFAULT_INDIA_ORIGINS

DEFAULT_REGION_DESTINATIONS: dict[str, str] = {
    "Japan": "Tokyo",
    "Thailand": "Bangkok",
    "Denpasar": "Denpasar",
    "Europe": "Paris",
}


@dataclass
class WatchJob:
    origin: str
    region: str
    destination_query: str
    outbound: date
    inbound: date

    @property
    def trip_days(self) -> int:
        return (self.inbound - self.outbound).days


@dataclass
class WatchConfig:
    interval_hours: float = 4.0
    max_jobs_per_run: int = 24
    delay_between_jobs_sec: float = 6.0
    year: int = 2027
    origins: list[str] | None = None
    regions: dict[str, str] | None = None
    windows_per_month: int = 1
    history_path: Path = Path("data/price_history.jsonl")
    state_path: Path = Path("data/watch_state.json")
    headless: bool = True


def load_watch_config(path: Path) -> WatchConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return WatchConfig(
        interval_hours=float(raw.get("interval_hours", 4)),
        max_jobs_per_run=int(raw.get("max_jobs_per_run", 24)),
        delay_between_jobs_sec=float(raw.get("delay_between_jobs_sec", 6)),
        year=int(raw.get("year", 2027)),
        origins=[o.upper() for o in raw.get("origins", DEFAULT_INDIA_ORIGINS)],
        regions=raw.get("regions") or DEFAULT_REGION_DESTINATIONS,
        windows_per_month=int(raw.get("windows_per_month", 1)),
        history_path=Path(raw.get("history_path", "data/price_history.jsonl")),
        state_path=Path(raw.get("state_path", "data/watch_state.json")),
        headless=bool(raw.get("headless", True)),
    )


def build_watch_jobs(config: WatchConfig) -> list[WatchJob]:
    windows = generate_trip_windows(
        outbound_start=date(config.year, 2, 1),
        outbound_end=date(config.year, 7, 31),
        max_return=date(config.year, 8, 15),
    )
    if config.windows_per_month > 0:
        windows = sample_windows_per_month(windows, per_month=config.windows_per_month)

    origins = config.origins or DEFAULT_INDIA_ORIGINS
    regions = config.regions or DEFAULT_REGION_DESTINATIONS
    jobs: list[WatchJob] = []
    for origin in origins:
        for region, dest in regions.items():
            for window in windows:
                jobs.append(
                    WatchJob(
                        origin=origin,
                        region=region,
                        destination_query=dest,
                        outbound=window.outbound,
                        inbound=window.inbound,
                    )
                )
    return jobs


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"next_index": 0}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {"next_index": 0}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(state), encoding="utf-8")


def select_job_batch(jobs: list[WatchJob], config: WatchConfig) -> tuple[list[WatchJob], int]:
    state = _load_state(config.state_path)
    start = int(state.get("next_index", 0)) % max(len(jobs), 1)
    end = min(start + config.max_jobs_per_run, len(jobs))
    batch = jobs[start:end]
    next_index = 0 if end >= len(jobs) else end
    return batch, next_index


def run_watch_batch(config: WatchConfig, *, jobs: list[WatchJob] | None = None) -> list[PriceRecord]:
    all_jobs = jobs or build_watch_jobs(config)
    batch, next_index = select_job_batch(all_jobs, config)
    store = PriceHistoryStore(config.history_path)
    results: list[PriceRecord] = []
    now = datetime.now(timezone.utc).isoformat()

    for index, job in enumerate(batch):
        scrape = scrape_google_flights(
            origin=job.origin,
            destination=job.destination_query,
            outbound=job.outbound,
            inbound=job.inbound,
            headless=config.headless,
        )
        record = PriceRecord(
            checked_at=now,
            origin=job.origin,
            destination_query=job.destination_query,
            region=job.region,
            outbound=job.outbound.isoformat(),
            inbound=job.inbound.isoformat(),
            trip_days=job.trip_days,
            cheapest_price=scrape.cheapest_price,
            cheapest_display=scrape.cheapest_display,
            currency_hint=scrape.currency_hint,
            airline=scrape.airline,
            stops=scrape.stops,
            url=scrape.url,
            error=scrape.error,
        )
        store.append(record)
        results.append(record)
        if index + 1 < len(batch):
            time.sleep(config.delay_between_jobs_sec)

    if jobs is None:
        _save_state(config.state_path, {"next_index": next_index, "total_jobs": len(all_jobs)})
    return results


def run_watch_daemon(config: WatchConfig) -> None:
    while True:
        run_watch_batch(config)
        time.sleep(max(config.interval_hours, 0.25) * 3600)
