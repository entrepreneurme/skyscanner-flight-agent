from __future__ import annotations

import time
from dataclasses import dataclass
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from skyscanner_agent.browser.skyscanner_web import (
    REGION_KEYWORDS,
    pick_cheapest_per_region,
    scrape_explore_anywhere,
    scrape_roundtrip_day_view,
)
from skyscanner_agent.price_store import PriceHistoryStore, PriceRecord
from skyscanner_agent.trip_windows import generate_trip_windows, sample_windows_per_month
from skyscanner_agent.window_search import DEFAULT_INDIA_ORIGINS

DEFAULT_FILTER_REGIONS = list(REGION_KEYWORDS.keys())

# Round-trip fallback destination IATA codes when not using explore-anywhere
DEFAULT_REGION_IATA: dict[str, str] = {
    "Japan": "TYO",
    "Thailand": "BKK",
    "Denpasar": "DPS",
    "Europe": "PARI",
}


@dataclass
class ExploreWatchJob:
    origin: str
    year: int
    month: int

    @property
    def travel_month(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


@dataclass
class RoundtripWatchJob:
    origin: str
    region: str
    destination_iata: str
    outbound: date
    inbound: date

    @property
    def trip_days(self) -> int:
        return (self.inbound - self.outbound).days


@dataclass
class WatchConfig:
    provider: str = "skyscanner"
    search_mode: str = "explore_anywhere"
    interval_hours: float = 4.0
    max_jobs_per_run: int = 24
    delay_between_jobs_sec: float = 8.0
    year: int = 2027
    month_start: int = 2
    month_end: int = 7
    origins: list[str] | None = None
    filter_regions: list[str] | None = None
    region_iata: dict[str, str] | None = None
    windows_per_month: int = 1
    market_site: str = "skyscanner.co.in"
    browser_user_data_dir: Path | None = Path("data/browser_profile")
    history_path: Path = Path("data/price_history.jsonl")
    state_path: Path = Path("data/watch_state.json")
    headless: bool = False


def load_watch_config(path: Path) -> WatchConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    user_data = raw.get("browser_user_data_dir")
    return WatchConfig(
        provider=str(raw.get("provider", "skyscanner")).lower(),
        search_mode=str(raw.get("search_mode", "explore_anywhere")).lower(),
        interval_hours=float(raw.get("interval_hours", 4)),
        max_jobs_per_run=int(raw.get("max_jobs_per_run", 24)),
        delay_between_jobs_sec=float(raw.get("delay_between_jobs_sec", 8)),
        year=int(raw.get("year", 2027)),
        month_start=int(raw.get("month_start", 2)),
        month_end=int(raw.get("month_end", 7)),
        origins=[o.upper() for o in raw.get("origins", DEFAULT_INDIA_ORIGINS)],
        filter_regions=raw.get("filter_regions") or DEFAULT_FILTER_REGIONS,
        region_iata=raw.get("region_iata") or DEFAULT_REGION_IATA,
        windows_per_month=int(raw.get("windows_per_month", 1)),
        market_site=str(raw.get("market_site", "skyscanner.co.in")),
        browser_user_data_dir=Path(user_data) if user_data else Path("data/browser_profile"),
        history_path=Path(raw.get("history_path", "data/price_history.jsonl")),
        state_path=Path(raw.get("state_path", "data/watch_state.json")),
        headless=bool(raw.get("headless", False)),
    )


def build_explore_jobs(config: WatchConfig) -> list[ExploreWatchJob]:
    origins = config.origins or DEFAULT_INDIA_ORIGINS
    jobs: list[ExploreWatchJob] = []
    for origin in origins:
        for month in range(config.month_start, config.month_end + 1):
            jobs.append(ExploreWatchJob(origin=origin, year=config.year, month=month))
    return jobs


def build_roundtrip_jobs(config: WatchConfig) -> list[RoundtripWatchJob]:
    last_day = monthrange(config.year, config.month_end)[1]
    windows = generate_trip_windows(
        outbound_start=date(config.year, config.month_start, 1),
        outbound_end=date(config.year, config.month_end, last_day),
        max_return=date(config.year, min(config.month_end + 1, 12), 15)
        if config.month_end < 12
        else date(config.year, 12, 31),
    )
    if config.windows_per_month > 0:
        windows = sample_windows_per_month(windows, per_month=config.windows_per_month)

    origins = config.origins or DEFAULT_INDIA_ORIGINS
    region_iata = config.region_iata or DEFAULT_REGION_IATA
    regions = config.filter_regions or DEFAULT_FILTER_REGIONS
    jobs: list[RoundtripWatchJob] = []
    for origin in origins:
        for region in regions:
            dest = region_iata.get(region)
            if not dest:
                continue
            for window in windows:
                jobs.append(
                    RoundtripWatchJob(
                        origin=origin,
                        region=region,
                        destination_iata=dest,
                        outbound=window.outbound,
                        inbound=window.inbound,
                    )
                )
    return jobs


def build_watch_jobs(config: WatchConfig) -> list[Any]:
    if config.search_mode == "roundtrip":
        return build_roundtrip_jobs(config)
    return build_explore_jobs(config)


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"next_index": 0}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {"next_index": 0}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(state), encoding="utf-8")


def select_job_batch(jobs: list[Any], config: WatchConfig) -> tuple[list[Any], int]:
    state = _load_state(config.state_path)
    start = int(state.get("next_index", 0)) % max(len(jobs), 1)
    end = min(start + config.max_jobs_per_run, len(jobs))
    batch = jobs[start:end]
    next_index = 0 if end >= len(jobs) else end
    return batch, next_index


def _run_explore_job(config: WatchConfig, job: ExploreWatchJob) -> list[PriceRecord]:
    allowed = set(config.filter_regions or DEFAULT_FILTER_REGIONS)
    result = scrape_explore_anywhere(
        origin_iata=job.origin,
        year=job.year,
        month=job.month,
        allowed_regions=allowed,
        market_site=config.market_site,
        headless=config.headless,
        user_data_dir=config.browser_user_data_dir,
    )
    now = datetime.now(timezone.utc).isoformat()
    if result.error:
        return [
            PriceRecord(
                checked_at=now,
                origin=job.origin,
                destination_query="Everywhere",
                region="*",
                outbound="",
                inbound="",
                trip_days=0,
                cheapest_price=None,
                cheapest_display=None,
                currency_hint=None,
                airline=None,
                stops=None,
                url=result.url,
                error=result.error,
                travel_month=job.travel_month,
            )
        ]

    best_by_region = pick_cheapest_per_region(result.offers)
    records: list[PriceRecord] = []
    for offer in best_by_region:
        records.append(
            PriceRecord(
                checked_at=now,
                origin=job.origin,
                destination_query=offer.destination,
                region=offer.region or "?",
                outbound="",
                inbound="",
                trip_days=0,
                cheapest_price=offer.price_amount,
                cheapest_display=offer.price_display,
                currency_hint=offer.currency_hint,
                airline=None,
                stops=None,
                url=offer.url or result.url,
                travel_month=job.travel_month,
            )
        )
    return records


def _run_roundtrip_job(config: WatchConfig, job: RoundtripWatchJob) -> PriceRecord:
    result = scrape_roundtrip_day_view(
        origin_iata=job.origin,
        destination_iata=job.destination_iata,
        outbound=job.outbound,
        inbound=job.inbound,
        market_site=config.market_site,
        headless=config.headless,
        user_data_dir=config.browser_user_data_dir,
    )
    now = datetime.now(timezone.utc).isoformat()
    cheapest = None
    if result.offers:
        priced = [o for o in result.offers if o.price_amount is not None]
        if priced:
            cheapest = min(priced, key=lambda o: o.price_amount or 0)
    return PriceRecord(
        checked_at=now,
        origin=job.origin,
        destination_query=job.destination_iata,
        region=job.region,
        outbound=job.outbound.isoformat(),
        inbound=job.inbound.isoformat(),
        trip_days=job.trip_days,
        cheapest_price=cheapest.price_amount if cheapest else None,
        cheapest_display=cheapest.price_display if cheapest else None,
        currency_hint=cheapest.currency_hint if cheapest else None,
        airline=None,
        stops=None,
        url=result.url,
        error=result.error,
        search_mode="roundtrip",
        travel_month=f"{job.outbound.year:04d}-{job.outbound.month:02d}",
    )


def run_watch_batch(config: WatchConfig, *, jobs: list[Any] | None = None) -> list[PriceRecord]:
    all_jobs = jobs or build_watch_jobs(config)
    batch, next_index = select_job_batch(all_jobs, config)
    store = PriceHistoryStore(config.history_path)
    results: list[PriceRecord] = []

    for index, job in enumerate(batch):
        if isinstance(job, ExploreWatchJob):
            records = _run_explore_job(config, job)
        else:
            records = [_run_roundtrip_job(config, job)]
        for record in records:
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
