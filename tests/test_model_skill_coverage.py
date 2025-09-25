import pandas as pd
import pytest

import model_cp
import precompute


def _build_employees(skill_map):
    rows = []
    for emp_id in skill_map:
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


def _build_shift(required_staff: int = 1, skill_req: dict | None = None, demand_id: str | None = None, demand_value: int = 0):
    shifts = pd.DataFrame(
        [
            {
                "shift_id": "S1",
                "day": pd.Timestamp("2025-01-01").date(),
                "start": pd.Timestamp("2025-01-01 08:00").time(),
                "end": pd.Timestamp("2025-01-01 16:00").time(),
                "role": "front",
                "required_staff": required_staff,
                "demand": demand_value,
                "demand_id": demand_id or "",
            }
        ]
    )
    shifts_norm = precompute.normalize_shift_times(shifts)
    shifts_norm["skill_requirements"] = [skill_req or {}]
    return shifts_norm


def _build_assign_mask(skill_map):
    rows = []
    for emp_id in skill_map:
        rows.append(
            {
                "employee_id": emp_id,
                "shift_id": "S1",
                "can_assign": 1,
                "qual_ok": 1,
                "is_available": 1,
            }
        )
    return pd.DataFrame(rows)


def _solve(skill_map, shift_req, slack_enabled, demand_id: str | None = None, window_demands=None, shift_soft=None):
    employees = _build_employees(skill_map)
    shifts_norm = _build_shift(
        required_staff=sum(shift_req.values()) if shift_req else 1,
        skill_req=shift_req,
        demand_id=demand_id,
        demand_value=shift_soft.get("S1", 0) if shift_soft else 0,
    )
    assign_mask = _build_assign_mask(skill_map)
    preferences = pd.DataFrame(columns=["employee_id", "shift_id", "score"])

    cfg = model_cp.SolverConfig(
        max_seconds=None,
        log_search_progress=False,
        skills_slack_enabled=slack_enabled,
    )

    window_demands = window_demands or {}
    if demand_id:
        window_shifts = {demand_id: ["S1"]}
    else:
        window_shifts = {wid: ["S1"] for wid in window_demands}
    shift_soft_demands = shift_soft or {}

    solver = model_cp.ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts_norm,
        assign_mask=assign_mask,
        rest_conflicts=None,
        overtime_costs=None,
        preferences=preferences,
        emp_skills={emp: set(skills) for emp, skills in skill_map.items()},
        shift_skill_requirements={"S1": shift_req},
        window_demands=window_demands,
        window_shifts=window_shifts,
        shift_soft_demands=shift_soft_demands,
        config=cfg,
    )
    solver.build()
    cp_solver = solver.solve()
    return solver, cp_solver


def test_skill_coverage_feasible_without_slack():
    skill_map = {
        "E1": {"muletto"},
        "E2": {"primo"},
    }
    shift_req = {"muletto": 1, "primo": 1}

    solver, cp_solver = _solve(skill_map, shift_req, slack_enabled=False)
    assert cp_solver.StatusName() == "OPTIMAL"

    skill_df = solver.extract_skill_coverage_summary(cp_solver)
    assert set(skill_df["skill"]) == {"muletto", "primo"}
    assert skill_df["shortfall"].sum() == 0
    assert all(skill_df["covered"] >= skill_df["required"])


def test_skill_slack_captures_missing_skills():
    skill_map = {"E1": set()}
    shift_req = {"muletto": 1}

    solver, cp_solver = _solve(skill_map, shift_req, slack_enabled=True)
    assert cp_solver.StatusName() == "OPTIMAL"

    skill_df = solver.extract_skill_coverage_summary(cp_solver)
    assert skill_df.loc[0, "shortfall"] == 1
    assert skill_df.loc[0, "covered"] == 0


def test_skill_requirement_without_slack_is_infeasible():
    skill_map = {"E1": set()}
    shift_req = {"muletto": 1}

    solver, cp_solver = _solve(skill_map, shift_req, slack_enabled=False)
    assert cp_solver.StatusName() == "INFEASIBLE"


def test_skill_requirement_respected_with_window():
    skill_map = {"E1": {"muletto"}, "E2": set()}
    shift_req = {"muletto": 1}

    solver, cp_solver = _solve(
        skill_map,
        shift_req,
        slack_enabled=False,
        demand_id="W1",
        window_demands={"W1": 1},
    )
    assert cp_solver.StatusName() == "OPTIMAL"
    assert cp_solver.Value(solver.assignment_vars[("E1", "S1")]) == 1
    assert cp_solver.Value(solver.assignment_vars[("E2", "S1")]) == 0
    assert cp_solver.Value(solver.window_shortfall_vars["W1"]) == 0

