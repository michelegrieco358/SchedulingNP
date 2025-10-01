from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

import pytest

from src import config_loader, loader, model_cp


_EMPLOYEES_HEADER = [
    "employee_id",
    "name",
    "roles",
    "max_week_hours",
    "min_rest_hours",
    "max_overtime_hours",
    "contracted_hours",
    "min_week_hours",
    "skills",
]



def _write_csv(path: Path, rows: Iterable[Iterable[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        for row in rows:
            writer.writerow(row)


@pytest.fixture()
def sample_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"

    _write_csv(
        data_dir / "employees.csv",
        [
            _EMPLOYEES_HEADER,
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
        [
            ["employee_id", "shift_id", "is_available"],
            ["E1", "S1", "1"],
        ],
    )

    _write_csv(
        data_dir / "windows.csv",
        [
            ["window_id", "day", "window_start", "window_end", "role", "window_demand", "skills"],
            ["W1", "2024-01-01", "08:00", "16:00", "Nurse", "1", "skillA:1"],
        ],
    )

    _write_csv(
        data_dir / "overtime_costs.csv",
        [
            ["role", "overtime_cost_per_hour"],
            ["Nurse", "20"],
        ],
    )

    return data_dir


@pytest.fixture()
def config_obj() -> config_loader.Config:
    return config_loader.Config()



def build_solver_from_data(data_dir: Path, cfg: config_loader.Config) -> SimpleNamespace:
    (
        employees,
        shifts_norm,
        availability,
        assign_mask,
        rest_conflicts,
        overtime_costs,
        preferences,
        emp_skills,
        shift_skill_req,
        window_demand_map,
        window_shifts,
        window_duration_map,
        shift_soft_demand,
        window_skill_req,
        adaptive_data,
    ) = model_cp._load_data(data_dir, cfg.rest.min_between_shifts, cfg)

    penalties = {
        "unmet_window": cfg.penalties.unmet_window,
        "unmet_demand": cfg.penalties.unmet_demand,
        "unmet_skill": cfg.penalties.unmet_skill,
        "unmet_shift": cfg.penalties.unmet_shift,
        "overstaff": cfg.penalties.overstaff,
        "overtime": cfg.penalties.overtime,
        "preferences": cfg.penalties.preferences,
        "fairness": cfg.penalties.fairness,
    }
    objective_priority = list(cfg.objective.priority)
    objective_weights = model_cp._build_objective_weights(objective_priority, penalties)

    solver_cfg = model_cp.SolverConfig(
        max_seconds=10.0,
        log_search_progress=False,
        global_min_rest_hours=cfg.rest.min_between_shifts,
        overtime_priority=objective_weights.get("overtime", 0),
        shortfall_priority=objective_weights.get("unmet_demand", 0),
        window_shortfall_priority=objective_weights.get("unmet_window", 0),
        skill_shortfall_priority=objective_weights.get("unmet_skill", 0),
        shift_shortfall_priority=objective_weights.get("unmet_shift", 0),
        preferences_weight=objective_weights.get("preferences", 0),
        fairness_weight=objective_weights.get("fairness", 0),
        default_overtime_cost_weight=objective_weights.get("overtime", 0),
        global_overtime_cap_minutes=None,
        random_seed=cfg.random.seed,
        mip_gap=cfg.solver.mip_gap,
        skills_slack_enabled=cfg.skills.enable_slack,
        objective_priority=tuple(objective_priority),
    )

    solver = model_cp.ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts_norm,
        assign_mask=assign_mask,
        rest_conflicts=rest_conflicts,
        overtime_costs=overtime_costs,
        preferences=preferences,
        emp_skills=emp_skills,
        shift_skill_requirements=shift_skill_req,
        window_skill_requirements=window_skill_req,
        window_demands=window_demand_map,
        window_shifts=window_shifts,
        window_duration_minutes=window_duration_map,
        config=solver_cfg,
        objective_priority=objective_priority,
        objective_weights=objective_weights,
        adaptive_slot_data=adaptive_data,
    )

    solver.demand_mode = cfg.shifts.demand_mode
    solver.build()
    cp_solver = solver.solve()

    return SimpleNamespace(
        data_dir=data_dir,
        cfg=cfg,
        solver_cfg=solver_cfg,
        solver=solver,
        cp_solver=cp_solver,
        employees=employees,
        shifts=shifts_norm,
        availability=availability,
        assign_mask=assign_mask,
        rest_conflicts=rest_conflicts,
        overtime_costs=overtime_costs,
        preferences=preferences,
        emp_skills=emp_skills,
        shift_skill_req=shift_skill_req,
        window_demand_map=window_demand_map,
        window_shifts=window_shifts,
        window_duration_map=window_duration_map,
        shift_soft_demand=shift_soft_demand,
        window_skill_req=window_skill_req,
        adaptive_data=adaptive_data,
    )


@pytest.fixture()
def sample_environment(sample_data_dir: Path, config_obj: config_loader.Config) -> SimpleNamespace:
    return build_solver_from_data(sample_data_dir, config_obj)
