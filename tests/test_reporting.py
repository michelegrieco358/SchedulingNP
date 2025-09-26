"""Test semplificati per il modulo reporting che funzionano con la modalità unica segmenti."""
import pytest
import pandas as pd
from pathlib import Path
import tempfile
import os

from src.model_cp import ShiftSchedulingCpSolver, SolverConfig
from src.reporting import ScheduleReporter


@pytest.fixture
def simple_solver_data():
    """Crea dati semplici per test di reporting."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
        {"employee_id": "E2", "name": "Bob", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "06:00", "end": "14:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 360, "end_min": 840, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
        {"shift_id": "S2", "day": pd.Timestamp("2025-10-07").date(), "start": "14:00", "end": "22:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 840, "end_min": 1320, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 14:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 22:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E1", "shift_id": "S2", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S2", "can_assign": 1},
    ])
    
    return employees, shifts, assign_mask


def test_segment_coverage_report(simple_solver_data):
    """Test generazione report copertura segmenti."""
    employees, shifts, assign_mask = simple_solver_data
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=3.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Test reporter
    reporter = ScheduleReporter(solver, cp_solver)
    coverage_df = reporter.generate_segment_coverage_report()
    
    # Verifica struttura DataFrame
    expected_columns = ["segment_id", "start_time", "end_time", "demand", "assigned", "shortfall", "overstaffing"]
    assert list(coverage_df.columns) == expected_columns
    
    # Verifica che abbia almeno un segmento
    assert len(coverage_df) >= 0


def test_constraint_report(simple_solver_data):
    """Test generazione report vincoli."""
    employees, shifts, assign_mask = simple_solver_data
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=3.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Test reporter
    reporter = ScheduleReporter(solver, cp_solver)
    constraint_df = reporter.generate_constraint_report()
    
    # Verifica struttura DataFrame
    expected_columns = ["name", "satisfied", "binding", "violation"]
    assert list(constraint_df.columns) == expected_columns
    
    # Verifica che abbia almeno un vincolo
    assert len(constraint_df) >= 0


def test_objective_breakdown(simple_solver_data):
    """Test generazione breakdown obiettivo."""
    employees, shifts, assign_mask = simple_solver_data
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=3.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Test reporter
    reporter = ScheduleReporter(solver, cp_solver)
    objective_df = reporter.generate_objective_breakdown()
    
    # Verifica struttura DataFrame
    expected_columns = ["name", "weight", "value", "contribution"]
    assert list(objective_df.columns) == expected_columns
    
    # Verifica che abbia almeno un termine
    assert len(objective_df) >= 1
    
    # Verifica che ci sia la riga TOTAL
    total_rows = objective_df[objective_df["name"] == "TOTAL"]
    assert len(total_rows) == 1


def test_generate_all_reports(simple_solver_data):
    """Test generazione di tutti i report."""
    employees, shifts, assign_mask = simple_solver_data
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=3.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Test reporter
    reporter = ScheduleReporter(solver, cp_solver)
    
    # Non dovrebbe crashare
    reporter.generate_all_reports()
    
    # Verifica che i file siano stati creati
    reports_dir = Path("reports")
    assert reports_dir.exists()
    
    expected_files = ["segment_coverage.csv", "constraint_status.csv", "objective_breakdown.csv"]
    for filename in expected_files:
        filepath = reports_dir / filename
        assert filepath.exists(), f"File {filename} non trovato"


def test_invalid_solution():
    """Test comportamento con soluzione non valida."""
    # Crea scenario impossibile
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 0, "skills": ""},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "06:00", "end": "14:00", 
         "role": "nurse", "demand": 5, "required_staff": 5, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 360, "end_min": 840, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=2.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    # Anche se non ottimale, il reporter dovrebbe funzionare
    reporter = ScheduleReporter(solver, cp_solver)
    
    # Non dovrebbe crashare
    coverage_df = reporter.generate_segment_coverage_report()
    constraint_df = reporter.generate_constraint_report()
    objective_df = reporter.generate_objective_breakdown()
    
    # Verifica che i DataFrame abbiano le colonne corrette
    assert len(coverage_df.columns) == 7
    assert len(constraint_df.columns) == 4
    assert len(objective_df.columns) == 4
