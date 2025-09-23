import sys
from pathlib import Path

import pandas as pd
from datetime import datetime, date

try:
    from ortools.sat.python import cp_model
except ModuleNotFoundError:
    import pytest
    pytest.skip("ortools non installato, salto i test del solver", allow_module_level=True)

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from model_cp import ShiftSchedulingCpSolver, SolverConfig  # noqa: E402


def test_rest_conflict_makes_model_infeasible():
    employees = pd.DataFrame([
        {
            "employee_id": "E1",
            "name": "Alice",
            "roles": "front",
            "max_week_hours": 80,
            "min_rest_hours": 0,
            "max_overtime_hours": 0,
        }
    ])

    shifts = pd.DataFrame([
        {
            "shift_id": "S1",
            "day": date(2025, 1, 1),
            "start_dt": datetime(2025, 1, 1, 8, 0),
            "end_dt": datetime(2025, 1, 1, 12, 0),
            "duration_h": 4.0,
            "role": "front",
            "required_staff": 1,
        },
        {
            "shift_id": "S2",
            "day": date(2025, 1, 2),
            "start_dt": datetime(2025, 1, 2, 0, 0),
            "end_dt": datetime(2025, 1, 2, 4, 0),
            "duration_h": 4.0,
            "role": "front",
            "required_staff": 1,
        },
    ])

    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E1", "shift_id": "S2", "can_assign": 1},
    ])

    rest_conflicts = pd.DataFrame([
        {"shift_id_from": "S1", "shift_id_to": "S2"}
    ])

    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        rest_conflicts=rest_conflicts,
        config=SolverConfig(max_seconds=5, log_search_progress=False),
    )
    solver.build()
    result = solver.solve()

    assert result.StatusName() == "INFEASIBLE"


def test_night_constraints_block_consecutive_nights():
    employees = pd.DataFrame([
        {
            "employee_id": "E1",
            "name": "Alice",
            "roles": "front",
            "max_week_hours": 80,
            "min_rest_hours": 0,
            "max_overtime_hours": 0,
        }
    ])

    shifts = pd.DataFrame([
        {
            "shift_id": "N1",
            "day": date(2025, 1, 1),
            "start_dt": datetime(2025, 1, 1, 22, 0),
            "end_dt": datetime(2025, 1, 2, 6, 0),
            "duration_h": 8.0,
            "role": "front",
            "required_staff": 1,
        },
        {
            "shift_id": "N2",
            "day": date(2025, 1, 2),
            "start_dt": datetime(2025, 1, 2, 22, 0),
            "end_dt": datetime(2025, 1, 3, 6, 0),
            "duration_h": 8.0,
            "role": "front",
            "required_staff": 1,
        },
    ])

    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "N1", "can_assign": 1},
        {"employee_id": "E1", "shift_id": "N2", "can_assign": 1},
    ])

    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        rest_conflicts=None,
        config=SolverConfig(max_seconds=5, log_search_progress=False),
    )
    solver.build()
    result = solver.solve()

    assert result.StatusName() == "INFEASIBLE"


def test_night_constraints_limit_three_per_week():
    employees = pd.DataFrame([
        {
            "employee_id": "E1",
            "name": "Alice",
            "roles": "front",
            "max_week_hours": 80,
            "min_rest_hours": 0,
            "max_overtime_hours": 0,
        }
    ])

    shifts = pd.DataFrame([
        {
            "shift_id": "N1",
            "day": date(2025, 1, 6),
            "start_dt": datetime(2025, 1, 6, 22, 0),
            "end_dt": datetime(2025, 1, 7, 6, 0),
            "duration_h": 8.0,
            "role": "front",
            "required_staff": 1,
        },
        {
            "shift_id": "N2",
            "day": date(2025, 1, 8),
            "start_dt": datetime(2025, 1, 8, 22, 0),
            "end_dt": datetime(2025, 1, 9, 6, 0),
            "duration_h": 8.0,
            "role": "front",
            "required_staff": 1,
        },
        {
            "shift_id": "N3",
            "day": date(2025, 1, 10),
            "start_dt": datetime(2025, 1, 10, 22, 0),
            "end_dt": datetime(2025, 1, 11, 6, 0),
            "duration_h": 8.0,
            "role": "front",
            "required_staff": 1,
        },
        {
            "shift_id": "N4",
            "day": date(2025, 1, 12),
            "start_dt": datetime(2025, 1, 12, 22, 0),
            "end_dt": datetime(2025, 1, 13, 6, 0),
            "duration_h": 8.0,
            "role": "front",
            "required_staff": 1,
        },
    ])

    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": sid, "can_assign": 1}
        for sid in ["N1", "N2", "N3", "N4"]
    ])

    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        rest_conflicts=None,
        config=SolverConfig(max_seconds=5, log_search_progress=False),
    )
    solver.build()
    result = solver.solve()

    assert result.StatusName() == "INFEASIBLE"


def test_overtime_soft_constraint_allows_solution():
    employees = pd.DataFrame([
        {
            "employee_id": "E1",
            "name": "Alice",
            "roles": "front",
            "max_week_hours": 16,
            "min_rest_hours": 0,
            "max_overtime_hours": 16,
        }
    ])

    shifts = pd.DataFrame([
        {
            "shift_id": f"D{i}",
            "day": date(2025, 1, i),
            "start_dt": datetime(2025, 1, i, 8, 0),
            "end_dt": datetime(2025, 1, i, 16, 0),
            "duration_h": 8.0,
            "role": "front",
            "required_staff": 1,
        }
        for i in range(1, 5)
    ])

    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": row.shift_id, "can_assign": 1}
        for row in shifts.itertuples()
    ])

    overtime_costs = pd.DataFrame([
        {"role": "front", "overtime_cost_per_hour": 50.0}
    ])

    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        rest_conflicts=None,
        overtime_costs=overtime_costs,
        config=SolverConfig(max_seconds=5, log_search_progress=False),
    )
    solver.build()
    result = solver.solve()

    assert result.StatusName() == "OPTIMAL"

    overtime_df = solver.extract_overtime_summary(result)
    assert not overtime_df.empty
    assert overtime_df.loc[0, "overtime_minutes"] == 960



def test_guardrail_warns_on_insufficient_capacity(capsys):
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
            "day": date(2025, 1, 1),
            "start_dt": datetime(2025, 1, 1, 8, 0),
            "end_dt": datetime(2025, 1, 1, 16, 0),
            "duration_h": 8.0,
            "role": "front",
            "required_staff": 2,
        }
    ])

    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])

    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        rest_conflicts=None,
        config=SolverConfig(max_seconds=1, log_search_progress=False),
    )
    solver.build()

    out = capsys.readouterr().out
    assert "capacita disponibili" in out


def test_log_employee_summary_outputs(capsys):
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
            "day": date(2025, 1, 1),
            "start_dt": datetime(2025, 1, 1, 8, 0),
            "end_dt": datetime(2025, 1, 1, 12, 0),
            "duration_h": 4.0,
            "role": "front",
            "required_staff": 1,
        }
    ])

    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])

    overtime_costs = pd.DataFrame([
        {"role": "front", "overtime_cost_per_hour": 10.0},
    ])

    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        rest_conflicts=None,
        overtime_costs=overtime_costs,
        config=SolverConfig(max_seconds=5, log_search_progress=False),
    )
    solver.build()
    result = solver.solve()

    solver.log_employee_summary(result)
    out = capsys.readouterr().out
    assert "E1" in out
    assert "Straordinario" in out or "straordinario" in out
