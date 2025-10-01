from __future__ import annotations

from pathlib import Path

from tests.conftest import _write_csv
from src import model_cp, config_loader


def test_time_off_blocks_assignments(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"

    _write_csv(
        data_dir / "employees.csv",
        [
            [
                "employee_id",
                "name",
                "roles",
                "max_week_hours",
                "min_rest_hours",
                "max_overtime_hours",
                "contracted_hours",
                "min_week_hours",
                "skills",
            ],
            ["E1", "Alice", "Nurse", "40", "11", "5", "", "0", "skillA"],
        ],
    )

    _write_csv(
        data_dir / "shifts.csv",
        [
            ["shift_id", "day", "start", "end", "role", "required_staff", "demand_id"],
            ["S1", "2024-01-01", "08:00", "16:00", "Nurse", "1", "W1"],
        ],
    )

    _write_csv(
        data_dir / "availability.csv",
        [["employee_id", "shift_id", "is_available"], ["E1", "S1", "1"]],
    )

    _write_csv(
        data_dir / "windows.csv",
        [
            ["window_id", "day", "window_start", "window_end", "role", "window_demand", "skills"],
            ["W1", "2024-01-01", "08:00", "16:00", "Nurse", "1", "skillA:1"],
        ],
    )

    _write_csv(
        data_dir / "time_off.csv",
        [
            ["employee_id", "day", "start_time", "end_time", "reason"],
            ["E1", "2024-01-01", "07:00", "18:00", "?"],
        ],
    )

    _write_csv(
        data_dir / "overtime_costs.csv",
        [["role", "overtime_cost_per_hour"], ["Nurse", "10"]],
    )

    cfg = config_loader.Config()
    (
        employees,
        shifts,
        availability,
        assign_mask,
        *_
    ) = model_cp._load_data(data_dir, cfg.rest.min_between_shifts, cfg)

    pair = assign_mask.loc[(assign_mask["employee_id"] == "E1") & (assign_mask["shift_id"] == "S1")]
    assert not pair.empty
    assert pair.iloc[0]["can_assign"] == 0
