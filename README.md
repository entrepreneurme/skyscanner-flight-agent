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

## Browser watch — Skyscanner Everywhere (no API key)

Uses **skyscanner.co.in in Chromium** (Playwright): **Everywhere** browse from each origin with **`oym` whole-month** queries (Skyscanner’s anytime-style exploration in the UI).

Skyscanner may show a **CAPTCHA** on automated/datacenter IPs. Set `headless: false` and `browser_user_data_dir: data/browser_profile`, solve CAPTCHA once locally, then scheduled runs reuse cookies.

```bash
pip install -e ".[browser]"
python3 -m playwright install chromium

python3 -m skyscanner_agent.cli watch -c config.watch.yaml --dry-run
python3 -m skyscanner_agent.cli watch -c config.watch.yaml --once
python3 -m skyscanner_agent.cli watch -c config.watch.yaml --daemon
```

Default `search_mode: explore_anywhere` scans **Feb–Jul** per origin and keeps the cheapest match in **Japan / Europe / Thailand / Denpasar**. Use `search_mode: roundtrip` for Thu/Fri → Sun/Mon fixed dates.

**Cron every 4 hours:**

```cron
0 */4 * * * cd /agent && python3 -m skyscanner_agent.cli watch -c config.watch.yaml --once >> data/watch.log 2>&1
```

## Limitations

- Partner **API** path still needs `SKYSCANNER_API_KEY`; the **browser watcher** does not.
- Browser automation can break when Skyscanner changes markup; CAPTCHAs require manual intervention.
- Explore mode uses indicative browse prices, not guaranteed live fares.
- API: continents cannot be passed directly to indicative search; explore mode filters regions after the Everywhere page loads.

## License

MIT
