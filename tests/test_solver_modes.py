from __future__ import annotations

from pathlib import Path

from tests.conftest import _write_csv, build_solver_from_data
from src import config_loader


def _build_dataset(base_dir: Path) -> Path:
    data_dir = base_dir / "data"
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
            ["S1", "2024-01-01", "08:00", "12:00", "Nurse", "1", "W1"],
            ["S2", "2024-01-01", "12:00", "16:00", "Nurse", "1", "W2"],
        ],
    )

    _write_csv(
        data_dir / "availability.csv",
        [
            ["employee_id", "shift_id", "is_available"],
            ["E1", "S1", "1"],
            ["E1", "S2", "1"],
        ],
    )

    _write_csv(
        data_dir / "windows.csv",
        [
            ["window_id", "day", "window_start", "window_end", "role", "window_demand", "skills"],
            ["W1", "2024-01-01", "08:00", "12:00", "Nurse", "1", "skillA:1"],
            ["W2", "2024-01-01", "12:00", "16:00", "Nurse", "1", "skillA:1"],
        ],
    )

    _write_csv(
        data_dir / "overtime_costs.csv",
        [["role", "overtime_cost_per_hour"], ["Nurse", "15"]],
    )

    return data_dir


def test_solver_segment_demand_headcount_vs_person_minutes(tmp_path: Path) -> None:
    data_dir = _build_dataset(tmp_path)

    head_cfg = config_loader.Config()
    person_cfg = config_loader.Config()
    person_cfg.shifts.demand_mode = "person_minutes"

    head_env = build_solver_from_data(data_dir, head_cfg)
    person_env = build_solver_from_data(data_dir, person_cfg)

    assert head_env.solver.demand_mode == "headcount"
    assert person_env.solver.demand_mode == "person_minutes"
    assert head_env.cp_solver.StatusName() == "OPTIMAL"
    assert person_env.cp_solver.StatusName() == "OPTIMAL"

    head_assign = head_env.solver.extract_assignments(head_env.cp_solver)
    person_assign = person_env.solver.extract_assignments(person_env.cp_solver)

    assert list(head_assign.columns) == list(person_assign.columns)
    assert head_assign.values.tolist() == person_assign.values.tolist()


def test_solver_objective_priority_respected(tmp_path: Path) -> None:
    data_dir = _build_dataset(tmp_path)

    cfg = config_loader.Config()
    cfg.objective.priority = ["fairness", "unmet_window", "overtime"]

    env = build_solver_from_data(data_dir, cfg)
    assert env.solver.config.objective_priority == tuple(cfg.objective.priority)
    assert list(env.solver.objective_priority) == cfg.objective.priority

    breakdown = env.solver.extract_objective_breakdown(env.cp_solver)
    assert set(cfg.objective.priority).issubset(breakdown.keys())
    assert env.solver.objective_priority[0] == "fairness"


def test_coverage_source_shifts_uses_shift_requirement(tmp_path: Path) -> None:
    data_dir = _build_dataset(tmp_path)

    cfg = config_loader.Config()
    cfg.shifts.coverage_source = "shifts"

    env = build_solver_from_data(data_dir, cfg)

    assert env.solver.using_window_demands is False
    assert not env.solver.window_demands
    assert env.solver.shortfall_vars
    assert env.adaptive_data is None

