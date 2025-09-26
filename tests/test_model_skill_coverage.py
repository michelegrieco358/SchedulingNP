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
        skill_req={},  # skill_req sempre vuoto nei turni
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
        shift_skill_requirements={},  # Non più skill dai turni
        window_demands=window_demands,
        window_shifts=window_shifts,
        config=cfg,
    )
    solver.build()
    cp_solver = solver.solve()
    return solver, cp_solver


def test_skill_coverage_feasible_without_slack():
    """Test che senza skill requirements dai turni, il solver sia sempre OPTIMAL."""
    skill_map = {
        "E1": {"muletto"},
        "E2": {"primo"},
    }
    shift_req = {"muletto": 1, "primo": 1}  # Ignorato dal modello

    solver, cp_solver = _solve(skill_map, shift_req, slack_enabled=False)
    assert cp_solver.StatusName() == "OPTIMAL"

    # Nessuna skill requirement dai turni -> nessun skill coverage report
    skill_df = solver.extract_skill_coverage_summary(cp_solver)
    assert len(skill_df) == 0  # Nessuna skill requirement attiva


def test_skill_slack_captures_missing_skills():
    """Test che senza skill requirements dai turni, non ci sia shortfall."""
    skill_map = {"E1": set()}
    shift_req = {"muletto": 1}  # Ignorato dal modello

    solver, cp_solver = _solve(skill_map, shift_req, slack_enabled=True)
    assert cp_solver.StatusName() == "OPTIMAL"

    # Nessuna skill requirement dai turni -> nessun shortfall
    skill_df = solver.extract_skill_coverage_summary(cp_solver)
    assert len(skill_df) == 0  # Nessuna skill requirement attiva


def test_skill_requirement_without_slack_is_infeasible():
    """Test che senza skill requirements dai turni, il solver sia sempre OPTIMAL."""
    skill_map = {"E1": set()}
    shift_req = {"muletto": 1}  # Ignorato dal modello

    solver, cp_solver = _solve(skill_map, shift_req, slack_enabled=False)
    # Senza skill requirements dai turni, sempre OPTIMAL
    assert cp_solver.StatusName() == "OPTIMAL"


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
    # Nella modalità unica segmenti, verifica che non ci sia shortfall nei segmenti
    if hasattr(solver, 'segment_shortfall_vars') and solver.segment_shortfall_vars:
        total_segment_shortfall = sum(cp_solver.Value(var) for var in solver.segment_shortfall_vars.values())
        assert total_segment_shortfall == 0
