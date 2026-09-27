from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from skyscanner_agent.agent import FlightSearchAgent
from skyscanner_agent.client import SkyscannerClient
from skyscanner_agent.config_loader import config_from_dict, load_search_config
from skyscanner_agent.models import SearchConfig, TripType
from skyscanner_agent.price_store import PriceHistoryStore
from skyscanner_agent.watch_runner import (
    build_watch_jobs,
    load_watch_config,
    run_watch_batch,
    run_watch_daemon,
    select_job_batch,
)
from skyscanner_agent.window_search import (
    DEFAULT_DESTINATION_GROUPS,
    DEFAULT_INDIA_ORIGINS,
    RoundTripWindowSearch,
    build_default_windows,
)

console = Console()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skyscanner-agent",
        description=(
            "Find cheapest Skyscanner indicative fares using anywhere/anytime and date ranges."
        ),
    )
    parser.add_argument(
        "--api-key",
        help="Skyscanner partner API key (default: SKYSCANNER_API_KEY env var)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="Run flight searches")
    search.add_argument("-c", "--config", type=Path, help="YAML config file")
    search.add_argument("--origins", help="Comma-separated origin IATA codes, e.g. DEL,BOM,LHR")
    search.add_argument(
        "--destinations",
        choices=["anywhere", "list"],
        help="Use Skyscanner anywhere search or explicit destination list",
    )
    search.add_argument(
        "--places",
        help="Comma-separated countries/cities when --destinations=list",
    )
    search.add_argument(
        "--continents",
        help="Filter anywhere results by continent (comma-separated)",
    )
    search.add_argument(
        "--countries",
        help="Filter anywhere results by country name (comma-separated)",
    )
    search.add_argument(
        "--date-mode",
        choices=["range", "anytime"],
        help="Use a month range or Skyscanner anytime pricing",
    )
    search.add_argument("--from-month", dest="from_month", help="Start month YYYY-MM")
    search.add_argument("--to-month", dest="to_month", help="End month YYYY-MM")
    search.add_argument("--market", default="UK")
    search.add_argument("--locale", default="en-GB")
    search.add_argument("--currency", default="GBP")
    search.add_argument("--top", type=int, default=25)
    search.add_argument("--json", action="store_true", help="Print JSON instead of a table")
    search.add_argument(
        "--trip",
        choices=["one_way", "return"],
        default="one_way",
        help="One-way or return indicative search",
    )

    trips = sub.add_parser(
        "search-trips",
        help="Round-trip search with Thu/Fri out and Sun/Mon back (9–11 days)",
    )
    trips.add_argument("--year", type=int, default=2027, help="Travel year (default 2027)")
    trips.add_argument(
        "--windows-per-month",
        type=int,
        default=2,
        help="Sample N departure windows per month to limit API calls (0 = all)",
    )
    trips.add_argument("--market", default="IN")
    trips.add_argument("--locale", default="en-GB")
    trips.add_argument("--currency", default="INR")
    trips.add_argument("--top", type=int, default=15, help="Top deals per destination region")
    trips.add_argument("--json", action="store_true")
    trips.add_argument(
        "--dry-run",
        action="store_true",
        help="Show trip windows and API call count without calling Skyscanner",
    )

    watch = sub.add_parser(
        "watch",
        help="Google Flights browser checks on an interval (no API key)",
    )
    watch.add_argument(
        "-c",
        "--config",
        type=Path,
        default=Path("config.watch.yaml"),
        help="Watch config YAML",
    )
    watch.add_argument(
        "--once",
        action="store_true",
        help="Run one batch and exit (use with cron every 4 hours)",
    )
    watch.add_argument(
        "--daemon",
        action="store_true",
        help="Loop forever: batch scrape then sleep interval_hours",
    )
    watch.add_argument("--json", action="store_true")
    watch.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window (debug captchas / layout)",
    )
    watch.add_argument(
        "--dry-run",
        action="store_true",
        help="List jobs in the next batch without opening a browser",
    )

    sub.add_parser("example-config", help="Print an example YAML config to stdout")
    return parser


def example_config_text() -> str:
    return """# Skyscanner Flight Agent — example configuration
# Requires SKYSCANNER_API_KEY from https://partners.skyscanner.net/

market: IN
locale: en-GB
currency: INR

origins:
  - DEL
  - BOM
  - BLR

destinations:
  # anywhere = Skyscanner "Everywhere" style indicative search
  mode: anywhere
  # Optional filters applied to anywhere results (continents are not valid API destinations)
  continents:
    - Europe
    - Southeast Asia
  countries: []

dates:
  # range = search cheapest prices across months/days in the window
  mode: range
  start: 2026-06
  end: 2026-09
  # mode: anytime   # Skyscanner "Anytime" — uncomment instead of range

trip:
  type: one_way   # or return

output:
  top: 30
  max_per_origin: 120
"""


def config_from_cli_args(args: argparse.Namespace) -> SearchConfig:
    if args.config:
        return load_search_config(args.config)

    if not args.origins:
        raise SystemExit("Provide --config or --origins")

    raw: dict = {
        "market": args.market,
        "locale": args.locale,
        "currency": args.currency,
        "origins": [o.strip() for o in args.origins.split(",") if o.strip()],
        "destinations": {
            "mode": args.destinations or "anywhere",
            "places": [p.strip() for p in (args.places or "").split(",") if p.strip()],
            "continents": [c.strip() for c in (args.continents or "").split(",") if c.strip()],
            "countries": [c.strip() for c in (args.countries or "").split(",") if c.strip()],
        },
        "dates": {
            "mode": args.date_mode or "range",
            "start": args.from_month,
            "end": args.to_month,
        },
        "trip": {"type": args.trip},
        "output": {"top": args.top},
    }
    return config_from_dict(raw)


def render_table(quotes) -> None:
    table = Table(title="Cheapest indicative fares")
    table.add_column("Price", justify="right")
    table.add_column("From")
    table.add_column("To")
    table.add_column("Country")
    table.add_column("Outbound")
    table.add_column("Direct")
    table.add_column("Carrier")

    for q in quotes:
        price = f"{q.currency} {q.price_amount:,.0f}"
        table.add_row(
            price,
            f"{q.origin_code} ({q.origin_name})",
            f"{q.destination_code} ({q.destination_name})",
            q.destination_country or "-",
            q.outbound_date or "-",
            "yes" if q.is_direct else "no",
            q.carrier_name or "-",
        )
    console.print(table)


def render_trip_table(results) -> None:
    table = Table(title="Round-trip deals (Thu/Fri → Sun/Mon, 9–11 days)")
    table.add_column("Region")
    table.add_column("Price", justify="right")
    table.add_column("From")
    table.add_column("To")
    table.add_column("Outbound")
    table.add_column("Return")
    table.add_column("Days", justify="right")
    for row in results:
        q = row.quote
        table.add_row(
            row.destination_group,
            f"{q.currency} {q.price_amount:,.0f}",
            q.origin_code,
            f"{q.destination_code} ({q.destination_name})",
            q.outbound_date or "-",
            q.inbound_date or "-",
            str(row.trip_days),
        )
    console.print(table)


def cmd_search_trips(args: argparse.Namespace) -> int:
    sample = args.windows_per_month if args.windows_per_month > 0 else None
    windows = build_default_windows(year=args.year, sample_per_month=sample)
    n_dest_queries = sum(len(v) for v in DEFAULT_DESTINATION_GROUPS.values())
    api_calls = len(DEFAULT_INDIA_ORIGINS) * n_dest_queries * len(windows)

    if args.dry_run:
        console.print(
            f"[bold]Planned indicative API calls:[/bold] {api_calls} "
            f"({len(DEFAULT_INDIA_ORIGINS)} origins × {n_dest_queries} destination lookups × "
            f"{len(windows)} date windows)"
        )
        console.print(f"Sample windows ({len(windows)}):")
        for w in windows[:8]:
            console.print(
                f"  {w.outbound.isoformat()} ({w.outbound.strftime('%a')}) → "
                f"{w.inbound.isoformat()} ({w.inbound.strftime('%a')}), {w.trip_days}d"
            )
        if len(windows) > 8:
            console.print("  …")
        return 0

    config = SearchConfig(
        market=args.market,
        locale=args.locale,
        currency=args.currency,
        origins=DEFAULT_INDIA_ORIGINS,
        trip_type=TripType.RETURN,
        top_n=args.top,
    )
    api_key = (args.api_key or os.environ.get("SKYSCANNER_API_KEY", "")).strip()
    with SkyscannerClient(api_key) as client:
        agent = FlightSearchAgent(client)
        searcher = RoundTripWindowSearch(client, agent)
        results = searcher.search(config=config, windows=windows)

    if args.json:
        payload = [
            {
                "region": r.destination_group,
                "price": r.quote.price_amount,
                "currency": r.quote.currency,
                "origin": r.quote.origin_code,
                "destination": r.quote.destination_code,
                "destination_name": r.quote.destination_name,
                "outbound": r.quote.outbound_date,
                "return": r.quote.inbound_date,
                "trip_days": r.trip_days,
                "carrier": r.quote.carrier_name,
            }
            for r in results
        ]
        print(json.dumps(payload, indent=2))
    else:
        if not results:
            console.print("[yellow]No quotes returned. Check API key and partner access.[/yellow]")
        else:
            render_trip_table(results)
    return 0


def render_watch_table(records, store: PriceHistoryStore) -> None:
    table = Table(title="Skyscanner watch results")
    table.add_column("Region")
    table.add_column("From")
    table.add_column("Month / dates")
    table.add_column("Price", justify="right")
    table.add_column("Δ vs last", justify="right")
    table.add_column("Destination")
    table.add_column("Mode")
    for rec in records:
        if rec.error:
            price = f"[red]error[/red]"
            delta = "-"
        else:
            price = rec.cheapest_display or "-"
            change = store.price_delta(rec)
            if change is None:
                delta = "-"
            elif change < 0:
                delta = f"[green]{change:,.0f}[/green]"
            elif change > 0:
                delta = f"[red]+{change:,.0f}[/red]"
            else:
                delta = "0"
        table.add_row(
            rec.region,
            rec.origin,
            rec.travel_month or f"{rec.outbound} → {rec.inbound}",
            price,
            delta,
            rec.destination_query[:28] if rec.destination_query else "-",
            rec.search_mode,
        )
    console.print(table)
    console.print(f"History appended to [bold]{store.path}[/bold]")


def cmd_watch(args: argparse.Namespace) -> int:
    if not args.config.exists():
        raise SystemExit(f"Config not found: {args.config}")

    config = load_watch_config(args.config)
    if args.headed:
        config.headless = False

    jobs = build_watch_jobs(config)
    if args.dry_run:
        batch, next_index = select_job_batch(jobs, config)
        console.print(
            f"[bold]Total jobs:[/bold] {len(jobs)} | "
            f"[bold]Next batch:[/bold] {len(batch)} | "
            f"[bold]interval:[/bold] {config.interval_hours}h"
        )
        for job in batch[:12]:
            if hasattr(job, "travel_month"):
                console.print(
                    f"  {job.origin} → Everywhere ({job.travel_month}) "
                    f"[explore / anytime-style month]"
                )
            else:
                console.print(
                    f"  {job.origin} → {job.destination_iata} ({job.region}) "
                    f"{job.outbound} → {job.inbound}"
                )
        if len(batch) > 12:
            console.print("  …")
        console.print(f"Next state index after run: {next_index}")
        return 0

    if args.daemon and args.once:
        raise SystemExit("Use either --once or --daemon, not both")

    if args.daemon:
        console.print(
            f"[bold]Starting watch daemon[/bold] — every {config.interval_hours}h, "
            f"{config.max_jobs_per_run} routes per batch"
        )
        run_watch_daemon(config)
        return 0

    records = run_watch_batch(config)
    store = PriceHistoryStore(config.history_path)

    if args.json:
        payload = []
        for rec in records:
            row = rec.__dict__.copy()
            row["delta"] = store.price_delta(rec)
            payload.append(row)
        print(json.dumps(payload, indent=2))
    else:
        render_watch_table(records, store)
        drops = [
            r
            for r in records
            if (delta := store.price_delta(r)) is not None and delta < 0
        ]
        if drops:
            console.print(f"[green]{len(drops)} route(s) dropped since last check.[/green]")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    config = config_from_cli_args(args)
    api_key = (args.api_key or os.environ.get("SKYSCANNER_API_KEY", "")).strip()
    with SkyscannerClient(api_key) as client:
        agent = FlightSearchAgent(client)
        quotes = agent.run(config)

    if args.json:
        payload = [
            {
                "price": q.price_amount,
                "currency": q.currency,
                "origin": q.origin_code,
                "origin_name": q.origin_name,
                "destination": q.destination_code,
                "destination_name": q.destination_name,
                "country": q.destination_country,
                "outbound_date": q.outbound_date,
                "inbound_date": q.inbound_date,
                "direct": q.is_direct,
                "carrier": q.carrier_name,
                "skyscanner_url": q.deep_link_hint,
            }
            for q in quotes
        ]
        print(json.dumps(payload, indent=2))
    else:
        if not quotes:
            console.print("[yellow]No quotes matched your filters.[/yellow]")
        else:
            render_table(quotes)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if args.command == "example-config":
        print(example_config_text())
        return 0
    if args.command == "search-trips":
        return cmd_search_trips(args)
    if args.command == "watch":
        return cmd_watch(args)
    if args.command == "search":
        return cmd_search(args)
    parser.error(f"Unknown command {args.command}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
