"""Configurazione pytest per la suite di test sintetici."""
import pytest
import time
from typing import Dict, List


# Collezione per tracciare i tempi di esecuzione dei test
test_times: Dict[str, float] = {}


def pytest_configure(config):
    """Configurazione pytest."""
    config.addinivalue_line(
        "markers", "slow: mark test as slow running (excluded by default)"
    )


def pytest_collection_modifyitems(config, items):
    """Modifica la collezione di test per aggiungere marker automatici."""
    for item in items:
        # Aggiungi marker slow per test che potrebbero essere lenti
        if any(keyword in item.name.lower() for keyword in [
            "performance", "many_slots", "parametrized", "complex", "tradeoff"
        ]):
            item.add_marker(pytest.mark.slow)


@pytest.fixture(autouse=True)
def track_test_time(request):
    """Fixture per tracciare il tempo di esecuzione dei test."""
    start_time = time.time()
    yield
    end_time = time.time()
    duration = end_time - start_time
    test_times[request.node.nodeid] = duration


def pytest_sessionfinish(session, exitstatus):
    """Callback eseguito alla fine della sessione di test."""
    if not test_times:
        return
    
    total_time = sum(test_times.values())
    slow_tests = [(name, duration) for name, duration in test_times.items() 
                  if duration > 2.0]  # Test > 2 secondi considerati lenti
    
    print(f"\n=== Performance Summary ===")
    print(f"Total test time: {total_time:.2f}s")
    print(f"Number of tests: {len(test_times)}")
    print(f"Average per test: {total_time/len(test_times):.2f}s")
    
    if slow_tests:
        print(f"\nSlow tests (>2s):")
        for name, duration in sorted(slow_tests, key=lambda x: x[1], reverse=True):
            print(f"  {duration:.2f}s - {name}")
    
    # Budget check
    BUDGET_SECONDS = 60  # Budget massimo per la suite
    if total_time > BUDGET_SECONDS:
        print(f"\n⚠️  WARNING: Test suite exceeded budget ({total_time:.1f}s > {BUDGET_SECONDS}s)")
    else:
        print(f"\n✅ Test suite within budget ({total_time:.1f}s <= {BUDGET_SECONDS}s)")


@pytest.fixture
def mock_solver_config():
    """Fixture per configurazione solver veloce per test."""
    from src.model_cp import SolverConfig
    return SolverConfig(max_seconds=2.0)  # Timeout basso per test veloci


@pytest.fixture
def sample_employees():
    """Fixture per dipendenti di esempio."""
    import pandas as pd
    return pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "first_aid"},
        {"employee_id": "E2", "name": "Bob", "roles": "doctor", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": "surgery"},
    ])


@pytest.fixture
def sample_shifts():
    """Fixture per turni di esempio."""
    import pandas as pd
    return pd.DataFrame([
        {"shift_id": "S1", "day": "2025-10-07", "start": "06:00", "end": "14:00",
         "role": "nurse", "demand": 1, "skill_requirements": "first_aid=1"},
        {"shift_id": "S2", "day": "2025-10-07", "start": "14:00", "end": "22:00",
         "role": "doctor", "demand": 1, "skill_requirements": "surgery=1"},
    ])


@pytest.fixture
def sample_assign_mask(sample_employees, sample_shifts):
    """Fixture per maschera di assegnabilità di esempio."""
    import pandas as pd
    return pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S2", "can_assign": 1},
    ])


class MockAdaptiveSlotData:
    """Classe mock per adaptive slot data nei test."""
    
    def __init__(self, shifts=None, slots=None):
        self.shifts = shifts or ["S1", "S2"]
        self.slots = slots or ["SLOT_1", "SLOT_2"]
        
        # Genera dati mock
        self.segments_of_s = {shift: [f"SEG_{shift}"] for shift in self.shifts}
        self.cover_segment = {
            (f"SEG_{shift}", slot): 1 if shift == slot.replace("SLOT_", "S") else 0
            for shift in self.shifts for slot in self.slots
        }
        self.slot_bounds = {
            slot: (i * 60, (i + 1) * 60) for i, slot in enumerate(self.slots)
        }
        self.window_bounds = {}
        self.slot_windows = {}


@pytest.fixture
def mock_adaptive_slot_data():
    """Fixture per dati slot adattivi mock."""
    return MockAdaptiveSlotData()
