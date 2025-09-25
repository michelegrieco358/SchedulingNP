import pandas as pd

import model_cp
import precompute


def _prepare_solver(required_skill_shortfall: bool):
    employees = pd.DataFrame(
        [
            {
                "employee_id": "E1",
                "name": "E1",
                "roles": "front",
                "max_week_hours": 40,
                "min_rest_hours": 0,
                "max_overtime_hours": 0,
            }
        ]
    )
    shifts = pd.DataFrame(
        [
            {
                "shift_id": "S1",
                "day": pd.Timestamp("2025-01-01").date(),
                "start": pd.Timestamp("2025-01-01 08:00").time(),
                "end": pd.Timestamp("2025-01-01 16:00").time(),
                "role": "front",
                "required_staff": 1,
            }
        ]
    )
    shifts_norm = precompute.normalize_shift_times(shifts)
    shifts_norm["skill_requirements"] = [{"muletto": 1}]

    assign_mask = pd.DataFrame(
        [
            {
                "employee_id": "E1",
                "shift_id": "S1",
                "can_assign": 1,
                "qual_ok": 1,
                "is_available": 1,
            }
        ]
    )

    emp_skills = {"E1": {"muletto"} if not required_skill_shortfall else set()}
    solver_cfg = model_cp.SolverConfig(skills_slack_enabled=True, max_seconds=None)

    solver = model_cp.ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts_norm,
        assign_mask=assign_mask,
        rest_conflicts=None,
        overtime_costs=None,
        preferences=pd.DataFrame(columns=["employee_id", "shift_id", "score"]),
        emp_skills=emp_skills,
        shift_skill_requirements={"S1": {"muletto": 1}},
        config=solver_cfg,
    )
    solver.build()
    cp_solver = solver.solve()
    return solver, cp_solver


def test_skill_summary_reports_shortfall():
    solver, cp_solver = _prepare_solver(required_skill_shortfall=True)
    summary = solver.extract_skill_coverage_summary(cp_solver)

    assert list(summary.columns) == ["shift_id", "skill", "required", "covered", "shortfall"]
    assert summary.loc[0, "shortfall"] == 1
    assert summary.loc[0, "covered"] == 0


def test_skill_summary_reports_coverage():
    solver, cp_solver = _prepare_solver(required_skill_shortfall=False)
    summary = solver.extract_skill_coverage_summary(cp_solver)

    assert summary.loc[0, "shortfall"] == 0
    assert summary.loc[0, "covered"] >= summary.loc[0, "required"]
