from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class TripWindow:
    outbound: date
    inbound: date

    @property
    def trip_days(self) -> int:
        return (self.inbound - self.outbound).days


def _valid_return_offsets(weekday: int) -> tuple[int, ...]:
    # Thu -> Sun (+10) or Mon (+11); Fri -> Sun (+9) or Mon (+10)
    if weekday == 3:
        return (10, 11)
    if weekday == 4:
        return (9, 10)
    return ()


def generate_trip_windows(
    *,
    outbound_start: date,
    outbound_end: date,
    max_return: date | None = None,
) -> list[TripWindow]:
    """Outbound Thu/Fri only; inbound Sun/Mon; trip length 9–11 days."""
    windows: list[TripWindow] = []
    current = outbound_start
    while current <= outbound_end:
        for offset in _valid_return_offsets(current.weekday()):
            inbound = current + timedelta(days=offset)
            if inbound.weekday() not in (6, 0):
                continue
            if not (9 <= offset <= 11):
                continue
            if max_return and inbound > max_return:
                continue
            windows.append(TripWindow(outbound=current, inbound=inbound))
        current += timedelta(days=1)
    return windows


def sample_windows_per_month(windows: list[TripWindow], *, per_month: int = 2) -> list[TripWindow]:
    """Keep up to N distinct outbound dates per month (Thu/Fri spread)."""
    by_month: dict[tuple[int, int], list[TripWindow]] = {}
    for w in windows:
        key = (w.outbound.year, w.outbound.month)
        by_month.setdefault(key, []).append(w)
    sampled: list[TripWindow] = []
    for month_windows in by_month.values():
        month_windows.sort(key=lambda w: (w.outbound, w.inbound))
        seen_outbound: set[date] = set()
        for w in month_windows:
            if w.outbound in seen_outbound:
                continue
            seen_outbound.add(w.outbound)
            sampled.append(w)
            if len(seen_outbound) >= per_month:
                break
        # include second return option for last picked outbound when available
        if seen_outbound:
            last_out = max(seen_outbound)
            extras = [w for w in month_windows if w.outbound == last_out]
            for w in extras[1:2]:
                if w not in sampled:
                    sampled.append(w)
    return sorted(sampled, key=lambda w: w.outbound)
