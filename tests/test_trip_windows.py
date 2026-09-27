from datetime import date

from skyscanner_agent.trip_windows import generate_trip_windows, sample_windows_per_month


def test_trip_length_and_weekdays():
    windows = generate_trip_windows(
        outbound_start=date(2027, 2, 1),
        outbound_end=date(2027, 2, 28),
        max_return=date(2027, 3, 15),
    )
    assert windows
    for w in windows:
        assert w.outbound.weekday() in (3, 4)
        assert w.inbound.weekday() in (6, 0)
        assert 9 <= w.trip_days <= 11


def test_sample_per_month():
    windows = generate_trip_windows(
        outbound_start=date(2027, 2, 1),
        outbound_end=date(2027, 4, 30),
    )
    sampled = sample_windows_per_month(windows, per_month=2)
    assert len(sampled) <= 9
