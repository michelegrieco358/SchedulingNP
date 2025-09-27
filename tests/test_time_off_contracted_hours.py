"""Test per verificare il calcolo corretto delle ore contrattuali con time_off."""

import pytest
import pandas as pd
from pathlib import Path

from src import model_cp, loader


def test_time_off_calculation_basic():
    """Test che _calculate_time_off_minutes calcoli correttamente i minuti di assenza."""
    # Crea dati time_off di test
    time_off_data = pd.DataFrame({
        'employee_id': ['E1', 'E1', 'E2'],
        'day': ['2024-01-01', '2024-01-02', '2024-01-01'],
        'start_time': ['09:00', '00:00', '14:00'],
        'end_time': ['17:00', '24:00', '18:00'],
        'reason': ['Ferie', 'Malattia', 'Permesso']
    })
    
    # Crea solver con time_off_data
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
    
    solver = model_cp.ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        time_off_data=time_off_data
    )
    
    # Test calcolo time_off per E1 (8h + 24h = 32h = 1920 minuti)
    time_off_e1 = solver._calculate_time_off_minutes('E1')
    expected_e1 = 8 * 60 + 24 * 60  # 1920 minuti
    assert time_off_e1 == expected_e1, f"E1: atteso {expected_e1}, ottenuto {time_off_e1}"
    
    # Test calcolo time_off per E2 (4h = 240 minuti)
    time_off_e2 = solver._calculate_time_off_minutes('E2')
    expected_e2 = 4 * 60  # 240 minuti
    assert time_off_e2 == expected_e2, f"E2: atteso {expected_e2}, ottenuto {time_off_e2}"
    
    # Test calcolo time_off per dipendente inesistente
    time_off_e3 = solver._calculate_time_off_minutes('E3')
    assert time_off_e3 == 0, f"E3: atteso 0, ottenuto {time_off_e3}"


def test_time_off_with_datetime_columns():
    """Test calcolo time_off con colonne start_datetime/end_datetime."""
    # Crea dati time_off con datetime espliciti
    time_off_data = pd.DataFrame({
        'employee_id': ['E1'],
        'day': ['2024-01-01'],
        'start_datetime': [pd.to_datetime('2024-01-01 09:00:00')],
        'end_datetime': [pd.to_datetime('2024-01-01 17:00:00')],
        'reason': ['Ferie']
    })
    
    employees = pd.DataFrame({
        'employee_id': ['E1'],
        'min_week_hours': [0],
        'max_week_hours': [40],
        'max_overtime_hours': [0],
        'min_rest_hours': [8],
        'roles_set': [set()]
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
        'employee_id': ['E1'],
        'shift_id': ['S1'],
        'can_assign': [1]
    })
    
    solver = model_cp.ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        time_off_data=time_off_data
    )
    
    # Test calcolo con datetime espliciti (8h = 480 minuti)
    time_off_e1 = solver._calculate_time_off_minutes('E1')
    expected = 8 * 60  # 480 minuti
    assert time_off_e1 == expected, f"E1: atteso {expected}, ottenuto {time_off_e1}"


def test_contracted_hours_with_time_off():
    """Test integrazione time_off nei vincoli di ore contrattuali."""
    # Dipendente contrattualizzato con 40h/settimana e 8h di ferie
    employees = pd.DataFrame({
        'employee_id': ['E1'],
        'contracted_hours': [40.0],  # Contrattualizzato
        'min_week_hours': [0],
        'max_week_hours': [40],
        'max_overtime_hours': [8],
        'min_rest_hours': [8],
        'roles_set': [set()]
    })
    
    # Time_off: 8 ore di ferie
    time_off_data = pd.DataFrame({
        'employee_id': ['E1'],
        'day': ['2024-01-01'],
        'start_time': ['09:00'],
        'end_time': ['17:00'],
        'reason': ['Ferie']
    })
    
    shifts = pd.DataFrame({
        'shift_id': ['S1', 'S2'],
        'day': ['2024-01-01', '2024-01-02'],
        'start': ['09:00', '09:00'],
        'end': ['17:00', '17:00'],
        'duration_h': [8.0, 8.0],
        'role': ['nurse', 'nurse'],
        'required_staff': [1, 1],
        'start_dt': [pd.to_datetime('2024-01-01 09:00:00'), pd.to_datetime('2024-01-02 09:00:00')],
        'end_dt': [pd.to_datetime('2024-01-01 17:00:00'), pd.to_datetime('2024-01-02 17:00:00')]
    })
    
    assign_mask = pd.DataFrame({
        'employee_id': ['E1', 'E1'],
        'shift_id': ['S1', 'S2'],
        'can_assign': [1, 1]
    })
    
    solver = model_cp.ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        time_off_data=time_off_data
    )
    
    # Verifica che time_off_data sia stato passato correttamente
    assert solver.time_off_data is not None
    assert not solver.time_off_data.empty
    assert len(solver.time_off_data) == 1
    
    # Verifica calcolo time_off
    time_off_minutes = solver._calculate_time_off_minutes('E1')
    assert time_off_minutes == 8 * 60, f"Time_off: atteso 480, ottenuto {time_off_minutes}"
    
    # Build del modello per testare i vincoli
    solver.build()
    
    # Verifica che le variabili overtime siano state create
    assert 'E1' in solver.overtime_vars
    
    # Il dipendente dovrebbe avere vincoli:
    # worked_minutes + time_off_minutes >= contracted_minutes
    # worked_minutes + time_off_minutes <= contracted_minutes + overtime_minutes
    # Con time_off = 480min, contracted = 2400min
    # Quindi: worked_minutes >= 1920min e worked_minutes <= 1920min + overtime


def test_no_time_off_data():
    """Test che il sistema funzioni correttamente senza dati time_off."""
    employees = pd.DataFrame({
        'employee_id': ['E1'],
        'contracted_hours': [40.0],
        'min_week_hours': [0],
        'max_week_hours': [40],
        'max_overtime_hours': [0],
        'min_rest_hours': [8],
        'roles_set': [set()]
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
        'employee_id': ['E1'],
        'shift_id': ['S1'],
        'can_assign': [1]
    })
    
    # Solver senza time_off_data
    solver = model_cp.ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        time_off_data=None
    )
    
    # Verifica che time_off_data sia None
    assert solver.time_off_data is None
    
    # Calcolo time_off dovrebbe restituire 0
    time_off_minutes = solver._calculate_time_off_minutes('E1')
    assert time_off_minutes == 0, f"Senza time_off: atteso 0, ottenuto {time_off_minutes}"


if __name__ == "__main__":
    pytest.main([__file__])
