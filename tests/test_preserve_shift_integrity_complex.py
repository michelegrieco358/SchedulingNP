"""Test per la funzionalità di preserve_shift_integrity in scenari complessi."""
import pytest
import pandas as pd
from src.model_cp import ShiftSchedulingCpSolver, SolverConfig


def test_integrity_with_overlapping_demands():
    """Test preserve_shift_integrity in uno scenario con domande sovrapposte."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
        {"employee_id": "E2", "name": "Bob", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    # Tre turni con sovrapposizione temporale
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1",
         "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"),
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
        {"shift_id": "S2", "day": "2025-10-07", "start": "10:00", "end": "18:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1",
         "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 10:00:00"),
         "end_dt": pd.Timestamp("2025-10-07 18:00:00")},
        {"shift_id": "S3", "day": "2025-10-07", "start": "14:00", "end": "22:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1",
         "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 14:00:00"),
         "end_dt": pd.Timestamp("2025-10-07 22:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": emp, "shift_id": shift, "can_assign": 1}
        for emp in ["E1", "E2"]
        for shift in ["S1", "S2", "S3"]
    ])
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {
                "S1": ["SEG1"], 
                "S2": ["SEG2"],
                "S3": ["SEG3"]
            }
            self.cover_segment = {
                ("SEG1", "SLOT1"): 1,
                ("SEG2", "SLOT2"): 1,
                ("SEG3", "SLOT3"): 1
            }
            self.segment_bounds = {
                "SEG1": (360, 840),   # 6:00-14:00
                "SEG2": (600, 1080),  # 10:00-18:00
                "SEG3": (840, 1320)   # 14:00-22:00
            }
            self.slot_bounds = {
                "SLOT1": (360, 840),
                "SLOT2": (600, 1080),
                "SLOT3": (840, 1320)
            }
    
    # Test preserve_shift_integrity=True
    solver_integrity = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=MockSlotData(),
        coverage_mode="adaptive_slots",
        enable_slot_slack=True,
        preserve_shift_integrity=True,
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver_integrity.build()
    cp_solver_integrity = solver_integrity.solve()
    
    assert cp_solver_integrity.StatusName() in ["OPTIMAL", "FEASIBLE"]
    assignments_integrity = solver_integrity.extract_assignments(cp_solver_integrity)
    
    # Con integrity=True ogni dipendente dovrebbe avere al massimo un turno completo
    employee_shifts = assignments_integrity.groupby("employee_id").size()
    for count in employee_shifts:
        assert count <= 1, "Un dipendente non può avere più di un turno con integrity=True"

    # Test preserve_shift_integrity=False
    solver_adaptive = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=MockSlotData(),
        coverage_mode="adaptive_slots",
        enable_slot_slack=True,
        preserve_shift_integrity=False,
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver_adaptive.build()
    cp_solver_adaptive = solver_adaptive.solve()
    
    assert cp_solver_adaptive.StatusName() in ["OPTIMAL", "FEASIBLE"]
    assignments_adaptive = solver_adaptive.extract_assignments(cp_solver_adaptive)
    
    # Con integrity=False un dipendente potrebbe avere più turni sovrapposti
    assert len(assignments_adaptive) >= len(assignments_integrity)


def test_integrity_with_high_demand():
    """Test preserve_shift_integrity con alta domanda di personale."""
    employees = pd.DataFrame([
        {"employee_id": f"E{i}", "name": f"Employee{i}", "roles": "nurse",
         "max_week_hours": 40, "min_rest_hours": 8, "max_overtime_hours": 10,
         "skills": "first_aid"}
        for i in range(1, 4)  # 3 dipendenti
    ])
    
    # Un turno con alta richiesta di personale
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00",
         "role": "nurse", "required_staff": 3, "demand": 3,
         "skill_requirements": "first_aid=3", "duration_h": 8.0,
         "start_dt": pd.Timestamp("2025-10-07 06:00:00"),
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")}
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": f"E{i}", "shift_id": "S1", "can_assign": 1}
        for i in range(1, 4)
    ])
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"]}
            self.cover_segment = {("SEG1", "SLOT1"): 1}
            self.segment_bounds = {"SEG1": (360, 840)}
            self.slot_bounds = {"SLOT1": (360, 840)}
    
    # Test preserve_shift_integrity=True con alta domanda
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
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    assignments = solver.extract_assignments(cp_solver)
    
    # Verifica che tutti i dipendenti siano assegnati al turno
    assert len(assignments) == 3, "Tutti i dipendenti dovrebbero essere assegnati"
    assert len(assignments["employee_id"].unique()) == 3


def test_integrity_with_skills():
    """Test preserve_shift_integrity con vincoli di skill."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse",
         "max_week_hours": 40, "min_rest_hours": 8, "max_overtime_hours": 10,
         "skills": "first_aid,advanced_care"},
        {"employee_id": "E2", "name": "Bob", "roles": "nurse",
         "max_week_hours": 40, "min_rest_hours": 8, "max_overtime_hours": 10,
         "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00",
         "role": "nurse", "required_staff": 2, "demand": 2,
         "skill_requirements": "advanced_care=1,first_aid=2", "duration_h": 8.0,
         "start_dt": pd.Timestamp("2025-10-07 06:00:00"),
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")}
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": emp, "shift_id": "S1", "can_assign": 1}
        for emp in ["E1", "E2"]
    ])
    
    emp_skills = {
        "E1": {"first_aid", "advanced_care"},
        "E2": {"first_aid"}
    }
    
    shift_skill_requirements = {
        "S1": {"advanced_care": 1, "first_aid": 2}
    }
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"]}
            self.cover_segment = {("SEG1", "SLOT1"): 1}
            self.segment_bounds = {"SEG1": (360, 840)}
            self.slot_bounds = {"SLOT1": (360, 840)}
    
    # Test con preserve_shift_integrity=True
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=MockSlotData(),
        emp_skills=emp_skills,
        shift_skill_requirements=shift_skill_requirements,
        coverage_mode="adaptive_slots",
        enable_slot_slack=True,
        preserve_shift_integrity=True,
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    assignments = solver.extract_assignments(cp_solver)
    
    # Verifica che entrambi i dipendenti siano assegnati
    assert len(assignments) == 2, "Entrambi i dipendenti dovrebbero essere assegnati"
    
    # Verifica che E1 (con advanced_care) sia assegnata
    assert "E1" in assignments["employee_id"].values, "E1 deve essere assegnata per advanced_care"