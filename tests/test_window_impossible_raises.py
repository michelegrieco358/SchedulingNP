"""Test per finestre senza copertura potenziale che dovrebbero generare errori."""
import pytest
import pandas as pd
from src.model_cp import ShiftSchedulingCpSolver, SolverConfig


def test_window_no_covering_shifts_with_slack():
    """Test finestra senza turni che la coprono - con slack dovrebbe funzionare."""
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
    
    # Finestra che NON è coperta da nessun turno
    window_demands = {"WIN_UNCOVERED": 1}
    slots_in_window = {"WIN_UNCOVERED": ["SLOT_IMPOSSIBLE"]}
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"]}
            # Nessun segmento copre SLOT_IMPOSSIBLE
            self.cover_segment = {("SEG1", "SLOT_IMPOSSIBLE"): 0}
            self.slot_bounds = {"SLOT_IMPOSSIBLE": (900, 1020)}  # 15:00-17:00
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=MockSlotData(),
        slots_in_window=slots_in_window,
        window_demands=window_demands,
        coverage_mode="adaptive_slots",
        enable_slot_slack=True,  # Con slack dovrebbe funzionare
        preserve_shift_integrity=False,  # Usa modalità slot per questo test
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    # Dovrebbe trovare soluzione (costosa) grazie al slack
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che ci sia una variabile di slack per lo slot impossibile
    assert len(solver.slot_shortfall_vars) > 0
    assert ("WIN_UNCOVERED", "SLOT_IMPOSSIBLE") in solver.slot_shortfall_vars
    
    # Verifica che lo slot non abbia turni che lo coprono
    assert solver.slot_to_covering_shifts["SLOT_IMPOSSIBLE"] == []


def test_window_no_covering_shifts_hard_constraint():
    """Test finestra senza turni che la coprono - senza slack potrebbe essere infeasible."""
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
    
    window_demands = {"WIN_IMPOSSIBLE": 1}
    slots_in_window = {"WIN_IMPOSSIBLE": ["SLOT_IMPOSSIBLE"]}
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"]}
            self.cover_segment = {("SEG1", "SLOT_IMPOSSIBLE"): 0}
            self.slot_bounds = {"SLOT_IMPOSSIBLE": (900, 1020)}
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=MockSlotData(),
        slots_in_window=slots_in_window,
        window_demands=window_demands,
        coverage_mode="adaptive_slots",
        enable_slot_slack=False,  # Senza slack - vincolo hard
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    # Potrebbe essere infeasible, ma non dovrebbe crashare
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE", "INFEASIBLE"]
    
    # Non dovrebbero esserci variabili di slack
    assert len(solver.slot_shortfall_vars) == 0


def test_window_missing_from_demands():
    """Test finestra in slots_in_window ma non in window_demands - dovrebbe essere ignorata."""
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
    
    # Finestra in slots_in_window ma NON in window_demands
    window_demands = {}  # Vuoto!
    slots_in_window = {"WIN_ORPHAN": ["SLOT_ORPHAN"]}
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"]}
            self.cover_segment = {("SEG1", "SLOT_ORPHAN"): 1}
            self.slot_bounds = {"SLOT_ORPHAN": (420, 840)}
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=MockSlotData(),
        slots_in_window=slots_in_window,
        window_demands=window_demands,
        coverage_mode="adaptive_slots",
        enable_slot_slack=True,
        preserve_shift_integrity=False,  # Usa modalità slot per test window impossible
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    # Dovrebbe funzionare normalmente
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Non dovrebbero essere create variabili per la finestra orfana
    assert len(solver.slot_shortfall_vars) == 0


def test_window_zero_demand():
    """Test finestra con domanda zero - dovrebbe essere ignorata."""
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
    
    window_demands = {"WIN_ZERO": 0}  # Domanda zero
    slots_in_window = {"WIN_ZERO": ["SLOT_ZERO"]}
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"]}
            self.cover_segment = {("SEG1", "SLOT_ZERO"): 1}
            self.slot_bounds = {"SLOT_ZERO": (420, 840)}
    
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
    
    # Dovrebbe funzionare normalmente
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Non dovrebbero essere create variabili per finestra con domanda zero
    assert len(solver.slot_shortfall_vars) == 0


def test_adaptive_slot_data_missing():
    """Test con adaptive_slot_data mancante - dovrebbe essere gestito gracefully."""
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
    
    window_demands = {"WIN_TEST": 1}
    slots_in_window = {"WIN_TEST": ["SLOT_TEST"]}
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=None,  # Mancante!
        slots_in_window=slots_in_window,
        window_demands=window_demands,
        coverage_mode="adaptive_slots",
        enable_slot_slack=True,
        preserve_shift_integrity=False,  # Usa modalità slot per questo test
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    
    # Non dovrebbe crashare, ma non dovrebbe creare vincoli slot
    assert len(solver.slot_shortfall_vars) == 0
    assert len(solver.slot_to_covering_shifts) == 0


@pytest.mark.parametrize("missing_attr", ["segments_of_s", "cover_segment", "slot_bounds"])
def test_adaptive_slot_data_malformed(missing_attr):
    """Test con adaptive_slot_data malformato - dovrebbe essere gestito gracefully."""
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
    
    window_demands = {"WIN_TEST": 1}
    slots_in_window = {"WIN_TEST": ["SLOT_TEST"]}
    
    # Crea oggetto con attributo mancante
    class MalformedSlotData:
        def __init__(self, missing_attr):
            if missing_attr != "segments_of_s":
                self.segments_of_s = {"S1": ["SEG1"]}
            if missing_attr != "cover_segment":
                self.cover_segment = {("SEG1", "SLOT_TEST"): 1}
            if missing_attr != "slot_bounds":
                self.slot_bounds = {"SLOT_TEST": (420, 840)}
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=MalformedSlotData(missing_attr),
        slots_in_window=slots_in_window,
        window_demands=window_demands,
        coverage_mode="adaptive_slots",
        enable_slot_slack=True,
        config=SolverConfig(max_seconds=5.0)
    )
    
    # Non dovrebbe crashare durante build
    solver.build()
    
    # Potrebbe non creare vincoli slot se i dati sono malformati
    # Ma non dovrebbe crashare
    cp_solver = solver.solve()
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE", "INFEASIBLE"]
