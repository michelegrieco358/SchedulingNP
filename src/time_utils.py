"""Utility helpers for handling time expressions in minutes."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Union


def parse_hhmm_to_min(value: Union[str, time]) -> int:
    """Return minutes from 00:00 given a HH:MM string or ``datetime.time``.

    Strings ``"24:00"`` and ``"24:00:00"`` are accepted and mapped to 1440 minutes,
    representing the end of day (used for shift end times).
    """
    if isinstance(value, time):
        total = value.hour * 60 + value.minute
        if value.second:
            total += int(round(value.second / 60))
        return _validate_minutes(total)

    text = str(value).strip()
    if text in {"24:00", "24:00:00"}:
        return 1440
    for pattern in ("%H:%M", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, pattern)
            return _validate_minutes(parsed.hour * 60 + parsed.minute)
        except ValueError:
            continue
    raise ValueError(f"Orario non valido '{value}' (atteso HH:MM)")


def normalize_2400(minutes: int) -> int:
    """Clamp minutes to the inclusive range [0, 1440]."""
    return _validate_minutes(minutes)


def add_minutes(day: date, minutes: int) -> datetime:
    """Combine a ``date`` with a minute offset from midnight."""
    minutes = normalize_2400(minutes)
    base = datetime.combine(day, time(0, 0))
    return base + timedelta(minutes=minutes)


def same_day(day: date, other: Union[date, datetime]) -> bool:
    """Return ``True`` if ``other`` falls on the same calendar day."""
    if isinstance(other, datetime):
        return day == other.date()
    return day == other


def _validate_minutes(minutes: int) -> int:
    if minutes < 0:
        raise ValueError("I minuti non possono essere negativi")
    if minutes > 1440:
        raise ValueError("I minuti non possono superare 1440 (24:00)")
    return minutes
