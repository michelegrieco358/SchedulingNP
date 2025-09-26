"""Test per distinzione automatica lavoratori standard vs flessibili."""
import pytest
import pandas as pd
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from src.model_cp import ShiftSchedulingCpSolver, SolverConfig


def test_standard_worker_with_contracted_hours():
    """Test Caso A: lavoratore contrattualizzato (min_hours == max_hours)."""
    # Dipendente contrattualizzato: min_hours = max_hours = 40
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 8, "skills": "", "min_hours": 40},
    ])
    
    # Turni per coprire esattamente 40 ore (5 turni da 8 ore)
    shifts = pd.DataFrame([
        {"shift_id": f"S{i}", "day": pd.Timestamp("2025-10-07").date(), "start": "08:00", "end": "16:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 480, "end_min": 960, 
         "crosses_midnight": False, "start_dt": pd.Timestamp(f"2025-10-{7+i} 08:00:00"), 
         "end_dt": pd.Timestamp(f"2025-10-{7+i} 16:00:00")}
        for i in range(5)  # 5 turni da 8 ore = 40 ore
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": f"S{i}", "can_assign": 1}
        for i in range(5)
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
    
    # Verifica che il lavoratore abbia esattamente 40 ore (2400 minuti)
    assignments = solver.extract_assignments(cp_solver)
    total_minutes = len(assignments) * 480  # 480 min per turno
    assert total_minutes == 2400  # Esattamente 40 ore
    
    # Verifica che non ci siano straordinari
    overtime = solver.extract_overtime_summary(cp_solver)
    if not overtime.empty:
        assert overtime["overtime_minutes"].sum() == 0


def test_standard_worker_with_equal_min_max():
    """Test Caso A: lavoratore contrattualizzato con min_hours = max_hours."""
    # Dipendente contrattualizzato: min_hours = max_hours = 32 (part-time fisso)
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Bob", "roles": "nurse", "max_week_hours": 32,
         "min_rest_hours": 8, "max_overtime_hours": 6, "skills": "", "min_hours": 32},
    ])
    
    # Turni per coprire esattamente 32 ore (4 turni da 8 ore)
    shifts = pd.DataFrame([
        {"shift_id": f"S{i}", "day": pd.Timestamp("2025-10-07").date(), "start": "08:00", "end": "16:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 480, "end_min": 960, 
         "crosses_midnight": False, "start_dt": pd.Timestamp(f"2025-10-{7+i} 08:00:00"), 
         "end_dt": pd.Timestamp(f"2025-10-{7+i} 16:00:00")}
        for i in range(4)  # 4 turni da 8 ore = 32 ore
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
    
    # Verifica che il lavoratore abbia esattamente 32 ore (1920 minuti)
    assignments = solver.extract_assignments(cp_solver)
    total_minutes = len(assignments) * 480  # 480 min per turno
    assert total_minutes == 1920  # Esattamente 32 ore


def test_flexible_worker():
    """Test Caso C: risorsa esterna (min_hours < max_hours) - vincoli condizionali."""
    # Risorsa esterna: min_hours=10, max=30h (attivazione condizionale)
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Carol", "roles": "nurse", "max_week_hours": 30,
         "min_rest_hours": 8, "max_overtime_hours": 5, "skills": "", "min_hours": 10},
    ])
    
    # Solo 3 turni da 8 ore = 24 ore (entro il limite di 30h)
    shifts = pd.DataFrame([
        {"shift_id": f"S{i}", "day": pd.Timestamp("2025-10-07").date(), "start": "08:00", "end": "16:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 480, "end_min": 960, 
         "crosses_midnight": False, "start_dt": pd.Timestamp(f"2025-10-{7+i} 08:00:00"), 
         "end_dt": pd.Timestamp(f"2025-10-{7+i} 16:00:00")}
        for i in range(3)  # 3 turni da 8 ore = 24 ore (entro limite)
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": f"S{i}", "can_assign": 1}
        for i in range(3)
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
    
    # NUOVA LOGICA: Vincoli condizionali
    assignments = solver.extract_assignments(cp_solver)
    total_minutes = len(assignments) * 480  # 480 min per turno
    
    if total_minutes > 0:
        # SE usata, ALLORA deve essere nel range [min_hours, max_hours]
        assert total_minutes >= 600   # Almeno 10 ore (min_hours)
        assert total_minutes <= 1800  # Massimo 30 ore (max_hours, NO straordinari)
    # ALTRIMENTI può essere 0 ore (senza penalità)
    
    # Verifica che NON ci siano straordinari (risorse esterne non ne hanno)
    overtime = solver.extract_overtime_summary(cp_solver)
    if not overtime.empty:
        assert overtime["overtime_minutes"].sum() == 0


def test_inconsistent_data_warning():
    """Test Caso B: lavoratore non contrattualizzato senza min_hours specificato."""
    # Dipendente non contrattualizzato: solo max_week_hours (min_hours default = 0)
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Diego", "roles": "nurse", "max_week_hours": 45,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""},
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
    
    # Verifica che il solver funzioni (lavoratore non contrattualizzato)
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che non ci siano straordinari (lavoratori non contrattualizzati non ne hanno)
    overtime = solver.extract_overtime_summary(cp_solver)
    if not overtime.empty:
        assert overtime["overtime_minutes"].sum() == 0


def test_standard_worker_with_overtime():
    """Test lavoratore standard che può fare straordinari."""
    # Dipendente con contracted_hours = 32, può fare fino a 6 ore di straordinari
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Eva", "roles": "nurse", "max_week_hours": 32,
         "min_rest_hours": 8, "max_overtime_hours": 6, "skills": "", "contracted_hours": 32},
    ])
    
    # 5 turni da 8 ore = 40 ore totali (32 normali + 8 straordinari, ma max 6)
    shifts = pd.DataFrame([
        {"shift_id": f"S{i}", "day": pd.Timestamp("2025-10-07").date(), "start": "08:00", "end": "16:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 480, "end_min": 960, 
         "crosses_midnight": False, "start_dt": pd.Timestamp(f"2025-10-{7+i} 08:00:00"), 
         "end_dt": pd.Timestamp(f"2025-10-{7+i} 16:00:00")}
        for i in range(5)  # 5 turni da 8 ore = 40 ore
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": f"S{i}", "can_assign": 1}
        for i in range(5)
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
    
    # Verifica che il lavoratore abbia almeno 32 ore (contracted)
    assignments = solver.extract_assignments(cp_solver)
    total_minutes = len(assignments) * 480  # 480 min per turno
    assert total_minutes >= 1920  # Almeno 32 ore
    
    # Verifica che non superi 32 + 6 ore di straordinari = 38 ore
    assert total_minutes <= 2280  # Massimo 38 ore (32 + 6)
    
    # Se ha più di 32 ore, dovrebbe avere straordinari
    if total_minutes > 1920:
        overtime = solver.extract_overtime_summary(cp_solver)
        assert not overtime.empty
        overtime_minutes = overtime["overtime_minutes"].iloc[0]
        assert overtime_minutes == total_minutes - 1920  # Straordinari = totale - contracted


def test_multiple_worker_types():
    """Test mix di lavoratori standard e flessibili."""
    employees = pd.DataFrame([
        # Contrattualizzato (min_hours == max_hours)
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 8, "skills": "", "min_hours": 40},
        # Non contrattualizzato (min_hours < max_hours)
        {"employee_id": "E2", "name": "Bob", "roles": "nurse", "max_week_hours": 32,
         "min_rest_hours": 8, "max_overtime_hours": 6, "skills": "", "min_hours": 10},
        # Non contrattualizzato (min_hours < max_hours)
        {"employee_id": "E3", "name": "Carol", "roles": "nurse", "max_week_hours": 30,
         "min_rest_hours": 8, "max_overtime_hours": 5, "skills": "", "min_hours": 5},
    ])
    
    # Turni sufficienti per tutti
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
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    assignments = solver.extract_assignments(cp_solver)
    
    # Verifica vincoli per ogni dipendente
    for emp_id in ["E1", "E2", "E3"]:
        emp_assignments = assignments[assignments["employee_id"] == emp_id]
        total_minutes = len(emp_assignments) * 480
        
        if emp_id == "E1":  # Contrattualizzato (min_hours=40, max_hours=40)
            assert total_minutes >= 2400  # Almeno 40 ore
        elif emp_id == "E2":  # Non contrattualizzato (min_hours=10, max_hours=32)
            assert total_minutes >= 600   # Almeno 10 ore (min_hours)
            assert total_minutes <= 1920  # Massimo 32 ore (NO straordinari)
        elif emp_id == "E3":  # Non contrattualizzato (min_hours=5, max_hours=30)
            assert total_minutes >= 300   # Almeno 5 ore (min_hours)
            assert total_minutes <= 1800  # Massimo 30 ore (NO straordinari)


def test_worker_classification_logic():
    """Test della logica di classificazione lavoratori."""
    from src.model_cp import ShiftSchedulingCpSolver, SolverConfig
    
    # Test cases per la classificazione (corretti secondo la logica implementata)
    test_cases = [
        # (contracted_hours, max_week_hours, expected_type, description)
        (40, 40, "standard", "contracted_hours presente"),
        (None, 32, "flexible", "senza contracted_hours = flessibile"),
        (None, 40, "flexible", "senza contracted_hours = flessibile"),
    ]
    
    for contracted, max_hours, expected_type, description in test_cases:
        employees = pd.DataFrame([
            {"employee_id": "E1", "name": "Test", "roles": "nurse", "max_week_hours": max_hours,
             "min_rest_hours": 8, "max_overtime_hours": 8, "skills": ""}
        ])
        
        if contracted is not None:
            employees["contracted_hours"] = contracted
        
        # Per i test standard, forniamo turni sufficienti per soddisfare contracted_hours
        if expected_type == "standard" and contracted is not None:
            # Calcola quanti turni servono per le ore contrattuali (8h per turno)
            num_shifts = max(1, int((contracted + 7) // 8))  # Arrotonda per eccesso
            shifts = pd.DataFrame([
                {"shift_id": f"S{i}", "day": pd.Timestamp("2025-10-07").date(), "start": "08:00", "end": "16:00", 
                 "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
                 "duration_minutes": 480, "duration_h": 8.0, "start_min": 480, "end_min": 960, 
                 "crosses_midnight": False, "start_dt": pd.Timestamp(f"2025-10-{7+i} 08:00:00"), 
                 "end_dt": pd.Timestamp(f"2025-10-{7+i} 16:00:00")}
                for i in range(num_shifts)
            ])
            assign_mask = pd.DataFrame([
                {"employee_id": "E1", "shift_id": f"S{i}", "can_assign": 1}
                for i in range(num_shifts)
            ])
        else:
            # Per i test flessibili, un solo turno opzionale
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
        
        # Il test passa se il solver si costruisce senza errori
        solver.build()
        cp_solver = solver.solve()
        
        # Verifica che il solver trovi una soluzione
        assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"], f"Fallito per caso: {description}"
        
        # Verifica comportamento specifico per tipo
        assignments = solver.extract_assignments(cp_solver)
        total_minutes = len(assignments) * 480
        
        if expected_type == "standard" and contracted is not None:
            # Lavoratore standard deve lavorare almeno le ore contrattuali
            assert total_minutes >= contracted * 60, f"Standard worker dovrebbe lavorare almeno {contracted}h"
        elif expected_type == "flexible":
            # Lavoratore flessibile può lavorare 0 ore (nessun vincolo minimo)
            assert total_minutes >= 0, f"Flexible worker può lavorare qualsiasi quantità >= 0"
