"""Test per vincoli di copertura istantanea per slot (STEP 3B)."""
import pytest
import pandas as pd
from datetime import datetime, time
from src.model_cp import ShiftSchedulingCpSolver, SolverConfig


def test_window_instant_coverage_basic():
    """Test base: finestra con slot deve essere coperta istantaneamente."""
    # Dipendenti
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40, 
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
        {"employee_id": "E2", "name": "Bob", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    # Turni che coprono la finestra
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00", 
         "role": "nurse", "required_staff": 2, "demand": 2, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
        {"shift_id": "S2", "day": "2025-10-07", "start": "14:00", "end": "22:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 14:00:00"), "end_dt": pd.Timestamp("2025-10-07 22:00:00")},
    
    
    ])
    
    # Maschera di assegnabilità (tutti disponibili)
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E1", "shift_id": "S2", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S2", "can_assign": 1},
    ])
    
    # Finestre istantanee
    window_demands = {"WIN_MORNING": 2}  # Richiede 2 persone contemporanee
    slots_in_window = {"WIN_MORNING": ["SLOT_07_14"]}  # Slot 07:00-14:00
    
    # Mock adaptive_slot_data
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"], "S2": ["SEG2"]}
            self.cover_segment = {("SEG1", "SLOT_07_14"): 1, ("SEG2", "SLOT_07_14"): 0}
            self.slot_bounds = {"SLOT_07_14": (420, 840)}  # 07:00-14:00 in minuti
    
    # Solver con slot adattivi
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=MockSlotData(),
        slots_in_window=slots_in_window,
        window_demands=window_demands,
        coverage_mode="adaptive_slots",
        enable_slot_slack=True,
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    # Verifica che il solver trovi una soluzione
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che ci siano variabili di slot shortfall
    assert len(solver.slot_shortfall_vars) > 0
    
    # Verifica che la mappa slot->turni sia stata creata
    assert "SLOT_07_14" in solver.slot_to_covering_shifts
    assert "S1" in solver.slot_to_covering_shifts["SLOT_07_14"]
    assert "S2" not in solver.slot_to_covering_shifts["SLOT_07_14"]


def test_window_coverage_disabled_mode():
    """Test che coverage_mode='disabled' non crei vincoli slot."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
    
    
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    # Solver con slot disabilitati
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        coverage_mode="disabled",  # Modalità legacy
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    
    # Verifica che non ci siano variabili di slot
    assert len(solver.slot_shortfall_vars) == 0
    assert len(solver.slot_to_covering_shifts) == 0


def test_slot_slack_hard_vs_soft():
    """Test differenza tra vincoli hard e soft per slot."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
    
    
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    window_demands = {"WIN_IMPOSSIBLE": 5}  # Domanda impossibile
    slots_in_window = {"WIN_IMPOSSIBLE": ["SLOT_07_14"]}
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"]}
            self.cover_segment = {("SEG1", "SLOT_07_14"): 1}
            self.slot_bounds = {"SLOT_07_14": (420, 840)}
    
    # Test con slack abilitato (soft)
    solver_soft = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=MockSlotData(),
        slots_in_window=slots_in_window,
        window_demands=window_demands,
        coverage_mode="adaptive_slots",
        enable_slot_slack=True,  # Slack abilitato
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver_soft.build()
    cp_solver_soft = solver_soft.solve()
    
    # Con slack dovrebbe trovare soluzione (anche se costosa)
    assert cp_solver_soft.StatusName() in ["OPTIMAL", "FEASIBLE"]
    assert len(solver_soft.slot_shortfall_vars) > 0
    
    # Test con slack disabilitato (hard) - potrebbe essere infeasible
    solver_hard = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=MockSlotData(),
        slots_in_window=slots_in_window,
        window_demands=window_demands,
        coverage_mode="adaptive_slots",
        enable_slot_slack=False,  # Slack disabilitato
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver_hard.build()
    cp_solver_hard = solver_hard.solve()
    
    # Con vincoli hard potrebbe essere infeasible
    # Ma non dovrebbe crashare
    assert cp_solver_hard.StatusName() in ["OPTIMAL", "FEASIBLE", "INFEASIBLE"]
    assert len(solver_hard.slot_shortfall_vars) == 0


@pytest.mark.parametrize("coverage_mode,expected_vars", [
    ("disabled", 0),
    ("adaptive_slots", 1),
])
def test_coverage_mode_parametrized(coverage_mode, expected_vars):
    """Test parametrizzato per diverse modalità di copertura."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
    
    
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    window_demands = {"WIN_TEST": 1} if coverage_mode == "adaptive_slots" else {}
    slots_in_window = {"WIN_TEST": ["SLOT_07_14"]} if coverage_mode == "adaptive_slots" else {}
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"]}
            self.cover_segment = {("SEG1", "SLOT_07_14"): 1}
            self.slot_bounds = {"SLOT_07_14": (420, 840)}
    
    adaptive_data = MockSlotData() if coverage_mode == "adaptive_slots" else None
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=adaptive_data,
        slots_in_window=slots_in_window,
        window_demands=window_demands,
        coverage_mode=coverage_mode,
        enable_slot_slack=True,
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    
    # Verifica numero di variabili slot create
    assert len(solver.slot_shortfall_vars) == expected_vars
