"""Test per la penalità di overstaffing."""

import pytest
import pandas as pd
from pathlib import Path

from src import config_loader, model_cp, loader, precompute


def test_overstaffing_config_loading():
    """Test che la configurazione dell'overstaffing venga caricata correttamente."""
    cfg = config_loader.load_config("config.yaml")
    
    # Verifica che overstaff sia presente nelle penalità
    assert hasattr(cfg.penalties, 'overstaff')
    assert cfg.penalties.overstaff == 0.15
    
    # Verifica che overstaff sia nella priorità dell'obiettivo
    assert 'overstaff' in cfg.objective.priority
    
    # Verifica che overstaff sia nelle chiavi di priorità
    assert 'overstaff' in config_loader.PRIORITY_KEYS


def test_overstaffing_model_integration():
    """Test che il modello integri correttamente le variabili di overstaffing."""
    # Crea dati di test minimi
    employees = pd.DataFrame({
        'employee_id': ['E1', 'E2'],
        'min_week_hours': [0, 0],
        'max_week_hours': [40, 40],
        'max_overtime_hours': [0, 0],
        'min_rest_hours': [8, 8],
        'roles_set': [set(), set()]
    })
    
    shifts = pd.DataFrame({
        'shift_id': ['S1'],
        'day': ['2024-01-01'],
        'start': ['09:00'],
        'end': ['17:00'],
        'duration_h': [8.0],
        'role': ['nurse'],
        'required_staff': [1]
    })
    
    assign_mask = pd.DataFrame({
        'employee_id': ['E1', 'E2'],
        'shift_id': ['S1', 'S1'],
        'can_assign': [1, 1]
    })
    
    # Crea il solver
    solver = model_cp.ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask
    )
    
    # Verifica che le variabili di overstaffing siano inizializzate
    assert hasattr(solver, 'window_overstaff_vars')
    assert isinstance(solver.window_overstaff_vars, dict)


def test_overstaffing_objective_weights():
    """Test che i pesi dell'overstaffing siano configurati correttamente."""
    cfg = config_loader.load_config("config.yaml")
    
    penalties = {
        "unmet_window": cfg.penalties.unmet_window,
        "unmet_demand": cfg.penalties.unmet_demand,
        "unmet_skill": cfg.penalties.unmet_skill,
        "unmet_shift": cfg.penalties.unmet_shift,
        "overstaff": cfg.penalties.overstaff,
        "overtime": cfg.penalties.overtime,
        "fairness": cfg.penalties.fairness,
        "preferences": cfg.penalties.preferences,
    }
    
    objective_weights = model_cp._build_objective_weights(cfg.objective.priority, penalties)
    
    # Verifica che overstaff abbia un peso
    assert 'overstaff' in objective_weights
    assert objective_weights['overstaff'] == int(cfg.penalties.overstaff * 100)


if __name__ == "__main__":
    pytest.main([__file__])
