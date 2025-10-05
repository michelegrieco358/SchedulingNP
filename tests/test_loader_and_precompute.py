from __future__ import annotations

import pandas as pd

from src import loader, precompute


def test_loader_extracts_window_skills(sample_environment):
    data_dir = sample_environment.data_dir
    shifts = sample_environment.shifts

    windows_df = loader.load_windows(data_dir / "windows.csv", shifts)
    assert not windows_df.empty
    assert windows_df.loc[0, "skill_requirements"] == {"skillA": 1}

    adaptive = precompute.build_adaptive_slots(shifts, sample_environment.cfg, windows_df)
    adaptive, slots_in_window, _ = precompute.map_windows_to_slots(adaptive, windows_df)

    key = next(iter(slots_in_window))
    assert slots_in_window[key], "each window should reference at least one slot"
    assert adaptive.slots_by_day_role, "adaptive slots must be generated"


def test_normalize_shift_times_passthrough(sample_environment):
    shifts = sample_environment.shifts

    normalized = precompute.normalize_shift_times(shifts)

    assert normalized is not shifts
    pd.testing.assert_series_equal(normalized["start_dt"], shifts["start_dt"], check_names=False)
    pd.testing.assert_series_equal(normalized["end_dt"], shifts["end_dt"], check_names=False)
    pd.testing.assert_series_equal(normalized["duration_h"], shifts["duration_h"], check_names=False)
