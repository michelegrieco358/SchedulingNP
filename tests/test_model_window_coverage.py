import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import model_cp  # noqa: E402
import precompute  # noqa: E402


def _build_employees(ids):
    rows = []
    for emp_id in ids:
        rows.append(
            {
                "employee_id": emp_id,
                "name": emp_id,
                "roles": "front",
                "max_week_hours": 40,
                "min_rest_hours": 0,
                "max_overtime_hours": 0,
            }
        )
    return pd.DataFrame(rows)


def _build_shifts(shift_ids, demand_id, demand_values=None):
    rows = []
    for idx, shift_id in enumerate(shift_ids, start=1):
        rows.append(
            {
                "shift_id": shift_id,
                "day": pd.Timestamp("2025-01-01").date(),
                "start": pd.Timestamp("2025-01-01 09:00").time(),
                "end": pd.Timestamp("2025-01-01 13:00").time(),
                "role": "front",
                "required_staff": 1,
                "demand": 0 if demand_values is None else demand_values[idx - 1],
                "demand_id": demand_id,
                "skill_requirements": {},
            }
        )
    shifts = pd.DataFrame(rows)
    shifts_norm = precompute.normalize_shift_times(shifts)
    shifts_norm["skill_requirements"] = [{} for _ in shift_ids]
    return shifts_norm


def _build_assign_mask(employee_ids, shift_ids):
    rows = []
    for emp_id in employee_ids:
        for shift_id in shift_ids:
            rows.append(
                {
                    "employee_id": emp_id,
                    "shift_id": shift_id,
                    "can_assign": 1,
                    "qual_ok": 1,
                    "is_available": 1,
                }
            )
    return pd.DataFrame(rows)


def _make_solver(
    employees,
    shifts_norm,
    assign_mask,
    window_demand_map,
    window_shifts,
    shift_soft_demand,
):
    penalties = {
        "unmet_window": 12,
        "unmet_demand": 10,
        "unmet_skill": 8,
        "unmet_shift": 8,
        "overtime": 5,
        "preferences": 2,
        "fairness": 3,
    }
    priority = list(model_cp.DEFAULT_OBJECTIVE_PRIORITY)
    objective_weights = model_cp._build_objective_weights(priority, penalties)
    cfg = model_cp.SolverConfig(
        max_seconds=None,
        log_search_progress=False,
        global_min_rest_hours=0,
        overtime_priority=objective_weights.get("overtime", 0),
        shortfall_priority=objective_weights.get("unmet_demand", 0),
        window_shortfall_priority=objective_weights.get("unmet_window", 0),
        skill_shortfall_priority=objective_weights.get("unmet_skill", 0),
        shift_shortfall_priority=objective_weights.get("unmet_shift", 0),
        preferences_weight=objective_weights.get("preferences", 0),
        fairness_weight=objective_weights.get("fairness", 0),
        skills_slack_enabled=True,
        objective_priority=tuple(priority),
    )
    preferences = pd.DataFrame(columns=["employee_id", "shift_id", "score"])
    emp_skills = {emp: set() for emp in employees["employee_id"]}
    shift_skill_req = {shift_id: {} for shift_id in shifts_norm["shift_id"]}
    solver = model_cp.ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts_norm,
        assign_mask=assign_mask,
        rest_conflicts=None,
        overtime_costs=None,
        preferences=preferences,
        emp_skills=emp_skills,
        shift_skill_requirements=shift_skill_req,
        window_demands=window_demand_map,
        window_shifts=window_shifts,
        shift_soft_demands=shift_soft_demand,
        config=cfg,
        objective_priority=priority,
        objective_weights=objective_weights,
        # Modalità unica segmenti (preserve_shift_integrity rimosso)
    )
    solver.build()
    cp_solver = solver.solve()
    return solver, cp_solver


def test_window_coverage_met_when_capable():
    shift_ids = ["S1", "S2", "S3"]
    employees = _build_employees(["E1", "E2", "E3"])
    shifts_norm = _build_shifts(shift_ids, demand_id="W1")
    assign_mask = _build_assign_mask(employees["employee_id"], shift_ids)
    window_demands = {"W1": 3}
    window_shifts = {"W1": shift_ids}
    shift_soft = {}

    solver, cp_solver = _make_solver(employees, shifts_norm, assign_mask, window_demands, window_shifts, shift_soft)

    assert cp_solver.StatusName() == "OPTIMAL"
    # Nella modalità unica segmenti, verifica che non ci sia shortfall nei segmenti
    if hasattr(solver, 'segment_shortfall_vars') and solver.segment_shortfall_vars:
        total_segment_shortfall = sum(cp_solver.Value(var) for var in solver.segment_shortfall_vars.values())
        assert total_segment_shortfall == 0
    assert all(cp_solver.Value(var) == 0 for var in solver.shortfall_vars.values())
    total_assigned = sum(cp_solver.Value(var) for var in solver.assignment_vars.values())
    assert total_assigned == 3
    assert solver.objective_weights["unmet_window"] > solver.objective_weights["unmet_shift"]


def test_window_shortfall_records_deficit():
    shift_ids = ["S1", "S2", "S3"]
    employees = _build_employees(["E1", "E2"])
    shifts_norm = _build_shifts(shift_ids, demand_id="W1")
    assign_mask = _build_assign_mask(employees["employee_id"], shift_ids)
    window_demands = {"W1": 3}
    window_shifts = {"W1": shift_ids}
    shift_soft = {}

    solver, cp_solver = _make_solver(employees, shifts_norm, assign_mask, window_demands, window_shifts, shift_soft)

    assert cp_solver.StatusName() == "OPTIMAL"
    # Nella modalità unica segmenti, verifica che ci sia shortfall nei segmenti
    if hasattr(solver, 'segment_shortfall_vars') and solver.segment_shortfall_vars:
        total_segment_shortfall = sum(cp_solver.Value(var) for var in solver.segment_shortfall_vars.values())
        assert total_segment_shortfall >= 1
    assert sum(cp_solver.Value(var) for var in solver.shortfall_vars.values()) == 1


def test_shift_soft_demand_creates_penalized_slack():
    shift_ids = ["S1"]
    employees = _build_employees(["E1"])
    shifts_norm = _build_shifts(shift_ids, demand_id="")
    shifts_norm.loc[0, "demand"] = 2
    assign_mask = _build_assign_mask(employees["employee_id"], shift_ids)
    window_demands = {}
    window_shifts = {}
    shift_soft = {"S1": 2}

    solver, cp_solver = _make_solver(employees, shifts_norm, assign_mask, window_demands, window_shifts, shift_soft)

    assert cp_solver.StatusName() == "OPTIMAL"
    assert cp_solver.Value(solver.shift_soft_shortfall_vars["S1"]) == 1
    assert cp_solver.Value(solver.shortfall_vars["S1"]) == 0
