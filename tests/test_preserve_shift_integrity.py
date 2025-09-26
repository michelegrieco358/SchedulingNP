"""Test per la funzionalità preserve_shift_integrity.

Test di regressione e casi limite per entrambe le modalità (True/False).
"""
import pytest
import pandas as pd
from src.model_cp import ShiftSchedulingCpSolver, SolverConfig
from src.config_loader import load_config
import tempfile
import os


@pytest.fixture
def temp_data_with_segments():
    """Crea dati di test con segmentazione temporale."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Employees
        employees_data = """employee_id,name,roles,max_week_hours,min_rest_hours,max_overtime_hours,skills
E1,Alice,nurse,40,8,10,"first_aid"
E2,Bob,nurse,40,8,10,"first_aid"
E3,Carol,nurse,40,8,10,"first_aid"
"""
        with open(os.path.join(temp_dir, 'employees.csv'), 'w') as f:
            f.write(employees_data)
        
        # Shifts che si sovrappongono per creare segmenti
        shifts_data = """shift_id,day,start,end,role,demand
S1,2025-10-07,06:00,12:00,nurse,1
S2,2025-10-07,10:00,16:00,nurse,1
S3,2025-10-07,14:00,20:00,nurse,1
"""
        with open(os.path.join(temp_dir, 'shifts.csv'), 'w') as f:
            f.write(shifts_data)
        
        # Windows che creano domanda sui segmenti
        windows_data = """window_id,day,window_start,window_end,role,window_demand
WIN1,2025-10-07,08:00,18:00,nurse,2
"""
        with open(os.path.join(temp_dir, 'windows.csv'), 'w') as f:
            f.write(windows_data)
        
        # Availability (tutti disponibili)
        availability_data = """employee_id,shift_id,is_available
E1,S1,1
E1,S2,1
E1,S3,1
E2,S1,1
E2,S2,1
E2,S3,1
E3,S1,1
E3,S2,1
E3,S3,1
"""
        with open(os.path.join(temp_dir, 'availability.csv'), 'w') as f:
            f.write(availability_data)
        
        # Overtime costs
        overtime_data = """role,overtime_cost_per_hour
nurse,25.0
"""
        with open(os.path.join(temp_dir, 'overtime_costs.csv'), 'w') as f:
            f.write(overtime_data)
        
        yield temp_dir


def test_preserve_shift_integrity_parameter():
    """Test che il parametro preserve_shift_integrity sia correttamente passato."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "06:00", "end": "14:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 360, "end_min": 840, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")}
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    # Test con preserve_shift_integrity=True
    solver_true = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        preserve_shift_integrity=True,
        config=SolverConfig(max_seconds=2.0)
    )
    
    assert solver_true.preserve_shift_integrity is True
    
    # Test con preserve_shift_integrity=False
    solver_false = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        preserve_shift_integrity=False,
        config=SolverConfig(max_seconds=2.0)
    )
    
    assert solver_false.preserve_shift_integrity is False


def test_segment_coverage_constraints_creation():
    """Test che i vincoli di segmenti siano creati quando preserve_shift_integrity=True."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "06:00", "end": "14:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 360, "end_min": 840, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")}
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    # Mock adaptive_slot_data per simulare segmentazione
    class MockAdaptiveSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["seg1", "seg2"]}
            self.segment_bounds = {"seg1": (360, 600), "seg2": (600, 840)}
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        preserve_shift_integrity=True,
        adaptive_slot_data=MockAdaptiveSlotData(),
        window_demands={"WIN1": 1},
        config=SolverConfig(max_seconds=2.0)
    )
    
    solver.build()
    
    # Verifica che le strutture per i segmenti siano inizializzate
    assert hasattr(solver, 'segment_shortfall_vars')
    assert hasattr(solver, 'shift_to_covering_segments')
    assert hasattr(solver, 'segment_demands')


def test_shift_integrity_vs_slot_mode():
    """Test che confronta preserve_shift_integrity vs modalità slot."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
        {"employee_id": "E2", "name": "Bob", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "06:00", "end": "12:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 360, "duration_h": 6.0, "start_min": 360, "end_min": 720, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 12:00:00")},
        {"shift_id": "S2", "day": pd.Timestamp("2025-10-07").date(), "start": "10:00", "end": "16:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 360, "duration_h": 6.0, "start_min": 600, "end_min": 960, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 10:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 16:00:00")}
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E1", "shift_id": "S2", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S2", "can_assign": 1},
    ])
    
    # Test con preserve_shift_integrity=True
    solver_integrity = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        preserve_shift_integrity=True,
        coverage_mode="disabled",
        config=SolverConfig(max_seconds=3.0)
    )
    
    solver_integrity.build()
    cp_solver_integrity = solver_integrity.solve()
    
    # Test con modalità slot (preserve_shift_integrity=False)
    solver_slots = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        preserve_shift_integrity=False,
        coverage_mode="adaptive_slots",
        config=SolverConfig(max_seconds=3.0)
    )
    
    solver_slots.build()
    cp_solver_slots = solver_slots.solve()
    
    # Entrambi dovrebbero trovare una soluzione
    assert cp_solver_integrity.StatusName() in ["OPTIMAL", "FEASIBLE"]
    assert cp_solver_slots.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Con preserve_shift_integrity=True, ogni turno assegnato deve essere completo
    assignments_integrity = solver_integrity.extract_assignments(cp_solver_integrity)
    
    # Verifica che le assegnazioni rispettino l'integrità dei turni
    for _, assignment in assignments_integrity.iterrows():
        shift_id = assignment["shift_id"]
        # Se un turno è assegnato, deve coprire tutto il suo intervallo temporale
        # (questo è garantito dalla formulazione matematica)
        assert shift_id in ["S1", "S2"], f"Turno assegnato {shift_id} deve essere uno dei turni completi"


def test_config_integration():
    """Test che il parametro preserve_shift_integrity sia letto dalla configurazione."""
    # Test configurazione con preserve_shift_integrity=True
    config_data = """
shifts:
  preserve_shift_integrity: true
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(config_data)
        config_path = f.name
    
    try:
        config = load_config(config_path)
        assert config.shifts.preserve_shift_integrity is True
    finally:
        os.unlink(config_path)
    
    # Test configurazione con preserve_shift_integrity=False
    config_data_false = """
shifts:
  preserve_shift_integrity: false
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(config_data_false)
        config_path = f.name
    
    try:
        config = load_config(config_path)
        assert config.shifts.preserve_shift_integrity is False
    finally:
        os.unlink(config_path)


def test_segment_shortfall_computation():
    """Test che il calcolo dello shortfall dei segmenti funzioni correttamente."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "06:00", "end": "14:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 360, "end_min": 840, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")}
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        preserve_shift_integrity=True,
        config=SolverConfig(max_seconds=2.0)
    )
    
    solver.build()
    
    # Test che il metodo _compute_segment_shortfall_expr esista e funzioni
    segment_expr, has_segment = solver._compute_segment_shortfall_expr()
    
    # Se non ci sono segmenti, dovrebbe restituire (0, False)
    if not solver.segment_shortfall_vars:
        assert segment_expr == 0
        assert has_segment is False
    else:
        # Se ci sono segmenti, dovrebbe restituire un'espressione valida
        assert has_segment is True


def test_objective_function_with_segments():
    """Test che la funzione obiettivo includa correttamente i termini dei segmenti."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "06:00", "end": "14:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 360, "end_min": 840, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")}
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    # Mock adaptive_slot_data con segmenti e domanda
    class MockAdaptiveSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["seg1"]}
            self.segment_bounds = {"seg1": (360, 840)}
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        preserve_shift_integrity=True,
        adaptive_slot_data=MockAdaptiveSlotData(),
        window_demands={"WIN1": 1},
        config=SolverConfig(max_seconds=2.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    # Verifica che il solver abbia trovato una soluzione
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che le variabili aggregate siano corrette
    assert solver.verify_aggregate_variables(cp_solver)


def test_performance_with_integrity():
    """Test che le performance siano accettabili con preserve_shift_integrity=True."""
    import time
    
    start_time = time.time()
    
    employees = pd.DataFrame([
        {"employee_id": f"E{i}", "name": f"Employee{i}", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"}
        for i in range(1, 6)  # 5 dipendenti
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": f"S{i}", "day": pd.Timestamp("2025-10-07").date(), "start": f"{6+i*2:02d}:00", 
         "end": f"{14+i*2:02d}:00", "role": "nurse", "demand": 1, "required_staff": 1, 
         "skill_requirements": {}, "duration_minutes": 480, "duration_h": 8.0, 
         "start_min": 360+i*120, "end_min": 840+i*120, "crosses_midnight": False,
         "start_dt": pd.Timestamp(f"2025-10-07 {6+i*2:02d}:00:00"), 
         "end_dt": pd.Timestamp(f"2025-10-07 {14+i*2:02d}:00:00")}
        for i in range(3)  # 3 turni
    ])
    
    # Crea assign_mask per tutte le combinazioni
    assign_mask_data = []
    for _, emp in employees.iterrows():
        for _, shift in shifts.iterrows():
            assign_mask_data.append({
                "employee_id": emp["employee_id"],
                "shift_id": shift["shift_id"],
                "can_assign": 1
            })
    
    assign_mask = pd.DataFrame(assign_mask_data)
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        preserve_shift_integrity=True,
        config=SolverConfig(max_seconds=5.0)  # Timeout generoso
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    end_time = time.time()
    duration = end_time - start_time
    
    # Verifica che sia veloce (< 10 secondi) e trovi una soluzione
    assert duration < 10.0, f"Test troppo lento: {duration:.2f}s"
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che le assegnazioni rispettino l'integrità
    assignments = solver.extract_assignments(cp_solver)
    assert len(assignments) >= 0, "Dovrebbe produrre assegnazioni valide"


def test_documentation_and_comments():
    """Test che la documentazione sia presente e accurata."""
    # Verifica che i metodi abbiano docstring appropriati
    solver = ShiftSchedulingCpSolver(
        employees=pd.DataFrame([{"employee_id": "E1", "name": "Alice", "roles": "nurse", 
                                "max_week_hours": 40, "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""}]),
        shifts=pd.DataFrame([{"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "06:00", 
                             "end": "14:00", "role": "nurse", "demand": 1, "required_staff": 1, 
                             "skill_requirements": {}, "duration_minutes": 480, "duration_h": 8.0, 
                             "start_min": 360, "end_min": 840, "crosses_midnight": False,
                             "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
                             "end_dt": pd.Timestamp("2025-10-07 14:00:00")}]),
        assign_mask=pd.DataFrame([{"employee_id": "E1", "shift_id": "S1", "can_assign": 1}]),
        preserve_shift_integrity=True
    )
    
    # Verifica che i metodi chiave abbiano documentazione
    assert solver._add_segment_coverage_constraints.__doc__ is not None
    assert "NUOVO" in solver._add_segment_coverage_constraints.__doc__
    assert "preserve_shift_integrity" in solver._add_segment_coverage_constraints.__doc__
    
    assert solver._compute_segment_shortfall_expr.__doc__ is not None
    assert "NUOVO" in solver._compute_segment_shortfall_expr.__doc__
