import sys
from pathlib import Path
from datetime import date, time

import pandas as pd
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import precompute  # noqa: E402


def test_normalize_shift_times_handles_midnight():
    raw = pd.DataFrame(
        [
            {
                "shift_id": "N1",
                "day": date(2025, 1, 1),
                "start": time(22, 0),
                "end": time(6, 0),
                "role": "nurse",
                "required_staff": 1,
            }
        ]
    )

    norm = precompute.normalize_shift_times(raw)
    row = norm.iloc[0]

    assert row["duration_h"] == pytest.approx(8.0)
    assert row["end_dt"] > row["start_dt"]
    assert (row["end_dt"] - row["start_dt"]).total_seconds() == pytest.approx(8 * 3600)


def test_compute_gap_table_returns_expected_gap():
    raw = pd.DataFrame(
        [
            {
                "shift_id": "S1",
                "day": date(2025, 1, 1),
                "start": time(8, 0),
                "end": time(12, 0),
                "role": "desk",
                "required_staff": 1,
            },
            {
                "shift_id": "S2",
                "day": date(2025, 1, 1),
                "start": time(15, 0),
                "end": time(18, 0),
                "role": "desk",
                "required_staff": 1,
            },
        ]
    )
    norm = precompute.normalize_shift_times(raw)
    gap_table = precompute.compute_gap_table(norm)

    gap_map = {
        (row.shift_id_from, row.shift_id_to): row.gap_h
        for row in gap_table.itertuples()
    }

    assert gap_map[("S1", "S2")] == pytest.approx(3.0)
    assert ("S2", "S1") not in gap_map




def test_conflict_pairs_filters_self_and_duplicates():
    raw = pd.DataFrame([
        {
            "shift_id": "A",
            "day": date(2025, 1, 1),
            "start": time(8, 0),
            "end": time(10, 0),
            "role": "nurse",
            "required_staff": 1,
        },
        {
            "shift_id": "B",
            "day": date(2025, 1, 1),
            "start": time(9, 0),
            "end": time(11, 0),
            "role": "nurse",
            "required_staff": 1,
        },
    ])

    norm = precompute.normalize_shift_times(raw)
    conflicts = precompute.conflict_pairs_for_rest(norm, min_rest_hours=2)

    ids = set(tuple(sorted((row.shift_id_from, row.shift_id_to))) for row in conflicts.itertuples())
    assert ("A", "A") not in ids
    assert ("B", "B") not in ids
    assert len(ids) == 1
    assert ("A", "B") in ids
