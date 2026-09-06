from datetime import datetime

import pytest

from oli.scheduler import MIN_INTERVAL_SEC, compute_next_run

NOW = datetime(2026, 9, 5, 10, 0, 0).timestamp()


def test_interval_basic():
    nr = compute_next_run("interval", NOW, interval_sec=90 * 60)
    assert abs(nr - (NOW + 5400)) < 1


def test_interval_clamped_to_minimum():
    nr = compute_next_run("interval", NOW, interval_sec=5)
    assert nr - NOW == MIN_INTERVAL_SEC


def test_daily_time_already_past_rolls_to_tomorrow():
    nr = compute_next_run("daily", NOW, time_of_day="07:00")
    d = datetime.fromtimestamp(nr)
    assert d.day == 6 and d.hour == 7


def test_daily_time_ahead_stays_today():
    nr = compute_next_run("daily", NOW, time_of_day="18:00")
    d = datetime.fromtimestamp(nr)
    assert d.day == 5 and d.hour == 18


def test_daily_malformed_defaults_to_nine():
    nr = compute_next_run("daily", NOW, time_of_day="not-a-time")
    assert datetime.fromtimestamp(nr).hour == 9


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        compute_next_run("weekly", NOW)
