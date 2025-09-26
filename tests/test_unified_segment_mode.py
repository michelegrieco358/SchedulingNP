"""Test per la modalità unica segmenti - funzionalità core del sistema refactorizzato."""
import pytest
import pandas as pd
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from src.model_cp import ShiftSchedulingCpSolver, SolverConfig
from src.config_loader import Config


@pytest.fixture
def basic_data():
    """Dati di base per test modalità unica segmenti."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
        {"employee_id": "E2", "name": "Bob", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
        {"employee_id": "E3", "name": "Carol", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "06:00", "end": "14:00", 
         "role": "nurse", "demand": 2, "required_staff": 2, "skill_requirements": {}, 
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
        {"employee_id": "E3", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E1", "shift_id": "S2", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S2", "can_assign": 1},
        {"employee_id": "E3", "shift_id": "S2", "can_assign": 1},
    ])
    
    return employees, shifts, assign_mask


def test_unified_segment_mode_basic_functionality(basic_data):
    """Test funzionalità di base della modalità unica segmenti."""
    employees, shifts, assign_mask = basic_data
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=5.0)
    )
    
    # Verifica che il solver sia inizializzato correttamente
    assert solver.demand_mode == "headcount"  # Default
    assert not hasattr(solver, 'preserve_shift_integrity')  # Parametro rimosso
    assert not hasattr(solver, 'coverage_mode')  # Parametro rimosso
    
    solver.build()
    cp_solver = solver.solve()
    
    # Verifica che la soluzione sia trovata
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che le variabili aggregate siano corrette
    assert solver.verify_aggregate_variables(cp_solver)
    
    # Verifica assegnazioni
    assignments = solver.extract_assignments(cp_solver)
    assert len(assignments) >= 2  # Almeno 2 assegnazioni per coprire required_staff


def test_unified_segment_mode_with_demand_modes(basic_data):
    """Test modalità unica segmenti con diverse modalità di domanda."""
    employees, shifts, assign_mask = basic_data
    
    # Test con demand_mode = "headcount"
    solver_headcount = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=5.0)
    )
    solver_headcount.demand_mode = "headcount"
    solver_headcount.build()
    cp_solver_headcount = solver_headcount.solve()
    
    assert cp_solver_headcount.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Test con demand_mode = "person_minutes"
    solver_person_min = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=5.0)
    )
    solver_person_min.demand_mode = "person_minutes"
    solver_person_min.build()
    cp_solver_person_min = solver_person_min.solve()
    
    assert cp_solver_person_min.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Entrambe le modalità dovrebbero funzionare
    assignments_headcount = solver_headcount.extract_assignments(cp_solver_headcount)
    assignments_person_min = solver_person_min.extract_assignments(cp_solver_person_min)
    
    assert len(assignments_headcount) >= 2
    assert len(assignments_person_min) >= 2


def test_unified_segment_mode_with_windows():
    """Test modalità unica segmenti con finestre temporali."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""},
        {"employee_id": "E2", "name": "Bob", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "06:00", "end": "14:00", 
         "role": "nurse", "demand": 0, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 360, "end_min": 840, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 14:00:00"), "demand_id": "W1"},
        {"shift_id": "S2", "day": pd.Timestamp("2025-10-07").date(), "start": "14:00", "end": "22:00", 
         "role": "nurse", "demand": 0, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 840, "end_min": 1320, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 14:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 22:00:00"), "demand_id": "W1"},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E1", "shift_id": "S2", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S2", "can_assign": 1},
    ])
    
    # Definisci finestre temporali
    window_demands = {"W1": 2}
    window_shifts = {"W1": ["S1", "S2"]}
    window_duration_minutes = {"W1": 960}  # 16 ore totali
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        window_demands=window_demands,
        window_shifts=window_shifts,
        window_duration_minutes=window_duration_minutes,
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che le finestre siano gestite correttamente
    assignments = solver.extract_assignments(cp_solver)
    assert len(assignments) >= 1  # Almeno un'assegnazione


def test_unified_segment_mode_with_skills():
    """Test modalità unica segmenti con requisiti di skill."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid,cpr"},
        {"employee_id": "E2", "name": "Bob", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "cpr"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "06:00", "end": "14:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {"first_aid": 1}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 360, "end_min": 840, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S1", "can_assign": 1},
    ])
    
    emp_skills = {
        "E1": {"first_aid", "cpr"},
        "E2": {"cpr"}
    }
    
    shift_skill_requirements = {
        "S1": {"first_aid": 1}
    }
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        emp_skills=emp_skills,
        shift_skill_requirements=shift_skill_requirements,
        config=SolverConfig(max_seconds=5.0, skills_slack_enabled=True)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica skill coverage
    skill_coverage = solver.extract_skill_coverage_summary(cp_solver)
    if not skill_coverage.empty:
        # Se ci sono requisiti di skill, verifica che siano gestiti
        first_aid_rows = skill_coverage[skill_coverage["skill"] == "first_aid"]
        if not first_aid_rows.empty:
            # Verifica che E1 (che ha first_aid) sia assegnato
            assignments = solver.extract_assignments(cp_solver)
            e1_assignments = assignments[assignments["employee_id"] == "E1"]
            assert len(e1_assignments) >= 1


def test_unified_segment_mode_performance():
    """Test performance della modalità unica segmenti."""
    # Crea un problema più grande per testare le performance
    employees = pd.DataFrame([
        {"employee_id": f"E{i}", "name": f"Employee{i}", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""}
        for i in range(1, 11)  # 10 dipendenti
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": f"S{i}", "day": pd.Timestamp("2025-10-07").date(), 
         "start": f"{6 + i}:00", "end": f"{14 + i}:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, 
         "start_min": 360 + i * 60, "end_min": 840 + i * 60, 
         "crosses_midnight": False, 
         "start_dt": pd.Timestamp(f"2025-10-07 {6 + i}:00:00"), 
         "end_dt": pd.Timestamp(f"2025-10-07 {14 + i}:00:00")}
        for i in range(5)  # 5 turni
    ])
    
    # Crea assign_mask per tutti i dipendenti e turni
    assign_mask_data = []
    for emp_id in employees["employee_id"]:
        for shift_id in shifts["shift_id"]:
            assign_mask_data.append({
                "employee_id": emp_id,
                "shift_id": shift_id,
                "can_assign": 1
            })
    assign_mask = pd.DataFrame(assign_mask_data)
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=10.0)  # Limite di tempo ragionevole
    )
    
    import time
    start_time = time.time()
    
    solver.build()
    cp_solver = solver.solve()
    
    end_time = time.time()
    solve_time = end_time - start_time
    
    # Verifica che la soluzione sia trovata in tempo ragionevole
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    assert solve_time < 15.0  # Dovrebbe risolvere in meno di 15 secondi
    
    # Verifica che le assegnazioni siano ragionevoli
    assignments = solver.extract_assignments(cp_solver)
    assert len(assignments) >= 5  # Almeno un'assegnazione per turno


def test_unified_segment_mode_config_integration():
    """Test integrazione con configurazione per modalità unica segmenti."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "06:00", "end": "14:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 360, "end_min": 840, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    # Test con configurazione default
    config = Config()
    
    # Verifica che la configurazione non abbia più parametri obsoleti
    assert not hasattr(config.shifts, 'preserve_shift_integrity')
    assert not hasattr(config, 'windows')
    
    # Verifica che abbia il nuovo parametro demand_mode
    assert hasattr(config.shifts, 'demand_mode')
    assert config.shifts.demand_mode in ["headcount", "person_minutes"]
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=5.0)
    )
    
    # Imposta demand_mode dal config
    solver.demand_mode = config.shifts.demand_mode
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che il solver usi la modalità corretta
    assert solver.demand_mode == config.shifts.demand_mode
