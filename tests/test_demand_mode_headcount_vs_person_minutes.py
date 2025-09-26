"""
Test per le due modalità di interpretazione della domanda:
- headcount: numero minimo di persone simultanee
- person_minutes: volume totale di lavoro in persona-minuti

Solo applicabile quando preserve_shift_integrity=True.
"""
import pytest
import pandas as pd
from datetime import datetime, time
from pathlib import Path

from src.model_cp import ShiftSchedulingCpSolver, SolverConfig
from src.config_loader import Config, ShiftsConfig


@pytest.fixture
def mock_employees():
    """Dipendenti di test con skill diverse."""
    return pd.DataFrame([
        {
            "employee_id": "E1",
            "name": "Alice",
            "roles": "nurse",
            "max_week_hours": 40,
            "min_rest_hours": 8,
            "max_overtime_hours": 10,
            "roles_set": {"nurse"},
            "skills_set": {"first_aid"},
            "primary_role": "nurse"
        },
        {
            "employee_id": "E2", 
            "name": "Bob",
            "roles": "nurse",
            "max_week_hours": 40,
            "min_rest_hours": 8,
            "max_overtime_hours": 10,
            "roles_set": {"nurse"},
            "skills_set": {"first_aid"},
            "primary_role": "nurse"
        },
        {
            "employee_id": "E3",
            "name": "Carol",
            "roles": "nurse", 
            "max_week_hours": 40,
            "min_rest_hours": 8,
            "max_overtime_hours": 10,
            "roles_set": {"nurse"},
            "skills_set": {"first_aid"},
            "primary_role": "nurse"
        }
    ])


@pytest.fixture
def mock_shifts():
    """Turni di test con durate diverse."""
    base_day = datetime(2025, 10, 7)
    return pd.DataFrame([
        {
            "shift_id": "S1_SHORT",
            "day": base_day.date(),
            "start": time(6, 0),
            "end": time(10, 0),  # 4 ore
            "start_dt": datetime.combine(base_day.date(), time(6, 0)),
            "end_dt": datetime.combine(base_day.date(), time(10, 0)),
            "role": "nurse",
            "demand": 1,
            "required_staff": 1,
            "duration_h": 4.0,
            "skill_requirements": {}
        },
        {
            "shift_id": "S2_LONG",
            "day": base_day.date(),
            "start": time(8, 0),
            "end": time(20, 0),  # 12 ore
            "start_dt": datetime.combine(base_day.date(), time(8, 0)),
            "end_dt": datetime.combine(base_day.date(), time(20, 0)),
            "role": "nurse",
            "demand": 1,
            "required_staff": 1,
            "duration_h": 12.0,
            "skill_requirements": {}
        }
    ])


@pytest.fixture
def mock_assign_mask():
    """Maschera di assegnazione - tutti disponibili."""
    pairs = []
    for emp_id in ["E1", "E2", "E3"]:
        for shift_id in ["S1_SHORT", "S2_LONG"]:
            pairs.append({
                "employee_id": emp_id,
                "shift_id": shift_id,
                "can_assign": 1,
                "qual_ok": 1,
                "is_available": 1
            })
    return pd.DataFrame(pairs)


@pytest.fixture
def mock_window_demands():
    """Finestre temporali di test."""
    return {
        "WIN1": 2  # Richiede 2 persone simultanee nella finestra
    }


@pytest.fixture
def mock_window_duration_minutes():
    """Durata finestre in minuti."""
    return {
        "WIN1": 240  # 4 ore = 240 minuti
    }


@pytest.fixture
def mock_adaptive_slot_data():
    """Dati di segmentazione semplificati per test."""
    class MockSlotData:
        def __init__(self):
            # Segmenti temporali (in minuti dall'inizio giornata)
            self.segment_bounds = {
                "SEG_06_08": (360, 480),  # 06:00-08:00 (2 ore)
                "SEG_08_10": (480, 600),  # 08:00-10:00 (2 ore)  
                "SEG_10_12": (600, 720),  # 10:00-12:00 (2 ore)
                "SEG_12_20": (720, 1200), # 12:00-20:00 (8 ore)
            }
            
            # Mappa turno -> segmenti che copre
            self.segments_of_s = {
                "S1_SHORT": ["SEG_06_08", "SEG_08_10"],  # 06:00-10:00
                "S2_LONG": ["SEG_08_10", "SEG_10_12", "SEG_12_20"]  # 08:00-20:00
            }
    
    return MockSlotData()


def test_demand_mode_headcount(mock_employees, mock_shifts, mock_assign_mask, 
                               mock_window_demands, mock_window_duration_minutes,
                               mock_adaptive_slot_data):
    """Test modalità headcount: domanda costante per segmento."""
    
    solver = ShiftSchedulingCpSolver(
        employees=mock_employees,
        shifts=mock_shifts,
        assign_mask=mock_assign_mask,
        window_demands=mock_window_demands,
        window_duration_minutes=mock_window_duration_minutes,
        adaptive_slot_data=mock_adaptive_slot_data,
        config=SolverConfig(max_seconds=5.0)
    )
    
    # Imposta modalità headcount
    solver.demand_mode = "headcount"
    
    # Costruisce il modello
    solver.build()
    
    # Verifica che le domande dei segmenti siano calcolate correttamente
    # In modalità headcount, ogni segmento dovrebbe richiedere il massimo
    # tra le finestre che lo intersecano (in questo caso 2 persone)
    assert len(solver.segment_demands) > 0
    
    # Tutti i segmenti dovrebbero avere domanda = 2 (headcount della finestra)
    for segment_id, demand in solver.segment_demands.items():
        assert demand == 2, f"Segmento {segment_id}: atteso 2, ottenuto {demand}"
    
    print(f"✓ Modalità headcount: {len(solver.segment_demands)} segmenti con domanda costante = 2")


def test_demand_mode_person_minutes(mock_employees, mock_shifts, mock_assign_mask,
                                   mock_window_demands, mock_window_duration_minutes, 
                                   mock_adaptive_slot_data):
    """Test modalità person_minutes: domanda proporzionale alla durata."""
    
    solver = ShiftSchedulingCpSolver(
        employees=mock_employees,
        shifts=mock_shifts,
        assign_mask=mock_assign_mask,
        window_demands=mock_window_demands,
        window_duration_minutes=mock_window_duration_minutes,
        adaptive_slot_data=mock_adaptive_slot_data,
        config=SolverConfig(max_seconds=5.0)
    )
    
    # Imposta modalità person_minutes
    solver.demand_mode = "person_minutes"
    
    # Costruisce il modello
    solver.build()
    
    # Verifica che le domande dei segmenti siano calcolate proporzionalmente
    assert len(solver.segment_demands) > 0
    
    # In modalità person_minutes, la domanda dovrebbe essere proporzionale
    # alla durata del segmento rispetto alla finestra
    window_demand = 2  # persone
    window_duration = 240  # minuti
    
    expected_demands = {}
    for segment_id, (start_min, end_min) in mock_adaptive_slot_data.segment_bounds.items():
        segment_duration = end_min - start_min
        # contribution = window_demand * segment_duration / window_duration
        expected_demand = max(1, int(round(window_demand * segment_duration / window_duration)))
        expected_demands[segment_id] = expected_demand
    
    for segment_id, actual_demand in solver.segment_demands.items():
        expected_demand = expected_demands.get(segment_id, 1)
        assert actual_demand == expected_demand, \
            f"Segmento {segment_id}: atteso {expected_demand}, ottenuto {actual_demand}"
    
    print(f"✓ Modalità person_minutes: domande proporzionali calcolate correttamente")


def test_demand_mode_comparison():
    """Test di confronto tra le due modalità con dati identici."""
    
    # Dati di test comuni
    employees = pd.DataFrame([
        {
            "employee_id": "E1", "name": "Alice", "roles": "nurse",
            "max_week_hours": 40, "min_rest_hours": 8, "max_overtime_hours": 10,
            "roles_set": {"nurse"}, "skills_set": {"first_aid"}, "primary_role": "nurse"
        }
    ])
    
    base_day = datetime(2025, 10, 7)
    shifts = pd.DataFrame([
        {
            "shift_id": "S1", "day": base_day.date(),
            "start": time(8, 0), "end": time(16, 0),
            "start_dt": datetime.combine(base_day.date(), time(8, 0)),
            "end_dt": datetime.combine(base_day.date(), time(16, 0)),
            "role": "nurse", "demand": 1, "required_staff": 1,
            "duration_h": 8.0, "skill_requirements": {}
        }
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1, "qual_ok": 1, "is_available": 1}
    ])
    
    window_demands = {"WIN1": 1}
    window_duration_minutes = {"WIN1": 480}  # 8 ore
    
    class MockData:
        def __init__(self):
            self.segment_bounds = {"SEG_08_16": (480, 960)}  # 08:00-16:00
            self.segments_of_s = {"S1": ["SEG_08_16"]}
    
    mock_data = MockData()
    
    # Test modalità headcount
    solver_headcount = ShiftSchedulingCpSolver(
        employees=employees, shifts=shifts, assign_mask=assign_mask,
        window_demands=window_demands, window_duration_minutes=window_duration_minutes,
        adaptive_slot_data=mock_data,
        config=SolverConfig(max_seconds=5.0)
    )
    solver_headcount.demand_mode = "headcount"
    solver_headcount.build()
    
    # Test modalità person_minutes
    solver_person_minutes = ShiftSchedulingCpSolver(
        employees=employees, shifts=shifts, assign_mask=assign_mask,
        window_demands=window_demands, window_duration_minutes=window_duration_minutes,
        adaptive_slot_data=mock_data,
        config=SolverConfig(max_seconds=5.0)
    )
    solver_person_minutes.demand_mode = "person_minutes"
    solver_person_minutes.build()
    
    # Confronta i risultati
    headcount_demand = solver_headcount.segment_demands.get("SEG_08_16", 0)
    person_minutes_demand = solver_person_minutes.segment_demands.get("SEG_08_16", 0)
    
    print(f"Confronto modalità:")
    print(f"  - headcount: {headcount_demand}")
    print(f"  - person_minutes: {person_minutes_demand}")
    
    # In questo caso specifico (segmento = finestra), dovrebbero essere uguali
    assert headcount_demand == person_minutes_demand == 1
    
    print("✓ Confronto modalità completato con successo")


def test_config_integration():
    """Test integrazione con sistema di configurazione."""
    
    # Test configurazione headcount
    config_headcount = Config()
    config_headcount.shifts = ShiftsConfig(demand_mode="headcount")
    
    assert config_headcount.shifts.demand_mode == "headcount"
    
    # Test configurazione person_minutes
    config_person_minutes = Config()
    config_person_minutes.shifts = ShiftsConfig(demand_mode="person_minutes")
    
    assert config_person_minutes.shifts.demand_mode == "person_minutes"
    
    # Test validazione valori non validi
    with pytest.raises(ValueError, match="demand_mode deve essere uno tra"):
        ShiftsConfig(demand_mode="invalid_mode")
    
    print("✓ Integrazione configurazione testata con successo")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
