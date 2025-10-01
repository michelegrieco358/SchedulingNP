from __future__ import annotations

from datetime import date, time

import pytest

from src import time_utils


def test_parse_hhmm_to_min_valid() -> None:
    assert time_utils.parse_hhmm_to_min("08:30") == 510
    assert time_utils.parse_hhmm_to_min(time(23, 45)) == 1425


def test_parse_hhmm_to_min_invalid() -> None:
    with pytest.raises(ValueError):
        time_utils.parse_hhmm_to_min("25:00")


def test_add_minutes_and_same_day() -> None:
    base = date(2024, 1, 1)
    dt = time_utils.add_minutes(base, 90)
    assert dt.hour == 1 and dt.minute == 30
    assert time_utils.same_day(base, dt)


def test_normalize_2400() -> None:
    assert time_utils.normalize_2400(1440) == 1440
    with pytest.raises(ValueError):
        time_utils.normalize_2400(1500)
