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
from skyscanner_agent.models import DateMode, DestinationMode, MonthYear, SearchConfig, TripType

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
    if args.command == "search":
        return cmd_search(args)
    parser.error(f"Unknown command {args.command}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
