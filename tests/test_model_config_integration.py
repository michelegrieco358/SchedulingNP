import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import config_loader  # noqa: E402
import loader  # noqa: E402
import model_cp  # noqa: E402
import precompute  # noqa: E402


def _simple_dataset():
    employees = pd.DataFrame([
        {
            "employee_id": "E1",
            "name": "Alice",
            "roles": "front",
            "max_week_hours": 40,
            "min_rest_hours": 0,
            "max_overtime_hours": 0,
        }
    ])
    shifts = pd.DataFrame([
        {
            "shift_id": "S1",
            "day": pd.Timestamp("2025-01-01").date(),
            "start": pd.Timestamp("2025-01-01 08:00").time(),
            "end": pd.Timestamp("2025-01-01 16:00").time(),
            "role": "front",
            "required_staff": 1,
        }
    ])
    availability = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "is_available": 1},
    ])
    shifts_norm = precompute.normalize_shift_times(shifts)
    quali_mask = loader.build_quali_mask(employees, shifts)
    assign_mask = loader.merge_availability(quali_mask, availability)
    preferences = pd.DataFrame(columns=["employee_id", "shift_id", "score"])
    return employees, shifts_norm, availability, assign_mask, preferences


def test_solver_uses_time_limit_and_seed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg_path = tmp_path / "config.yaml"
    cfg_data = {
        "rest": {"min_between_shifts": 8},
        "penalties": {"unmet_window": 12, "unmet_demand": 10, "unmet_skill": 8, "unmet_shift": 8, "overtime": 5, "fairness": 3, "preferences": 2},
        "objective": {"priority": ["unmet_demand", "overtime", "preferences", "fairness"]},
        "random": {"seed": 99},
        "solver": {"time_limit_sec": 5, "mip_gap": 0.05},
        "logging": {"level": "INFO"},
    }
    import yaml
    yaml.safe_dump(cfg_data, cfg_path.open("w", encoding="utf-8"))

    cfg = config_loader.load_config(str(cfg_path))
    penalties = {
        "unmet_window": cfg.penalties.unmet_window,
        "unmet_demand": cfg.penalties.unmet_demand,
        "unmet_skill": cfg.penalties.unmet_skill,
        "unmet_shift": cfg.penalties.unmet_shift,
        "overtime": cfg.penalties.overtime,
        "fairness": cfg.penalties.fairness,
        "preferences": cfg.penalties.preferences,
    }
    objective_priority = list(cfg.objective.priority)
    objective_weights = model_cp._build_objective_weights(objective_priority, penalties)

    solver_cfg = model_cp.SolverConfig(
        max_seconds=cfg.solver.time_limit_sec,
        log_search_progress=False,
        global_min_rest_hours=cfg.rest.min_between_shifts,
        overtime_priority=objective_weights.get("overtime", 0),
        shortfall_priority=objective_weights.get("unmet_demand", 0),
        window_shortfall_priority=objective_weights.get("unmet_window", 0),
        skill_shortfall_priority=objective_weights.get("unmet_skill", 0),
        shift_shortfall_priority=objective_weights.get("unmet_shift", 0),
        preferences_weight=objective_weights.get("preferences", 0),
        fairness_weight=objective_weights.get("fairness", 0),
        default_overtime_cost_weight=model_cp.DEFAULT_OVERTIME_COST_WEIGHT,
        global_overtime_cap_minutes=None,
        random_seed=cfg.random.seed,
        mip_gap=cfg.solver.mip_gap,
        skills_slack_enabled=True,
        objective_priority=tuple(objective_priority),
    )

    employees, shifts_norm, availability, assign_mask, preferences = _simple_dataset()
    solver = model_cp.ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts_norm,
        assign_mask=assign_mask,
        rest_conflicts=None,
        overtime_costs=None,
        preferences=preferences,
        config=solver_cfg,
        objective_priority=objective_priority,
        objective_weights=objective_weights,
    )
    solver.build()
    cp_solver = solver.solve()

    assert cp_solver.parameters.max_time_in_seconds == pytest.approx(5.0)
    assert cp_solver.parameters.random_seed == 99
    assert cp_solver.parameters.relative_gap_limit == pytest.approx(0.05)


def test_objective_priority_reordered(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg_path = tmp_path / "config.yaml"
    cfg_data = {
        "penalties": {"unmet_window": 9, "unmet_demand": 8, "unmet_skill": 6, "unmet_shift": 5, "overtime": 2, "fairness": 6, "preferences": 1},
        "objective": {"priority": ["unmet_demand", "fairness", "overtime", "preferences"]},
        "logging": {"level": "INFO"},
    }
    import yaml
    yaml.safe_dump(cfg_data, cfg_path.open("w", encoding="utf-8"))

    cfg = config_loader.load_config(str(cfg_path))
    penalties = {
        "unmet_window": cfg.penalties.unmet_window,
        "unmet_demand": cfg.penalties.unmet_demand,
        "unmet_skill": cfg.penalties.unmet_skill,
        "unmet_shift": cfg.penalties.unmet_shift,
        "overtime": cfg.penalties.overtime,
        "fairness": cfg.penalties.fairness,
        "preferences": cfg.penalties.preferences,
    }
    objective_priority = list(cfg.objective.priority)
    objective_weights = model_cp._build_objective_weights(objective_priority, penalties)

    solver_cfg = model_cp.SolverConfig(
        max_seconds=None,
        log_search_progress=False,
        global_min_rest_hours=cfg.rest.min_between_shifts,
        overtime_priority=objective_weights.get("overtime", 0),
        shortfall_priority=objective_weights.get("unmet_demand", 0),
        window_shortfall_priority=objective_weights.get("unmet_window", 0),
        skill_shortfall_priority=objective_weights.get("unmet_skill", 0),
        shift_shortfall_priority=objective_weights.get("unmet_shift", 0),
        preferences_weight=objective_weights.get("preferences", 0),
        fairness_weight=objective_weights.get("fairness", 0),
        default_overtime_cost_weight=model_cp.DEFAULT_OVERTIME_COST_WEIGHT,
        global_overtime_cap_minutes=None,
        random_seed=cfg.random.seed,
        mip_gap=cfg.solver.mip_gap,
        skills_slack_enabled=True,
        objective_priority=tuple(objective_priority),
    )

    employees, shifts_norm, availability, assign_mask, preferences = _simple_dataset()
    solver = model_cp.ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts_norm,
        assign_mask=assign_mask,
        rest_conflicts=None,
        overtime_costs=None,
        preferences=preferences,
        config=solver_cfg,
        objective_priority=objective_priority,
        objective_weights=objective_weights,
    )

    assert solver.objective_priority == objective_priority
    assert solver.objective_weights["fairness"] > solver.objective_weights["overtime"]
