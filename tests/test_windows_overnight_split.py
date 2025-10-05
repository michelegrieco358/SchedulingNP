from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd

from src import loader, precompute


def test_loader_splits_overnight_windows_and_maps(tmp_path):
    windows_path = tmp_path / "windows.csv"
    pd.DataFrame(
        [
            {
                "window_id": "WIN_OVN",
                "day": "2024-01-01",
                "window_start": "22:00",
                "window_end": "02:00",
                "role": "Nurse",
                "window_demand": 2,
                "skills": "",
            }
        ]
    ).to_csv(windows_path, index=False)

    windows_df = loader.load_windows(windows_path)

    assert len(windows_df) == 2
    first = windows_df.iloc[0]
    second = windows_df.iloc[1]

    assert first.window_id != second.window_id
    assert first.window_id.endswith("__D0")
    assert second.window_id.endswith("__D1")

    assert first.day == date(2024, 1, 1)
    assert second.day == date(2024, 1, 2)

    assert first.window_start_min == 1320
    assert first.window_end_min == 1440
    assert second.window_start_min == 0
    assert second.window_end_min == 120

    assert first.window_minutes == 120
    assert second.window_minutes == 120

    shifts_df = pd.DataFrame(
        [
            {
                "shift_id": "S_OVN",
                "day": date(2024, 1, 1),
                "role": "Nurse",
                "start_min": 1320,
                "end_min": 120,
            }
        ]
    )

    config = SimpleNamespace(windows=SimpleNamespace(midnight_policy="split"))

    adaptive = precompute.build_adaptive_slots(shifts_df, config, windows_df)
    adaptive, slots_in_window, _ = precompute.map_windows_to_slots(adaptive, windows_df)

    first_slots = slots_in_window[first.window_id]
    second_slots = slots_in_window[second.window_id]

    assert first_slots, "La finestra pre-midnight deve avere almeno uno slot"
    assert second_slots, "La finestra post-midnight deve avere almeno uno slot"

    assert all(adaptive.slot_bounds[slot_id] == (1320, 1440) for slot_id in first_slots)
    assert all(adaptive.slot_bounds[slot_id] == (0, 120) for slot_id in second_slots)

    day_role_first = (date(2024, 1, 1), "Nurse")
    day_role_second = (date(2024, 1, 2), "Nurse")

    assert set(first_slots) <= set(adaptive.slots_by_day_role[day_role_first])
    assert set(second_slots) <= set(adaptive.slots_by_day_role[day_role_second])
