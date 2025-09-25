import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import config_loader  # noqa: E402
import precompute  # noqa: E402
from precompute import AdaptiveSlotData


def _make_shifts(rows):
    df = pd.DataFrame(rows)
    required_columns = {"shift_id", "day", "role", "start_min", "end_min", "crosses_midnight"}
    missing = required_columns - set(df.columns)
    if missing:
        raise AssertionError(f"Columns mancanti per test: {missing}")
    return df


def test_build_adaptive_slots_creates_expected_slots():
    day = date(2025, 1, 1)
    shifts = _make_shifts(
        [
            {
                "shift_id": "S1",
                "day": day,
                "role": "nurse",
                "start_min": 480,
                "end_min": 720,
                "crosses_midnight": False,
            },
            {
                "shift_id": "S2",
                "day": day,
                "role": "nurse",
                "start_min": 600,
                "end_min": 840,
                "crosses_midnight": False,
            },
        ]
    )
    cfg = config_loader.Config()

    result = precompute.build_adaptive_slots(shifts, cfg)
    slots = result.slots_by_day_role[(day, "nurse")]
    assert slots == [
        f"{day.isoformat()}__nurse__0480_0600",
        f"{day.isoformat()}__nurse__0600_0720",
        f"{day.isoformat()}__nurse__0720_0840",
    ]
    assert [result.slot_minutes[s] for s in slots] == [120, 120, 120]


def test_build_adaptive_slots_midnight_policy_split_vs_exclude():
    base_day = date(2025, 1, 2)
    shift_row = {
        "shift_id": "S_N",
        "day": base_day,
        "role": "triage",
        "start_min": 1320,
        "end_min": 360,
        "crosses_midnight": True,
    }
    cfg_split = config_loader.Config()
    cfg_split.windows.midnight_policy = "split"

    result_split = precompute.build_adaptive_slots(_make_shifts([shift_row]), cfg_split)
    segs_split = result_split.segments_of_s["S_N"]
    assert len(segs_split) == 2
    seg0_day, _, seg0_start, seg0_end = result_split.segment_bounds[segs_split[0]]
    seg1_day, _, seg1_start, seg1_end = result_split.segment_bounds[segs_split[1]]
    assert (seg0_day, seg0_start, seg0_end) == (base_day, 1320, 1440)
    assert (seg1_day, seg1_start, seg1_end) == (base_day + timedelta(days=1), 0, 360)

    cfg_exclude = config_loader.Config()
    cfg_exclude.windows.midnight_policy = "exclude"

    result_exclude = precompute.build_adaptive_slots(_make_shifts([shift_row]), cfg_exclude)
    segs_exclude = result_exclude.segments_of_s["S_N"]
    assert len(segs_exclude) == 1
    seg_info = result_exclude.segment_bounds[segs_exclude[0]]
    assert seg_info[0] == base_day and seg_info[2:] == (1320, 1440)
    assert base_day + timedelta(days=1) not in [key[0] for key in result_exclude.slots_by_day_role]


def test_build_adaptive_slots_cover_segment_map():
    day = date(2025, 1, 3)
    shifts = _make_shifts(
        [
            {
                "shift_id": "S_full",
                "day": day,
                "role": "doc",
                "start_min": 480,
                "end_min": 840,
                "crosses_midnight": False,
            },
            {
                "shift_id": "S_partial",
                "day": day,
                "role": "doc",
                "start_min": 600,
                "end_min": 720,
                "crosses_midnight": False,
            },
        ]
    )
    cfg = config_loader.Config()

    result = precompute.build_adaptive_slots(shifts, cfg)
    slots = result.slots_by_day_role[(day, "doc")]
    seg_full = result.segments_of_s["S_full"][0]
    seg_partial = result.segments_of_s["S_partial"][0]

    for slot_id in slots:
        assert result.cover_segment[(seg_full, slot_id)] == 1
    # il secondo segmento copre solo lo slot centrale
    coverage_partial = [result.cover_segment[(seg_partial, slot)] for slot in slots]
    assert coverage_partial == [0, 1, 0]


def test_build_adaptive_slots_thresholds(caplog):
    day = date(2025, 1, 4)
    # genera 3 slot distinti
    shifts = _make_shifts(
        [
            {"shift_id": "S1", "day": day, "role": "nurse", "start_min": 0, "end_min": 60, "crosses_midnight": False},
            {"shift_id": "S2", "day": day, "role": "nurse", "start_min": 60, "end_min": 120, "crosses_midnight": False},
            {"shift_id": "S3", "day": day, "role": "nurse", "start_min": 120, "end_min": 180, "crosses_midnight": False},
        ]
    )

    cfg_warn = config_loader.Config()
    cfg_warn.windows.warn_slots_threshold = 2
    cfg_warn.windows.hard_slots_threshold = 10

    with caplog.at_level(logging.WARNING, logger="src.precompute"):
        precompute.build_adaptive_slots(shifts, cfg_warn)
    assert any("warn_slots_threshold" in rec.message for rec in caplog.records)

    cfg_error = config_loader.Config()
    cfg_error.windows.warn_slots_threshold = 1
    cfg_error.windows.hard_slots_threshold = 2

    with pytest.raises(RuntimeError):
        precompute.build_adaptive_slots(shifts, cfg_error)

def test_map_windows_to_slots_basic_mapping():
    day = date(2025, 1, 6)
    shifts = _make_shifts(
        [
            {"shift_id": "S1", "day": day, "role": "nurse", "start_min": 480, "end_min": 600, "crosses_midnight": False},
            {"shift_id": "S2", "day": day, "role": "nurse", "start_min": 600, "end_min": 720, "crosses_midnight": False},
            {"shift_id": "S3", "day": day, "role": "nurse", "start_min": 720, "end_min": 780, "crosses_midnight": False},
        ]
    )
    cfg = config_loader.Config()
    adaptive = precompute.build_adaptive_slots(shifts, cfg)

    windows_df = pd.DataFrame(
        [
            {"window_id": "W1", "day": day, "role": "nurse", "window_start_min": 480, "window_end_min": 720, "window_demand": 2},
            {"window_id": "W2", "day": day, "role": "nurse", "window_start_min": 720, "window_end_min": 780, "window_demand": 1},
        ]
    )

    mapped, slots_in_window, signatures = precompute.map_windows_to_slots(
        adaptive,
        windows_df,
        strict=True,
        merge_signatures=False,
    )

    slots_day = mapped.slots_by_day_role[(day, "nurse")]
    assert slots_in_window["W1"] == slots_day[:2]
    assert slots_in_window["W2"] == slots_day[2:]
    assert all(signatures[s] for s in slots_day)


def test_map_windows_to_slots_strict_failure():
    day = date(2025, 1, 7)
    shifts = _make_shifts(
        [
            {"shift_id": "S_gap1", "day": day, "role": "triage", "start_min": 480, "end_min": 540, "crosses_midnight": False},
            {"shift_id": "S_gap2", "day": day, "role": "triage", "start_min": 600, "end_min": 660, "crosses_midnight": False},
        ]
    )
    cfg = config_loader.Config()
    adaptive = precompute.build_adaptive_slots(shifts, cfg)

    windows_df = pd.DataFrame(
        [
            {"window_id": "W_gap", "day": day, "role": "triage", "window_start_min": 480, "window_end_min": 660, "window_demand": 2},
        ]
    )

    with pytest.raises(RuntimeError) as exc:
        precompute.map_windows_to_slots(adaptive, windows_df, strict=True)
    assert "slot" in str(exc.value)


def test_map_windows_to_slots_merge_signatures():
    day = date(2025, 1, 8)
    slot_a = f"{day.isoformat()}__nurse__0000_0060"
    slot_b = f"{day.isoformat()}__nurse__0060_0120"
    seg_id = "S_agg__seg0"

    adaptive = AdaptiveSlotData(
        slots_by_day_role={(day, "nurse"): [slot_a, slot_b]},
        slot_minutes={slot_a: 60, slot_b: 60},
        slot_bounds={slot_a: (0, 60), slot_b: (60, 120)},
        segments_of_s={"S_agg": [seg_id]},
        segment_owner={seg_id: "S_agg"},
        segment_bounds={seg_id: (day, "nurse", 0, 120)},
        cover_segment={(seg_id, slot_a): 1, (seg_id, slot_b): 1},
    )

    windows_df = pd.DataFrame(
        [
            {"window_id": "W_merge", "day": day, "role": "nurse", "window_start_min": 0, "window_end_min": 120, "window_demand": 1},
        ]
    )

    merged, slots_in_window, signatures = precompute.map_windows_to_slots(
        adaptive,
        windows_df,
        merge_signatures=True,
    )

    slots = merged.slots_by_day_role[(day, "nurse")]
    assert len(slots) == 1
    new_slot = slots[0]
    assert merged.slot_bounds[new_slot] == (0, 120)
    assert slots_in_window["W_merge"] == [new_slot]
    assert signatures[new_slot] == frozenset({seg_id})
    assert merged.cover_segment[(seg_id, new_slot)] == 1
