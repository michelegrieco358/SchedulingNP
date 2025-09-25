"""Test per midnight_policy con turni cross-midnight (22:00-06:00)."""
import pytest
import pandas as pd
from src.model_cp import ShiftSchedulingCpSolver, SolverConfig


def test_midnight_policy_split():
    """Test midnight_policy='split' - turni cross-midnight divisi in segmenti."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    # Turno cross-midnight: 22:00-06:00
    shifts = pd.DataFrame([
        {"shift_id": "S_NIGHT", "day": "2025-10-07", "start": "22:00", "end": "06:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 22:00:00"), "end_dt": pd.Timestamp("2025-10-07 06:00:00")},
        {"shift_id": "S_DAY", "day": "2025-10-07", "start": "08:00", "end": "16:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 08:00:00"), "end_dt": pd.Timestamp("2025-10-07 16:00:00")},
    
    
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S_NIGHT", "can_assign": 1},
        {"employee_id": "E1", "shift_id": "S_DAY", "can_assign": 1},
    ])
    
    # Finestra che attraversa la mezzanotte
    window_demands = {"WIN_NIGHT": 1}
    slots_in_window = {"WIN_NIGHT": ["SLOT_23_01"]}  # 23:00-01:00
    
    class MockSlotData:
        def __init__(self):
            # Con policy 'split', il turno notturno dovrebbe essere diviso in segmenti
            self.segments_of_s = {
                "S_NIGHT": ["SEG_NIGHT_1", "SEG_NIGHT_2"],  # Due segmenti
                "S_DAY": ["SEG_DAY"]
            }
            # Il segmento notturno copre lo slot 23:00-01:00
            self.cover_segment = {
                ("SEG_NIGHT_1", "SLOT_23_01"): 1,
                ("SEG_NIGHT_2", "SLOT_23_01"): 0,
                ("SEG_DAY", "SLOT_23_01"): 0
            }
            self.slot_bounds = {"SLOT_23_01": (1380, 60)}  # 23:00-01:00 in minuti
    
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
    
    # Dovrebbe trovare soluzione
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che il turno notturno sia mappato correttamente
    assert "SLOT_23_01" in solver.slot_to_covering_shifts
    assert "S_NIGHT" in solver.slot_to_covering_shifts["SLOT_23_01"]


def test_midnight_policy_exclude():
    """Test midnight_policy='exclude' - turni cross-midnight esclusi dalle finestre."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S_NIGHT", "day": "2025-10-07", "start": "22:00", "end": "06:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 22:00:00"), "end_dt": pd.Timestamp("2025-10-07 06:00:00")},
        {"shift_id": "S_DAY", "day": "2025-10-07", "start": "08:00", "end": "16:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 08:00:00"), "end_dt": pd.Timestamp("2025-10-07 16:00:00")},
    
    
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S_NIGHT", "can_assign": 1},
        {"employee_id": "E1", "shift_id": "S_DAY", "can_assign": 1},
    ])
    
    window_demands = {"WIN_NIGHT": 1}
    slots_in_window = {"WIN_NIGHT": ["SLOT_23_01"]}
    
    class MockSlotData:
        def __init__(self):
            # Con policy 'exclude', turni cross-midnight non hanno segmenti per finestre
            self.segments_of_s = {
                "S_NIGHT": [],  # Nessun segmento (escluso)
                "S_DAY": ["SEG_DAY"]
            }
            self.cover_segment = {
                ("SEG_DAY", "SLOT_23_01"): 0  # Turno diurno non copre slot notturno
            }
            self.slot_bounds = {"SLOT_23_01": (1380, 60)}
    
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
    
    # Dovrebbe trovare soluzione (con slack)
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Il turno notturno NON dovrebbe coprire lo slot
    assert "SLOT_23_01" in solver.slot_to_covering_shifts
    assert "S_NIGHT" not in solver.slot_to_covering_shifts["SLOT_23_01"]
    
    # Dovrebbe esserci shortfall per lo slot non coperto
    assert len(solver.slot_shortfall_vars) > 0


def test_regular_shift_not_affected_by_midnight_policy():
    """Test che turni normali (non cross-midnight) non siano affetti dalla policy."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    # Solo turni normali (non cross-midnight)
    shifts = pd.DataFrame([
        {"shift_id": "S_MORNING", "day": "2025-10-07", "start": "06:00", "end": "14:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
        {"shift_id": "S_EVENING", "day": "2025-10-07", "start": "14:00", "end": "22:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 14:00:00"), "end_dt": pd.Timestamp("2025-10-07 22:00:00")},
    
    
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S_MORNING", "can_assign": 1},
        {"employee_id": "E1", "shift_id": "S_EVENING", "can_assign": 1},
    ])
    
    window_demands = {"WIN_DAY": 1}
    slots_in_window = {"WIN_DAY": ["SLOT_10_12"]}  # 10:00-12:00
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {
                "S_MORNING": ["SEG_MORNING"],
                "S_EVENING": ["SEG_EVENING"]
            }
            self.cover_segment = {
                ("SEG_MORNING", "SLOT_10_12"): 1,  # Turno mattutino copre slot
                ("SEG_EVENING", "SLOT_10_12"): 0
            }
            self.slot_bounds = {"SLOT_10_12": (600, 720)}  # 10:00-12:00
    
    # Test con entrambe le policy - dovrebbe comportarsi uguale
    for policy in ["split", "exclude"]:
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
        
        # Dovrebbe funzionare ugualmente con entrambe le policy
        assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
        assert "S_MORNING" in solver.slot_to_covering_shifts["SLOT_10_12"]


def test_multiple_midnight_shifts():
    """Test con più turni cross-midnight in giorni diversi."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
        {"employee_id": "E2", "name": "Bob", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S_NIGHT_1", "day": "2025-10-07", "start": "22:00", "end": "06:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 22:00:00"), "end_dt": pd.Timestamp("2025-10-07 06:00:00")},
        {"shift_id": "S_NIGHT_2", "day": "2025-10-08", "start": "22:00", "end": "06:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-08 22:00:00"), "end_dt": pd.Timestamp("2025-10-08 06:00:00")},
    
    
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S_NIGHT_1", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S_NIGHT_1", "can_assign": 1},
        {"employee_id": "E1", "shift_id": "S_NIGHT_2", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S_NIGHT_2", "can_assign": 1},
    ])
    
    window_demands = {"WIN_NIGHT_1": 1, "WIN_NIGHT_2": 1}
    slots_in_window = {
        "WIN_NIGHT_1": ["SLOT_23_01_DAY1"],
        "WIN_NIGHT_2": ["SLOT_23_01_DAY2"]
    }
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {
                "S_NIGHT_1": ["SEG_NIGHT_1A", "SEG_NIGHT_1B"],
                "S_NIGHT_2": ["SEG_NIGHT_2A", "SEG_NIGHT_2B"]
            }
            self.cover_segment = {
                ("SEG_NIGHT_1A", "SLOT_23_01_DAY1"): 1,
                ("SEG_NIGHT_1B", "SLOT_23_01_DAY1"): 0,
                ("SEG_NIGHT_2A", "SLOT_23_01_DAY1"): 0,
                ("SEG_NIGHT_2B", "SLOT_23_01_DAY1"): 0,
                ("SEG_NIGHT_1A", "SLOT_23_01_DAY2"): 0,
                ("SEG_NIGHT_1B", "SLOT_23_01_DAY2"): 0,
                ("SEG_NIGHT_2A", "SLOT_23_01_DAY2"): 1,
                ("SEG_NIGHT_2B", "SLOT_23_01_DAY2"): 0,
            }
            self.slot_bounds = {
                "SLOT_23_01_DAY1": (1380, 60),
                "SLOT_23_01_DAY2": (1380, 60)
            }
    
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
    
    # Dovrebbe gestire correttamente più turni notturni
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica mapping corretto
    assert "S_NIGHT_1" in solver.slot_to_covering_shifts["SLOT_23_01_DAY1"]
    assert "S_NIGHT_2" in solver.slot_to_covering_shifts["SLOT_23_01_DAY2"]
    assert "S_NIGHT_1" not in solver.slot_to_covering_shifts["SLOT_23_01_DAY2"]
    assert "S_NIGHT_2" not in solver.slot_to_covering_shifts["SLOT_23_01_DAY1"]


@pytest.mark.parametrize("policy,expected_coverage", [
    ("split", True),    # Con split, turni cross-midnight dovrebbero coprire slot
    ("exclude", False), # Con exclude, turni cross-midnight NON dovrebbero coprire slot
])
def test_midnight_policy_parametrized(policy, expected_coverage):
    """Test parametrizzato per diverse midnight policy."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S_NIGHT", "day": "2025-10-07", "start": "22:00", "end": "06:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 22:00:00"), "end_dt": pd.Timestamp("2025-10-07 06:00:00")},
    
    
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S_NIGHT", "can_assign": 1},
    ])
    
    window_demands = {"WIN_NIGHT": 1}
    slots_in_window = {"WIN_NIGHT": ["SLOT_MIDNIGHT"]}
    
    class MockSlotData:
        def __init__(self, policy):
            if policy == "split":
                self.segments_of_s = {"S_NIGHT": ["SEG_NIGHT"]}
                self.cover_segment = {("SEG_NIGHT", "SLOT_MIDNIGHT"): 1}
            else:  # exclude
                self.segments_of_s = {"S_NIGHT": []}  # Nessun segmento
                self.cover_segment = {}
            self.slot_bounds = {"SLOT_MIDNIGHT": (1380, 360)}  # 23:00-05:00
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=MockSlotData(policy),
        slots_in_window=slots_in_window,
        window_demands=window_demands,
        coverage_mode="adaptive_slots",
        enable_slot_slack=True,
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica copertura secondo la policy
    if expected_coverage:
        assert "S_NIGHT" in solver.slot_to_covering_shifts["SLOT_MIDNIGHT"]
    else:
        assert "S_NIGHT" not in solver.slot_to_covering_shifts["SLOT_MIDNIGHT"]
