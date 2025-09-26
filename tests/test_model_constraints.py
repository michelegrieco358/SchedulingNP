import sys
from pathlib import Path

import pandas as pd
from datetime import datetime, date, time

try:
    from ortools.sat.python import cp_model
except ModuleNotFoundError:
    import pytest
    pytest.skip("ortools non installato, salto i test del solver", allow_module_level=True)

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import loader  # noqa: E402
import precompute  # noqa: E402

from model_cp import (
    ShiftSchedulingCpSolver,
    SolverConfig,
    DEFAULT_SHORTFALL_PRIORITY,
)  # noqa: E402


def test_rest_conflict_triggers_shortfall():
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

    assert result.StatusName() == "OPTIMAL"

    assignments_df = solver.extract_assignments(result)
    assert len(assignments_df) == 1

    shortfall_df = solver.extract_shortfall_summary(result)
    assert not shortfall_df.empty
    assert set(shortfall_df["shift_id"]).issubset({"S1", "S2"})
    assert shortfall_df["shortfall_units"].sum() == 1
    assert shortfall_df["shortfall_staff_minutes"].sum() == 240


def test_night_constraints_block_consecutive_nights_via_shortfall():
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

    assert result.StatusName() == "OPTIMAL"

    assignments_df = solver.extract_assignments(result)
    assert len(assignments_df) == 1

    shortfall_df = solver.extract_shortfall_summary(result)
    assert not shortfall_df.empty
    assert set(shortfall_df["shift_id"]) <= {"N1", "N2"}
    assert shortfall_df["shortfall_units"].sum() == 1
    assert shortfall_df["shortfall_staff_minutes"].sum() == 480


def test_night_constraints_limit_three_per_week_via_shortfall():
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

    assert result.StatusName() == "OPTIMAL"

    assignments_df = solver.extract_assignments(result)
    assert len(assignments_df) == 3

    shortfall_df = solver.extract_shortfall_summary(result)
    assert shortfall_df["shortfall_units"].sum() == 1
    assert shortfall_df["shortfall_staff_minutes"].sum() == 480


def test_overtime_soft_constraint_allows_solution():
    employees = pd.DataFrame([
        {
            "employee_id": "E1",
            "name": "Alice",
            "roles": "front",
            "max_week_hours": 16,
            "min_rest_hours": 0,
            "max_overtime_hours": 16,
            "min_hours": 16,  # Rende il lavoratore contrattualizzato (min_hours == max_hours)
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

def test_shortfall_zero_when_coverage_possible():
    employees = pd.DataFrame([
        {
            "employee_id": "E1",
            "name": "Alice",
            "roles": "front",
            "max_week_hours": 40,
            "min_rest_hours": 0,
            "max_overtime_hours": 0,
        },
        {
            "employee_id": "E2",
            "name": "Bob",
            "roles": "front",
            "max_week_hours": 40,
            "min_rest_hours": 0,
            "max_overtime_hours": 0,
        },
    ])

    shifts = pd.DataFrame([
        {
            "shift_id": "S1",
            "day": date(2025, 2, 1),
            "start_dt": datetime(2025, 2, 1, 8, 0),
            "end_dt": datetime(2025, 2, 1, 16, 0),
            "duration_h": 8.0,
            "role": "front",
            "required_staff": 1,
        },
        {
            "shift_id": "S2",
            "day": date(2025, 2, 2),
            "start_dt": datetime(2025, 2, 2, 8, 0),
            "end_dt": datetime(2025, 2, 2, 16, 0),
            "duration_h": 8.0,
            "role": "front",
            "required_staff": 1,
        },
    ])

    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S2", "can_assign": 1},
    ])

    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        rest_conflicts=None,
        overtime_costs=None,
        config=SolverConfig(max_seconds=5, log_search_progress=False),
    )
    solver.build()
    result = solver.solve()

    assert result.StatusName() == "OPTIMAL"

    shortfall_df = solver.extract_shortfall_summary(result)
    assert shortfall_df.empty

    assignments_df = solver.extract_assignments(result)
    assert set(assignments_df["shift_id"]) == {"S1", "S2"}
    assert set(assignments_df["employee_id"]) == {"E1", "E2"}


def test_shortfall_priority_increase_preserves_solution():
    employees = pd.DataFrame([
        {
            "employee_id": "E1",
            "name": "Alice",
            "roles": "front",
            "max_week_hours": 8,
            "min_rest_hours": 0,
            "max_overtime_hours": 16,
            "min_hours": 8,  # Rende il lavoratore contrattualizzato (min_hours == max_hours)
        }
    ])

    shifts = pd.DataFrame([
        {
            "shift_id": f"D{i}",
            "day": date(2025, 3, i),
            "start_dt": datetime(2025, 3, i, 8, 0),
            "end_dt": datetime(2025, 3, i, 16, 0),
            "duration_h": 8.0,
            "role": "front",
            "required_staff": 1,
        }
        for i in range(1, 4)
    ])

    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": row.shift_id, "can_assign": 1}
        for row in shifts.itertuples()
    ])

    overtime_costs = pd.DataFrame([
        {"role": "front", "overtime_cost_per_hour": 25.0}
    ])

    def solve_with_priority(priority=None):
        config_kwargs = dict(max_seconds=5, log_search_progress=False)
        if priority is not None:
            config_kwargs["shortfall_priority"] = priority
        solver = ShiftSchedulingCpSolver(
            employees=employees.copy(),
            shifts=shifts.copy(),
            assign_mask=assign_mask.copy(),
            rest_conflicts=None,
            overtime_costs=overtime_costs,
            config=SolverConfig(**config_kwargs),
        )
        solver.build()
        solver_result = solver.solve()
        assignments = solver.extract_assignments(solver_result)
        shortfall_df = solver.extract_shortfall_summary(solver_result)
        return solver_result, assignments, shortfall_df

    default_result, default_assignments, default_shortfall = solve_with_priority()
    high_priority_result, high_priority_assignments, high_priority_shortfall = solve_with_priority(10_000_000)

    assert default_result.StatusName() == "OPTIMAL"
    assert high_priority_result.StatusName() == "OPTIMAL"

    assert default_shortfall.empty
    assert high_priority_shortfall.empty

    default_pairs = {
        (row.employee_id, row.shift_id)
        for row in default_assignments.itertuples(index=False)
    }
    high_priority_pairs = {
        (row.employee_id, row.shift_id)
        for row in high_priority_assignments.itertuples(index=False)
    }

    assert default_pairs == high_priority_pairs

def test_preferences_steer_assignments():
    employees = pd.DataFrame([
        {
            "employee_id": "E1",
            "name": "Alice",
            "roles": "front",
            "max_week_hours": 40,
            "min_rest_hours": 0,
            "max_overtime_hours": 0,
        },
        {
            "employee_id": "E2",
            "name": "Bob",
            "roles": "front",
            "max_week_hours": 40,
            "min_rest_hours": 0,
            "max_overtime_hours": 0,
        },
    ])

    shifts = pd.DataFrame([
        {
            "shift_id": "S1",
            "day": date(2025, 4, 1),
            "start_dt": datetime(2025, 4, 1, 8, 0),
            "end_dt": datetime(2025, 4, 1, 16, 0),
            "duration_h": 8.0,
            "role": "front",
            "required_staff": 1,
        }
    ])

    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S1", "can_assign": 1},
    ])

    preferences = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "score": 2},
        {"employee_id": "E2", "shift_id": "S1", "score": -2},
    ])

    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        rest_conflicts=None,
        overtime_costs=None,
        preferences=preferences,
        config=SolverConfig(
            max_seconds=5,
            log_search_progress=False,
            fairness_weight=0,
            overtime_priority=0,
            preferences_weight=500,
            shortfall_priority=DEFAULT_SHORTFALL_PRIORITY,
        ),
    )

    solver.build()
    result = solver.solve()

    assert result.StatusName() == "OPTIMAL"

    assignments = solver.extract_assignments(result)
    assert set(assignments["employee_id"]) == {"E1"}
    assert set(assignments["shift_id"]) == {"S1"}

    pref_summary = solver.extract_preference_summary(result)
    assert not pref_summary.empty
    e1_row = pref_summary[pref_summary["employee_id"] == "E1"].iloc[0]
    assert e1_row["liked_assigned"] == 1
    assert e1_row["disliked_assigned"] == 0
    assert e1_row["total_score"] == 2


def test_time_off_blocks_assignments():
    employees = pd.DataFrame([
        {
            "employee_id": "E1",
            "name": "Alice",
            "roles": "front",
            "max_week_hours": 40,
            "min_rest_hours": 0,
            "max_overtime_hours": 0,
        },
        {
            "employee_id": "E2",
            "name": "Bob",
            "roles": "front",
            "max_week_hours": 40,
            "min_rest_hours": 0,
            "max_overtime_hours": 0,
        },
    ])

    shifts = pd.DataFrame([
        {
            "shift_id": "S1",
            "day": date(2025, 5, 1),
            "start": time(8, 0),
            "end": time(16, 0),
            "role": "front",
            "required_staff": 1,
        }
    ])

    availability = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "is_available": 1},
        {"employee_id": "E2", "shift_id": "S1", "is_available": 1},
    ])

    shifts_norm = precompute.normalize_shift_times(shifts)
    quali_mask = loader.build_quali_mask(employees, shifts)
    assign_mask = loader.merge_availability(quali_mask, availability)

    time_off = pd.DataFrame([
        {
            "employee_id": "E1",
            "off_start_dt": datetime(2025, 5, 1, 0, 0),
            "off_end_dt": datetime(2025, 5, 2, 0, 0),
            "reason": "ferie",
        }
    ])

    assign_mask = loader.apply_time_off(assign_mask, time_off, shifts_norm)

    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts_norm,
        assign_mask=assign_mask,
        rest_conflicts=None,
        overtime_costs=None,
        preferences=None,
        config=SolverConfig(max_seconds=5, log_search_progress=False),
    )
    solver.build()
    result = solver.solve()

    assert result.StatusName() == "OPTIMAL"

    assignments = solver.extract_assignments(result)
    assert set(assignments["employee_id"]) == {"E2"}
    assert set(assignments["shift_id"]) == {"S1"}

    e1_mask = assign_mask[(assign_mask["employee_id"] == "E1") & (assign_mask["shift_id"] == "S1")]
    assert int(e1_mask.iloc[0]["timeoff_block"]) == 1
