from __future__ import annotations

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
