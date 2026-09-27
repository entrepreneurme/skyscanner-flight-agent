# Skyscanner Flight Agent

Find the **cheapest indicative fares** across your origin airports, destination countries/regions, and travel windows — using Skyscanner Partner API features that mirror the consumer site's **Everywhere** (`anywhere`) and **Anytime** (`anytime`) exploration.

## Prerequisites

1. **Skyscanner Partner API key** — apply at [Skyscanner Partner Portal](https://partners.skyscanner.net/).
2. Python 3.11+

Indicative prices are cached estimates (great for discovery). For bookable live fares, extend the client with the Live Prices create/poll flow.

## Setup

```bash
cd /agent
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
export SKYSCANNER_API_KEY="your-key-here"
```

## Quick start

Print an example config:

```bash
skyscanner-agent example-config > config.yaml
```

Run with YAML (recommended):

```bash
skyscanner-agent search -c config.yaml
```

Run from CLI flags:

```bash
skyscanner-agent search \
  --origins DEL,BOM,LHR \
  --destinations anywhere \
  --continents Europe,Southeast\ Asia \
  --date-mode range \
  --from-month 2026-06 \
  --to-month 2026-09 \
  --market IN \
  --currency INR \
  --top 25
```

Anytime search (Skyscanner "Anytime"):

```bash
skyscanner-agent search \
  --origins LHR \
  --destinations anywhere \
  --continents Europe \
  --date-mode anytime \
  --currency GBP
```

Explicit countries/cities instead of everywhere:

```bash
skyscanner-agent search \
  --origins DEL \
  --destinations list \
  --places "Spain,Thailand,Japan" \
  --date-mode range \
  --from-month 2026-07 \
  --to-month 2026-08
```

JSON output for automation:

```bash
skyscanner-agent search -c config.yaml --json
```

## How it maps to Skyscanner UI

| Your goal | Config | API |
|-----------|--------|-----|
| Flying from several home airports | `origins: [DEL, BOM, ...]` | One indicative query per origin |
| "Everywhere" destination | `destinations.mode: anywhere` | `destinationPlace: { "anywhere": true }` |
| Filter by continent / region | `continents:` | Post-filter on API results (continents are **not** valid indicative destinations) |
| Named countries | `countries:` or `mode: list` + `places` | Country `entityId` via autosuggest |
| Flexible dates in a window | `dates.mode: range` + `start`/`end` | `dateRange` + grouping by date/month |
| Fully flexible dates | `dates.mode: anytime` | `anytime: true` + monthly grouping |

## Architecture

```mermaid
flowchart LR
  CLI[skyscanner-agent CLI] --> Agent[FlightSearchAgent]
  Agent --> Client[SkyscannerClient]
  Client --> Indicative[Indicative Search API]
  Client --> Autosuggest[Autosuggest API]
  Agent --> Parser[Quote parser + geo filters]
  Parser --> Results[Ranked cheapest routes]
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `SKYSCANNER_API_KEY` | Partner API key (required) |

## Tests

```bash
pytest
```

## Browser watch (no Skyscanner key)

Uses **Google Flights in Chromium** (Playwright). Intended for personal price tracking; Google may rate-limit or change the page layout.

```bash
pip install -e ".[browser]"
python3 -m playwright install chromium

# Preview the next batch (rotating queue)
python3 -m skyscanner_agent.cli watch -c config.watch.yaml --dry-run

# One batch (~24 routes), append to data/price_history.jsonl
python3 -m skyscanner_agent.cli watch -c config.watch.yaml --once

# Loop: batch → sleep 4 hours → repeat
python3 -m skyscanner_agent.cli watch -c config.watch.yaml --daemon
```

**Cron (every 4 hours):**

```cron
0 */4 * * * cd /agent && /usr/bin/python3 -m skyscanner_agent.cli watch -c config.watch.yaml --once >> data/watch.log 2>&1
```

Edit `config.watch.yaml` for origins, regions, `max_jobs_per_run`, and `interval_hours`. The runner rotates through all origin × region × Thu/Fri window jobs over multiple cycles.


- Requires an approved Skyscanner partner key; without it, API calls will fail.
- Continents cannot be passed directly to the indicative API; this agent uses **`anywhere`** plus geographic filters (same practical outcome as narrowing Everywhere results).
- Indicative prices are not guaranteed live booking prices.
- Return-trip indicative searches use symmetric date settings on both legs; tune in `config.yaml` as Skyscanner documents for your market.

## License

MIT
