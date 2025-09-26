"""Test per verificare retrocompatibilità e funzionamento della colonna contracted_hours."""
import pytest
import pandas as pd
import warnings
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from src.loader import load_employees, _normalize_contracted_hours
from src.model_cp import ShiftSchedulingCpSolver, SolverConfig


def test_normalize_contracted_hours_missing_column():
    """Test normalizzazione quando contracted_hours non è presente nel CSV."""
    # CSV senza contracted_hours
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 8, "skills": "", "min_hours": 40},
        {"employee_id": "E2", "name": "Bob", "roles": "nurse", "max_week_hours": 32,
         "min_rest_hours": 8, "max_overtime_hours": 6, "skills": "", "min_hours": 10},
    ])
    
    # Simula il comportamento del loader
    _normalize_contracted_hours(employees)
    
    # Verifica che contracted_hours sia stata aggiunta
    assert "contracted_hours" in employees.columns
    
    # E1: min_hours == max_week_hours → contracted_hours = min_hours
    assert employees.loc[0, "contracted_hours"] == 40.0
    
    # E2: min_hours != max_week_hours → contracted_hours rimane NaN
    assert pd.isna(employees.loc[1, "contracted_hours"])


def test_normalize_contracted_hours_with_incoherent_data():
    """Test warning per dati incoerenti."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 8, "skills": "", 
         "contracted_hours": 32, "min_hours": 20},  # Incoerente: contracted_hours ma min_hours != max_week_hours
    ])
    
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _normalize_contracted_hours(employees)
        
        # Verifica che sia stato emesso un warning
        assert len(w) >= 1
        assert "incoerenti" in str(w[0].message)


def test_normalize_contracted_hours_sets_min_hours():
    """Test che contracted_hours imposti min_hours se mancante."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 8, "skills": "", 
         "contracted_hours": 35},  # min_hours mancante
    ])
    
    _normalize_contracted_hours(employees)
    
    # Verifica che min_hours sia stato impostato
    assert employees.loc[0, "min_hours"] == 35.0


def test_solver_with_contracted_hours():
    """Test solver con dipendenti che hanno contracted_hours esplicitamente."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 8, "skills": "", "contracted_hours": 32},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": f"S{i}", "day": pd.Timestamp("2025-10-07").date(), "start": "08:00", "end": "16:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 480, "end_min": 960, 
         "crosses_midnight": False, "start_dt": pd.Timestamp(f"2025-10-{7+i} 08:00:00"), 
         "end_dt": pd.Timestamp(f"2025-10-{7+i} 16:00:00")}
        for i in range(4)  # 4 turni da 8 ore = 32 ore (esattamente contracted_hours)
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": f"S{i}", "can_assign": 1}
        for i in range(4)
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che il lavoratore abbia esattamente 32 ore (contracted_hours)
    assignments = solver.extract_assignments(cp_solver)
    total_minutes = len(assignments) * 480  # 480 min per turno
    assert total_minutes == 1920  # Esattamente 32 ore


def test_solver_without_contracted_hours():
    """Test solver con dipendenti senza contracted_hours (risorsa esterna)."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Bob", "roles": "nurse", "max_week_hours": 32,
         "min_rest_hours": 8, "max_overtime_hours": 6, "skills": "", "min_hours": 10},
        # Nessuna contracted_hours → risorsa esterna
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "08:00", "end": "16:00", 
         "role": "nurse", "demand": 1, "required_staff": 0, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 480, "end_min": 960, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 08:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 16:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che NON ci siano straordinari (risorse esterne non ne hanno)
    overtime = solver.extract_overtime_summary(cp_solver)
    if not overtime.empty:
        assert overtime["overtime_minutes"].sum() == 0


def test_mixed_worker_types():
    """Test mix di lavoratori con e senza contracted_hours."""
    employees = pd.DataFrame([
        # Contrattualizzato
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 8, "skills": "", "contracted_hours": 32},
        # Risorsa esterna
        {"employee_id": "E2", "name": "Bob", "roles": "nurse", "max_week_hours": 30,
         "min_rest_hours": 8, "max_overtime_hours": 5, "skills": "", "min_hours": 10},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": f"S{i}", "day": pd.Timestamp("2025-10-07").date(), "start": "08:00", "end": "16:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 480, "end_min": 960, 
         "crosses_midnight": False, "start_dt": pd.Timestamp(f"2025-10-{7+i} 08:00:00"), 
         "end_dt": pd.Timestamp(f"2025-10-{7+i} 16:00:00")}
        for i in range(6)  # 6 turni disponibili
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": emp_id, "shift_id": f"S{i}", "can_assign": 1}
        for emp_id in ["E1", "E2"]
        for i in range(6)
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=10.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    assignments = solver.extract_assignments(cp_solver)
    
    # Verifica vincoli per ogni dipendente
    for emp_id in ["E1", "E2"]:
        emp_assignments = assignments[assignments["employee_id"] == emp_id]
        total_minutes = len(emp_assignments) * 480
        
        if emp_id == "E1":  # Contrattualizzato (contracted_hours=32)
            assert total_minutes >= 1920  # Almeno 32 ore
        elif emp_id == "E2":  # Risorsa esterna (min_hours=10, max_hours=30)
            if total_minutes > 0:  # Se usata
                assert total_minutes >= 600   # Almeno 10 ore (min_hours)
                assert total_minutes <= 1800  # Massimo 30 ore (NO straordinari)


def test_retrocompatibility_csv_without_contracted_hours():
    """Test retrocompatibilità completa: CSV senza contracted_hours deve funzionare."""
    # Simula un CSV legacy senza contracted_hours
    employees_data = {
        "employee_id": ["E1", "E2", "E3"],
        "name": ["Alice", "Bob", "Carol"],
        "roles": ["nurse", "nurse", "nurse"],
        "max_week_hours": [40, 32, 30],
        "min_rest_hours": [8, 8, 8],
        "max_overtime_hours": [8, 6, 5],
        "skills": ["", "", ""],
        "min_hours": [40, 10, 30],  # E1: contrattualizzato, E2: flessibile, E3: contrattualizzato
    }
    
    employees = pd.DataFrame(employees_data)
    
    # Applica normalizzazione (simula load_employees)
    _normalize_contracted_hours(employees)
    
    # Verifica risultati normalizzazione
    assert employees.loc[0, "contracted_hours"] == 40.0  # E1: min_hours == max_week_hours
    assert pd.isna(employees.loc[1, "contracted_hours"])  # E2: min_hours != max_week_hours
    assert employees.loc[2, "contracted_hours"] == 30.0  # E3: min_hours == max_week_hours
    
    # Test che il solver funzioni con questi dati
    shifts = pd.DataFrame([
        {"shift_id": f"S{i}", "day": pd.Timestamp("2025-10-07").date(), "start": "08:00", "end": "16:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 480, "end_min": 960, 
         "crosses_midnight": False, "start_dt": pd.Timestamp(f"2025-10-{7+i} 08:00:00"), 
         "end_dt": pd.Timestamp(f"2025-10-{7+i} 16:00:00")}
        for i in range(10)  # 10 turni disponibili
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": emp_id, "shift_id": f"S{i}", "can_assign": 1}
        for emp_id in ["E1", "E2", "E3"]
        for i in range(10)
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=10.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    # Il solver deve funzionare senza errori
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che i vincoli siano applicati correttamente
    assignments = solver.extract_assignments(cp_solver)
    
    for emp_id in ["E1", "E2", "E3"]:
        emp_assignments = assignments[assignments["employee_id"] == emp_id]
        total_minutes = len(emp_assignments) * 480
        
        if emp_id == "E1":  # Contrattualizzato (contracted_hours=40)
            assert total_minutes >= 2400  # Almeno 40 ore
        elif emp_id == "E2":  # Risorsa esterna (min_hours=10, max_hours=32)
            if total_minutes > 0:  # Se usata
                assert total_minutes >= 600   # Almeno 10 ore
                assert total_minutes <= 1920  # Massimo 32 ore
        elif emp_id == "E3":  # Contrattualizzato (contracted_hours=30)
            assert total_minutes >= 1800  # Almeno 30 ore
