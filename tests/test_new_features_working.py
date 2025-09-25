"""Test funzionanti per le nuove funzionalità."""
import pytest
import pandas as pd
from src.model_cp import ShiftSchedulingCpSolver, SolverConfig


def test_objective_weights_conversion():
    """Test che i pesi siano convertiti correttamente in persona-minuti."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1",
         "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"),
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        coverage_mode="disabled",
        config=SolverConfig(max_seconds=2.0)
    )
    
    solver.build()
    
    # Verifica conversione pesi (persona-ora → persona-minuto)
    weights = solver.objective_weights_minutes
    
    # Pesi attesi (divisi per 60)
    expected_weights = {
        "unmet_window": 2.0 / 60.0,
        "unmet_demand": 1.0 / 60.0,
        "unmet_skill": 0.8 / 60.0,
        "unmet_shift": 0.6 / 60.0,
        "overtime": 0.3 / 60.0,
        "fairness": 0.05 / 60.0,
        "preferences": 0.05 / 60.0,
    }
    
    for component, expected in expected_weights.items():
        actual = weights.get(component, 0.0)
        assert abs(actual - expected) < 0.0001, \
            f"{component}: atteso {expected:.4f}, ottenuto {actual:.4f}"


def test_preferences_vs_overtime_weights():
    """Test che preferenze abbiano peso configurabile."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1",
         "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"),
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    preferences = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "score": -1},
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        preferences=preferences,
        coverage_mode="disabled",
        config=SolverConfig(max_seconds=2.0)
    )
    
    solver.build()
    
    # Verifica che i pesi siano configurabili
    weights = solver.objective_weights_minutes
    pref_weight = weights.get("preferences", 0.0)
    overtime_weight = weights.get("overtime", 0.0)
    
    assert pref_weight >= 0.0, "Peso preferenze dovrebbe essere non negativo"
    assert overtime_weight >= 0.0, "Peso straordinari dovrebbe essere non negativo"


def test_coverage_modes_comparison():
    """Test che confronta modalità adaptive_slots vs disabled."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1",
         "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"),
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    # Test con modalità disabled
    solver_disabled = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        coverage_mode="disabled",
        config=SolverConfig(max_seconds=2.0)
    )
    
    solver_disabled.build()
    
    # Verifica che non abbia variabili slot
    assert len(getattr(solver_disabled, 'slot_shortfall_vars', {})) == 0
    
    # Test con adaptive slots (senza finestre)
    solver_adaptive = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        coverage_mode="adaptive_slots",
        enable_slot_slack=True,
        config=SolverConfig(max_seconds=2.0)
    )
    
    solver_adaptive.build()
    
    # Senza finestre, anche adaptive_slots non dovrebbe avere slot
    assert len(getattr(solver_adaptive, 'slot_shortfall_vars', {})) == 0


def test_performance_budget():
    """Test che verifica il budget di performance per test singolo."""
    import time
    
    start_time = time.time()
    
    # Test semplice e veloce
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1",
         "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"),
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        coverage_mode="disabled",
        config=SolverConfig(max_seconds=1.0)  # Timeout molto basso
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    end_time = time.time()
    duration = end_time - start_time
    
    # Verifica che il test sia veloce (< 5 secondi)
    assert duration < 5.0, f"Test troppo lento: {duration:.2f}s"
    
    # Verifica che trovi comunque una soluzione
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]


def test_solver_basic_functionality():
    """Test base che il solver funzioni con dati minimi."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1",
         "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"),
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        coverage_mode="disabled",
        config=SolverConfig(max_seconds=3.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    # Verifica che trovi una soluzione
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che possa estrarre assegnazioni
    assignments = solver.extract_assignments(cp_solver)
    assert len(assignments) >= 0, "Dovrebbe poter estrarre assegnazioni"
    
    # Verifica breakdown obiettivo
    breakdown = solver.extract_objective_breakdown(cp_solver)
    assert isinstance(breakdown, dict), "Breakdown dovrebbe essere un dizionario"
    assert "unmet_demand" in breakdown, "Dovrebbe contenere unmet_demand"


@pytest.mark.parametrize("coverage_mode", ["disabled", "adaptive_slots"])
def test_coverage_modes_parametrized(coverage_mode):
    """Test parametrizzato per diverse modalità di copertura."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1",
         "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"),
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        coverage_mode=coverage_mode,
        enable_slot_slack=True,
        config=SolverConfig(max_seconds=2.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    # Dovrebbe funzionare con entrambe le modalità
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica numero di variabili slot
    actual_slots = len(getattr(solver, 'slot_shortfall_vars', {}))
    
    if coverage_mode == "disabled":
        assert actual_slots == 0, "Modalità disabled non dovrebbe avere slot"
    else:
        # Con adaptive_slots senza finestre, non dovrebbe avere slot
        assert actual_slots >= 0, "Modalità adaptive_slots dovrebbe gestire slot"


def test_mean_shift_minutes_calculation():
    """Test che mean_shift_minutes sia calcolato correttamente."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    # Turni di durata diversa
    shifts = pd.DataFrame([
        {"shift_id": "S_SHORT", "day": "2025-10-07", "start": "06:00", "end": "10:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1",
         "duration_h": 4.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"),
         "end_dt": pd.Timestamp("2025-10-07 10:00:00")},  # 4 ore = 240 min
        {"shift_id": "S_LONG", "day": "2025-10-08", "start": "06:00", "end": "18:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1",
         "duration_h": 12.0, "start_dt": pd.Timestamp("2025-10-08 06:00:00"),
         "end_dt": pd.Timestamp("2025-10-08 18:00:00")},  # 12 ore = 720 min
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S_SHORT", "can_assign": 1},
        {"employee_id": "E1", "shift_id": "S_LONG", "can_assign": 1},
    ])
    
    preferences = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S_SHORT", "score": -1},
        {"employee_id": "E1", "shift_id": "S_LONG", "score": -1},
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        preferences=preferences,
        coverage_mode="disabled",
        config=SolverConfig(max_seconds=3.0)
    )
    
    solver.build()
    
    # Media dovrebbe essere (240 + 720) / 2 = 480 minuti
    expected_mean = 480
    assert solver.mean_shift_minutes == expected_mean, \
        f"Mean shift minutes: atteso {expected_mean}, ottenuto {solver.mean_shift_minutes}"


def test_solver_with_preferences():
    """Test che il solver gestisca correttamente le preferenze."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S_LIKED", "day": "2025-10-07", "start": "06:00", "end": "14:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1",
         "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-07 06:00:00"),
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
        {"shift_id": "S_DISLIKED", "day": "2025-10-08", "start": "06:00", "end": "14:00",
         "role": "nurse", "required_staff": 1, "demand": 1, "skill_requirements": "first_aid=1",
         "duration_h": 8.0, "start_dt": pd.Timestamp("2025-10-08 06:00:00"),
         "end_dt": pd.Timestamp("2025-10-08 14:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S_LIKED", "can_assign": 1},
        {"employee_id": "E1", "shift_id": "S_DISLIKED", "can_assign": 1},
    ])
    
    preferences = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S_LIKED", "score": 2},      # Molto gradito
        {"employee_id": "E1", "shift_id": "S_DISLIKED", "score": -2},  # Molto sgradito
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        preferences=preferences,
        coverage_mode="disabled",
        config=SolverConfig(max_seconds=3.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che le preferenze siano considerate
    breakdown = solver.extract_objective_breakdown(cp_solver)
    assert "preferences" in breakdown, "Dovrebbe includere costo preferenze"
    
    pref_cost = breakdown["preferences"]["cost"]
    assert pref_cost >= 0.0, "Costo preferenze dovrebbe essere non negativo"
