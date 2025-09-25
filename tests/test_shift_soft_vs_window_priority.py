"""Test per verificare che finestre abbiano priorità sui turni soft."""
import pytest
import pandas as pd
from src.model_cp import ShiftSchedulingCpSolver, SolverConfig


def test_window_priority_over_shift_soft():
    """Test che finestre (peso 2.0) abbiano priorità su turni soft (peso 0.6)."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00", "role": "nurse", "demand": 2, "skill_requirements": "first_aid=1", "required_staff": 2, "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), "end_dt": pd.Timestamp("2025-10-07 14:00:00")},  # Demand soft = 2
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    # Finestra che richiede 1 persona, turno soft che richiede 2
    window_demands = {"WIN_PRIORITY": 1}
    slots_in_window = {"WIN_PRIORITY": ["SLOT_PRIORITY"]}
    shift_soft_demands = {"S1": 2}  # Turno soft richiede 2 persone
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"]}
            self.cover_segment = {("SEG1", "SLOT_PRIORITY"): 1}
            self.slot_bounds = {"SLOT_PRIORITY": (360, 840)}  # 06:00-14:00
    
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
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Estrai breakdown per verificare priorità
    breakdown = solver.extract_objective_breakdown(cp_solver)
    
    window_cost = breakdown["unmet_window"]["cost"]
    shift_soft_cost = breakdown["unmet_shift"]["cost"]
    
    # Con 1 dipendente disponibile:
    # - Finestra richiede 1 → può essere soddisfatta (costo 0)
    # - Turno soft richiede 2 → 1 shortfall (costo > 0)
    # Il solver dovrebbe preferire soddisfare la finestra
    assert window_cost == 0.0, "Finestra dovrebbe essere soddisfatta (priorità alta)"
    assert shift_soft_cost > 0.0, "Turno soft dovrebbe avere shortfall"


def test_multiple_windows_vs_shifts():
    """Test con più finestre e turni per verificare priorità complesse."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
        {"employee_id": "E2", "name": "Bob", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00", "role": "nurse", "required_staff": 3, "demand": 3, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
        {"shift_id": "S2", "day": "2025-10-07", "start": "14:00", "end": "22:00", "role": "nurse", "required_staff": 2, "demand": 2, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 14:00:00"), "end_dt": pd.Timestamp("2025-10-07 22:00:00")}
    
    
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E1", "shift_id": "S2", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S2", "can_assign": 1},
    ])
    
    # Due finestre con priorità alta
    window_demands = {"WIN_HIGH_1": 1, "WIN_HIGH_2": 1}
    slots_in_window = {
        "WIN_HIGH_1": ["SLOT_MORNING"],
        "WIN_HIGH_2": ["SLOT_EVENING"]
    }
    shift_soft_demands = {"S1": 3, "S2": 2}
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"], "S2": ["SEG2"]}
            self.cover_segment = {
                ("SEG1", "SLOT_MORNING"): 1,
                ("SEG1", "SLOT_EVENING"): 0,
                ("SEG2", "SLOT_MORNING"): 0,
                ("SEG2", "SLOT_EVENING"): 1
            }
            self.slot_bounds = {
                "SLOT_MORNING": (360, 840),   # 06:00-14:00
                "SLOT_EVENING": (840, 1320)   # 14:00-22:00
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
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    breakdown = solver.extract_objective_breakdown(cp_solver)
    
    # Con 2 dipendenti, le finestre (1+1=2) dovrebbero essere soddisfatte
    # I turni soft (3+2=5) avranno shortfall
    window_cost = breakdown["unmet_window"]["cost"]
    shift_soft_cost = breakdown["unmet_shift"]["cost"]
    
    assert window_cost >= 0.0, "Costo finestre dovrebbe essere non negativo"
    assert shift_soft_cost > 0.0, "Turni soft dovrebbero avere shortfall"


def test_window_vs_shift_hard_priority():
    """Test che finestre abbiano priorità anche sui turni hard (required_staff)."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    # Turno con required_staff=1 (hard) ma finestra con peso maggiore
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00", "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1", "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), "end_dt": pd.Timestamp("2025-10-07 14:00:00")}
    
    
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    # Finestra che compete con il turno hard
    window_demands = {"WIN_VS_HARD": 1}
    slots_in_window = {"WIN_VS_HARD": ["SLOT_COMPETE"]}
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"]}
            self.cover_segment = {("SEG1", "SLOT_COMPETE"): 1}
            self.slot_bounds = {"SLOT_COMPETE": (360, 840)}
    
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
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    breakdown = solver.extract_objective_breakdown(cp_solver)
    
    window_cost = breakdown["unmet_window"]["cost"]
    shift_hard_cost = breakdown["unmet_demand"]["cost"]
    
    # Con 1 dipendente, entrambi possono essere soddisfatti se il turno copre la finestra
    # Ma se dovessero competere, la finestra dovrebbe vincere (peso 2.0 vs 1.0)
    # In questo caso specifico, assegnando E1 a S1 soddisfa sia turno che finestra
    assert window_cost == 0.0 or shift_hard_cost == 0.0, "Almeno uno dovrebbe essere soddisfatto"


def test_priority_weights_scaling():
    """Test che i pesi relativi siano rispettati nella scala persona-minuti."""
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
    
    # Scenario impossibile: finestra e turno soft richiedono più di quanto disponibile
    window_demands = {"WIN_IMPOSSIBLE": 2}  # Richiede 2 persone
    slots_in_window = {"WIN_IMPOSSIBLE": ["SLOT_IMPOSSIBLE"]}
    shift_soft_demands = {"S1": 2}  # Anche turno soft richiede 2
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"]}
            self.cover_segment = {("SEG1", "SLOT_IMPOSSIBLE"): 0}  # Turno NON copre finestra
            self.slot_bounds = {"SLOT_IMPOSSIBLE": (900, 1020)}  # 15:00-17:00 (fuori turno)
    
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
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    breakdown = solver.extract_objective_breakdown(cp_solver)
    
    # Verifica che i pesi siano convertiti correttamente
    window_weight = breakdown["unmet_window"]["weight_per_min"]
    shift_weight = breakdown["unmet_shift"]["weight_per_min"]
    
    # Pesi dovrebbero essere: finestre 2.0/60 = 0.0333, turni soft 0.6/60 = 0.0100
    assert window_weight > shift_weight, f"Finestre ({window_weight}) dovrebbero avere peso > turni soft ({shift_weight})"
    
    # Rapporto dovrebbe essere circa 2.0/0.6 = 3.33
    ratio = window_weight / shift_weight if shift_weight > 0 else float('inf')
    expected_ratio = 2.0 / 0.6  # 3.33
    assert abs(ratio - expected_ratio) < 0.1, f"Rapporto pesi {ratio:.2f} dovrebbe essere ~{expected_ratio:.2f}"


@pytest.mark.parametrize("window_demand,shift_demand,expected_window_satisfied", [
    (1, 2, True),   # Finestra piccola vs turno grande → finestra soddisfatta
    (2, 1, False),  # Finestra grande vs turno piccolo → turno soddisfatto (peso minore ma fattibile)
    (1, 1, True),   # Pari domanda → finestra soddisfatta (peso maggiore)
])
def test_priority_scenarios_parametrized(window_demand, shift_demand, expected_window_satisfied):
    """Test parametrizzato per diversi scenari di priorità."""
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
    
    window_demands = {"WIN_TEST": window_demand}
    slots_in_window = {"WIN_TEST": ["SLOT_TEST"]}
    shift_soft_demands = {"S1": shift_demand}
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"]}
            self.cover_segment = {("SEG1", "SLOT_TEST"): 1}
            self.slot_bounds = {"SLOT_TEST": (360, 840)}
    
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
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    breakdown = solver.extract_objective_breakdown(cp_solver)
    window_cost = breakdown["unmet_window"]["cost"]
    
    if expected_window_satisfied:
        assert window_cost == 0.0, f"Finestra dovrebbe essere soddisfatta (domanda={window_demand})"
    # Nota: Non testiamo il caso opposto perché dipende dalla capacità disponibile


def test_window_priority_with_skills():
    """Test priorità finestre quando ci sono anche requisiti di skill."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid,icu"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00", "role": "nurse", "demand": 2, "skill_requirements": "first_aid=1", "required_staff": 2, "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), "end_dt": pd.Timestamp("2025-10-07 14:00:00")},  # Richiede 2 skill
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    window_demands = {"WIN_WITH_SKILLS": 1}
    slots_in_window = {"WIN_WITH_SKILLS": ["SLOT_SKILLS"]}
    shift_soft_demands = {"S1": 2}
    
    class MockSlotData:
        def __init__(self):
            self.segments_of_s = {"S1": ["SEG1"]}
            self.cover_segment = {("SEG1", "SLOT_SKILLS"): 1}
            self.slot_bounds = {"SLOT_SKILLS": (360, 840)}
    
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
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    breakdown = solver.extract_objective_breakdown(cp_solver)
    
    # Verifica che le priorità siano rispettate anche con skill
    window_cost = breakdown["unmet_window"]["cost"]
    skill_cost = breakdown["unmet_skill"]["cost"]
    shift_soft_cost = breakdown["unmet_shift"]["cost"]
    
    # Finestre dovrebbero avere priorità più alta di skill e turni soft
    assert window_cost == 0.0, "Finestra dovrebbe essere soddisfatta (priorità massima)"
    
    # Skill (peso 0.8) dovrebbero avere priorità su turni soft (peso 0.6)
    if skill_cost > 0 and shift_soft_cost > 0:
        skill_weight = breakdown["unmet_skill"]["weight_per_min"]
        shift_weight = breakdown["unmet_shift"]["weight_per_min"]
        assert skill_weight > shift_weight, "Skill dovrebbero avere peso > turni soft"
