"""Test per la funzionalità di reporting diagnostico."""
import pytest
import pandas as pd
import tempfile
from pathlib import Path
from ortools.sat.python import cp_model
from src.model_cp import ShiftSchedulingCpSolver, SolverConfig
from src.reporting import ScheduleReporter


def test_segment_coverage_report():
    """Test che il report di copertura dei segmenti sia generato correttamente."""
    # Setup dati base
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00", 
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1",
         "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")}
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])

    # Test con preserve_shift_integrity = True e adaptive_slots
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"]}
            self.cover_segment = {("SEG1", "SLOT1"): 1}
            self.segment_bounds = {"SEG1": (360, 840)}
            self.slot_bounds = {"SLOT1": (360, 840)}

    model = cp_model.CpModel()
    var = model.NewIntVar(0, 1, "shortfall")
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=MockSlotData(),
        coverage_mode="adaptive_slots",
        enable_slot_slack=True,
        preserve_shift_integrity=True,
        config=SolverConfig(max_seconds=5.0)
    )

    # Aggiungi variabili di shortfall al solver
    solver.slot_shortfall_vars = {("SEG1", "SLOT1"): var}
    solver.window_demands = {"SEG1": 1}

    # Build model e risolvi
    solver.build()
    cp_solver = solver.solve()
    cp_solver = solver.solve()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # Imposta directory temporanea per i report
        reporter = ScheduleReporter(solver, cp_solver)
        reporter.output_dir = Path(temp_dir)
        
        # Genera report
        coverage_df = reporter.generate_segment_coverage_report()
        
        # Verifica contenuto report
        assert len(coverage_df) > 0
        assert "segment_id" in coverage_df.columns
        assert "demand" in coverage_df.columns
        assert "assigned" in coverage_df.columns
        assert "shortfall" in coverage_df.columns
        assert "overstaffing" in coverage_df.columns
        
        # Verifica che il file sia stato creato
        report_path = Path(temp_dir) / "segment_coverage.csv"
        assert report_path.exists()


def test_constraint_report():
    """Test che il report dei vincoli sia generato correttamente."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00", 
         "role": "nurse", "required_staff": 2, "demand": 2,  # Richiede 2 ma c'è solo 1 dipendente
         "skill_requirements": "first_aid=1", "duration_h": 8.0,
         "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")}
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        coverage_mode="disabled",
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        reporter = ScheduleReporter(solver, cp_solver)
        reporter.output_dir = Path(temp_dir)
        
        constraint_df = reporter.generate_constraint_report()
        
        # Verifica contenuto report
        assert len(constraint_df) > 0
        assert "name" in constraint_df.columns
        assert "satisfied" in constraint_df.columns
        assert "violation" in constraint_df.columns
        
        # Verifica che il file sia stato creato
        report_path = Path(temp_dir) / "constraint_status.csv"
        assert report_path.exists()
        
        # Dovrebbe mostrare violazione dei vincoli di copertura
        coverage_row = constraint_df[constraint_df["name"] == "coverage_constraints"].iloc[0]
        assert not coverage_row["satisfied"]
        assert coverage_row["violation"] > 0


def test_objective_breakdown():
    """Test che il breakdown dell'obiettivo sia generato correttamente."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00", 
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1",
         "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")}
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        coverage_mode="disabled",
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        reporter = ScheduleReporter(solver, cp_solver)
        reporter.output_dir = Path(temp_dir)
        
        objective_df = reporter.generate_objective_breakdown()
        
        # Verifica contenuto report
        assert len(objective_df) > 0
        assert "name" in objective_df.columns
        assert "weight" in objective_df.columns
        assert "value" in objective_df.columns
        assert "contribution" in objective_df.columns
        
        # Verifica presenza riga totale
        assert "TOTAL" in objective_df["name"].values
        
        # Verifica che il file sia stato creato
        report_path = Path(temp_dir) / "objective_breakdown.csv"
        assert report_path.exists()


def test_generate_all_reports():
    """Test che la generazione di tutti i report funzioni correttamente."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00", 
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1",
         "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")}
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        coverage_mode="disabled",
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        reporter = ScheduleReporter(solver, cp_solver)
        reporter.output_dir = Path(temp_dir)
        
        # Genera tutti i report
        reporter.generate_all_reports()
        
        # Verifica che tutti i file siano stati creati
        expected_files = [
            "segment_coverage.csv",
            "constraint_status.csv",
            "objective_breakdown.csv"
        ]
        
        for filename in expected_files:
            assert (Path(temp_dir) / filename).exists()


def test_invalid_solution():
    """Test che il reporter gestisca correttamente soluzioni non valide."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00", 
         "role": "nurse", "required_staff": 2, "demand": 2,  # Impossibile da soddisfare
         "skill_requirements": "first_aid=1", "duration_h": 8.0,
         "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")}
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        coverage_mode="disabled",
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        reporter = ScheduleReporter(solver, cp_solver)
        reporter.output_dir = Path(temp_dir)
        
        # Verifica che i report siano generati anche con soluzione non ottimale
        reporter.generate_all_reports()
        
        # Verifica che i report contengano informazioni sulle violazioni
        constraint_df = pd.read_csv(Path(temp_dir) / "constraint_status.csv")
        coverage_row = constraint_df[constraint_df["name"] == "coverage_constraints"].iloc[0]
        assert coverage_row["violation"] > 0  # Dovrebbe mostrare violazione