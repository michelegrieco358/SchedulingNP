"""Test per coerenza scala persona-minuti nell'obiettivo (STEP 4A)."""
import pytest
import pandas as pd
from src.model_cp import ShiftSchedulingCpSolver, SolverConfig


def test_person_minutes_conversion():
    """Test che i pesi siano convertiti correttamente da persona-ora a persona-minuto."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00", "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), "end_dt": pd.Timestamp("2025-10-07 14:00:00")}
    
    
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
    
    # Verifica conversione pesi
    weights = solver.objective_weights_minutes
    
    # Pesi di default (persona-ora) → persona-minuto
    expected_conversions = {
        "unmet_window": 2.0 / 60.0,    # 0.0333
        "unmet_demand": 1.0 / 60.0,    # 0.0167
        "unmet_skill": 0.8 / 60.0,     # 0.0133
        "unmet_shift": 0.6 / 60.0,     # 0.0100
        "overtime": 0.3 / 60.0,        # 0.0050
        "fairness": 0.05 / 60.0,       # 0.0008
        "preferences": 0.05 / 60.0,    # 0.0008
    }
    
    for component, expected_weight in expected_conversions.items():
        actual_weight = weights.get(component, 0.0)
        assert abs(actual_weight - expected_weight) < 0.0001, \
            f"{component}: atteso {expected_weight:.4f}, ottenuto {actual_weight:.4f}"


def test_slot_minutes_calculation():
    """Test che le durate slot siano calcolate correttamente."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00", "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), "end_dt": pd.Timestamp("2025-10-07 14:00:00")}
    
    
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    window_demands = {"WIN_TEST": 1}
    slots_in_window = {"WIN_TEST": ["SLOT_SHORT", "SLOT_LONG"]}
    shift_soft_demands = {}  # Aggiunto per correggere l'errore
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"]}
            self.cover_segment = {
                ("SEG1", "SLOT_SHORT"): 1,
                ("SEG1", "SLOT_LONG"): 1
            }
            self.slot_bounds = {
                "SLOT_SHORT": (360, 420),   # 06:00-07:00 = 60 minuti
                "SLOT_LONG": (420, 600)     # 07:00-10:00 = 180 minuti
            }
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=MockSlotData(),
        slots_in_window=slots_in_window,
        window_demands=window_demands,
        shift_soft_demands=shift_soft_demands,
        coverage_mode="adaptive_slots",
        enable_slot_slack=True,
        preserve_shift_integrity=False,  # Usa modalità slot per test scaling
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    
    # Verifica durate slot
    assert solver.slot_minutes["SLOT_SHORT"] == 60, "Slot corto dovrebbe essere 60 minuti"
    assert solver.slot_minutes["SLOT_LONG"] == 180, "Slot lungo dovrebbe essere 180 minuti"


def test_objective_scaling_consistency():
    """Test che l'obiettivo sia coerente nella scala persona-minuti."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00", "role": "nurse", "required_staff": 2, "demand": 2, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), "end_dt": pd.Timestamp("2025-10-07 14:00:00")},  # Richiede 2, disponibile 1
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    window_demands = {"WIN_SCALE": 2}  # Anche finestra richiede 2
    slots_in_window = {"WIN_SCALE": ["SLOT_SCALE"]}
    shift_soft_demands = {"S1": 2}
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"]}
            self.cover_segment = {("SEG1", "SLOT_SCALE"): 1}
            self.slot_bounds = {"SLOT_SCALE": (360, 840)}  # 480 minuti (8 ore)
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=MockSlotData(),
        slots_in_window=slots_in_window,
        window_demands=window_demands,
        shift_soft_demands=shift_soft_demands,
        coverage_mode="adaptive_slots",
        enable_slot_slack=True,
        preserve_shift_integrity=False,  # Usa modalità slot per test scaling
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    breakdown = solver.extract_objective_breakdown(cp_solver)
    
    # Con 1 dipendente disponibile e domande di 2:
    # - Finestra: 1 shortfall × 480 min = 480 persona-minuti
    # - Turno hard: 1 shortfall × 480 min = 480 persona-minuti  
    # - Turno soft: 1 shortfall × 480 min = 480 persona-minuti
    
    window_minutes = breakdown["unmet_window"]["minutes"]
    shift_minutes = breakdown["unmet_demand"]["minutes"]
    shift_soft_minutes = breakdown["unmet_shift"]["minutes"]
    
    # Tutti dovrebbero avere lo stesso numero di persona-minuti di shortfall
    expected_minutes = 480  # 1 persona × 480 minuti
    assert window_minutes == expected_minutes, f"Finestra: atteso {expected_minutes}, ottenuto {window_minutes}"
    assert shift_minutes == expected_minutes, f"Turno hard: atteso {expected_minutes}, ottenuto {shift_minutes}"
    assert shift_soft_minutes == expected_minutes, f"Turno soft: atteso {expected_minutes}, ottenuto {shift_soft_minutes}"
    
    # Ma i costi dovrebbero essere diversi per i pesi diversi
    window_cost = breakdown["unmet_window"]["cost"]
    shift_cost = breakdown["unmet_demand"]["cost"]
    shift_soft_cost = breakdown["unmet_shift"]["cost"]
    
    # Finestre (2.0) > turni hard (1.0) > turni soft (0.6)
    assert window_cost > shift_cost, "Costo finestre dovrebbe essere > turni hard"
    assert shift_cost > shift_soft_cost, "Costo turni hard dovrebbe essere > turni soft"


def test_mean_shift_minutes_calculation():
    """Test che mean_shift_minutes sia calcolato correttamente per preferenze."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    # Turni di durata diversa
    shifts = pd.DataFrame([
        {"shift_id": "S_SHORT", "day": "2025-10-07", "start": "06:00", "end": "10:00", "role": "nurse", "demand": 1, "skill_requirements": "first_aid=1", "required_staff": 1, "duration_h": 4.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), "end_dt": pd.Timestamp("2025-10-07 10:00:00")},  # 4 ore = 240 min
        {"shift_id": "S_LONG", "day": "2025-10-07", "start": "14:00", "end": "22:00", "role": "nurse", "demand": 1, "skill_requirements": "first_aid=1", "required_staff": 1, "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 14:00:00"), "end_dt": pd.Timestamp("2025-10-07 22:00:00")},   # 8 ore = 480 min
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S_SHORT", "can_assign": 1},
        {"employee_id": "E1", "shift_id": "S_LONG", "can_assign": 1},
    ])
    
    # Preferenze negative per testare il calcolo
    preferences = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S_SHORT", "score": -1},
        {"employee_id": "E1", "shift_id": "S_LONG", "score": -2},
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        preferences=preferences,
        coverage_mode="disabled",
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    
    # Media dovrebbe essere (240 + 480) / 2 = 360 minuti
    expected_mean = 360
    assert solver.mean_shift_minutes == expected_mean, \
        f"Mean shift minutes: atteso {expected_mean}, ottenuto {solver.mean_shift_minutes}"
    
    cp_solver = solver.solve()
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    breakdown = solver.extract_objective_breakdown(cp_solver)
    
    # Verifica che le preferenze usino la media corretta
    pref_data = breakdown["preferences"]
    assert pref_data["mean_shift_minutes"] == expected_mean


def test_overtime_minutes_scaling():
    """Test che gli straordinari siano già in minuti e non richiedano conversione."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 8,  # Basso per forzare overtime
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00", "role": "nurse", "demand": 1, "skill_requirements": "first_aid=1", "required_staff": 1, "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), "end_dt": pd.Timestamp("2025-10-07 14:00:00")},  # 8 ore = 480 min
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
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    breakdown = solver.extract_objective_breakdown(cp_solver)
    
    # Con max_week_hours=8 (480 min) e turno di 8 ore, non dovrebbe esserci overtime
    # Ma se ci fosse, dovrebbe essere già in minuti
    overtime_minutes = breakdown["overtime"]["minutes"]
    overtime_cost = breakdown["overtime"]["cost"]
    weight_per_min = breakdown["overtime"]["weight_per_min"]
    
    # Verifica coerenza: costo = minuti × peso_per_minuto
    expected_cost = overtime_minutes * weight_per_min
    assert abs(overtime_cost - expected_cost) < 0.001, \
        f"Costo overtime incoerente: {overtime_cost} vs atteso {expected_cost}"


@pytest.mark.parametrize("slot_duration,expected_cost_ratio", [
    (60, 1.0),    # Slot di 1 ora
    (120, 2.0),   # Slot di 2 ore → costo doppio
    (30, 0.5),    # Slot di 30 min → costo dimezzato
])
def test_slot_duration_cost_scaling(slot_duration, expected_cost_ratio):
    """Test parametrizzato che slot di durata diversa abbiano costi proporzionali."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00", "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), "end_dt": pd.Timestamp("2025-10-07 14:00:00")}
    
    
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    # Finestra impossibile per forzare shortfall
    window_demands = {"WIN_DURATION": 2}  # Richiede 2, disponibile 1
    slots_in_window = {"WIN_DURATION": ["SLOT_DURATION"]}
    
    class MockSlotData:
        def __init__(self, duration):
            self.segments_of_s = {"S1": ["SEG1"]}
            self.cover_segment = {("SEG1", "SLOT_DURATION"): 0}  # Nessuna copertura
            self.slot_bounds = {"SLOT_DURATION": (360, 360 + duration)}
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        adaptive_slot_data=MockSlotData(slot_duration),
        slots_in_window=slots_in_window,
        window_demands=window_demands,
        coverage_mode="adaptive_slots",
        enable_slot_slack=True,
        preserve_shift_integrity=False,  # Usa modalità slot per test scaling
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    breakdown = solver.extract_objective_breakdown(cp_solver)
    
    # Costo dovrebbe essere proporzionale alla durata
    window_cost = breakdown["unmet_window"]["cost"]
    base_cost = 2.0 / 60.0 * 60 * 2  # 2 persone shortfall  # 2 persone × 1 ora × peso_per_minuto
    expected_cost = base_cost * expected_cost_ratio
    
    assert abs(window_cost - expected_cost) < 0.001, \
        f"Costo slot {slot_duration}min: atteso {expected_cost:.3f}, ottenuto {window_cost:.3f}"
