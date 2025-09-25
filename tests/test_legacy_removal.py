"""Test per verificare la rimozione definitiva del supporto legacy - STEP 6B."""
import pytest
import tempfile
import pandas as pd
from pathlib import Path

from src.loader import load_data_bundle, load_shifts


def test_required_staff_field_rejected():
    """Test che required_staff venga rifiutato in v2.0+."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Crea shifts.csv con required_staff (non più supportato)
        shifts_data = """shift_id,day,start,end,role,required_staff
S1,2025-10-07,06:00,14:00,nurse,2
"""
        shifts_path = Path(temp_dir) / "shifts.csv"
        with open(shifts_path, 'w') as f:
            f.write(shifts_data)
        
        # Dovrebbe sollevare ValueError
        with pytest.raises(ValueError, match="'demand' column is required"):
            load_shifts(shifts_path)


def test_missing_demand_field_rejected():
    """Test che l'assenza del campo demand venga rifiutata."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Crea shifts.csv senza demand (obbligatorio)
        shifts_data = """shift_id,day,start,end,role
S1,2025-10-07,06:00,14:00,nurse
"""
        shifts_path = Path(temp_dir) / "shifts.csv"
        with open(shifts_path, 'w') as f:
            f.write(shifts_data)
        
        # Dovrebbe sollevare ValueError
        with pytest.raises(ValueError, match="'demand' column is required"):
            load_shifts(shifts_path)


def test_demand_field_accepted():
    """Test che il campo demand sia accettato correttamente."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Crea shifts.csv con demand (nuovo schema)
        shifts_data = """shift_id,day,start,end,role,demand
S1,2025-10-07,06:00,14:00,nurse,2
S2,2025-10-07,14:00,22:00,nurse,1
"""
        shifts_path = Path(temp_dir) / "shifts.csv"
        with open(shifts_path, 'w') as f:
            f.write(shifts_data)
        
        # Dovrebbe funzionare correttamente
        df = load_shifts(shifts_path)
        
        assert len(df) == 2
        assert 'demand' in df.columns
        assert 'required_staff' in df.columns  # Creato internamente per compatibilità
        assert df.iloc[0]['demand'] == 2
        assert df.iloc[0]['required_staff'] == 2  # Mappato da demand
        assert df.iloc[1]['demand'] == 1
        assert df.iloc[1]['required_staff'] == 1


def test_windows_csv_required():
    """Test che windows.csv sia obbligatorio in v2.0+."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Crea solo i file base senza windows.csv
        employees_data = """employee_id,name,roles,max_week_hours,min_rest_hours,max_overtime_hours,skills
E1,Alice,nurse,40,8,10,first_aid
"""
        shifts_data = """shift_id,day,start,end,role,demand
S1,2025-10-07,06:00,14:00,nurse,1
"""
        availability_data = """employee_id,shift_id,is_available
E1,S1,1
"""
        
        with open(Path(temp_dir) / "employees.csv", 'w') as f:
            f.write(employees_data)
        with open(Path(temp_dir) / "shifts.csv", 'w') as f:
            f.write(shifts_data)
        with open(Path(temp_dir) / "availability.csv", 'w') as f:
            f.write(availability_data)
        
        # Dovrebbe sollevare FileNotFoundError
        with pytest.raises(FileNotFoundError, match="windows.csv is required"):
            load_data_bundle(Path(temp_dir))


def test_shifts_only_mode_bypass():
    """Test che shifts_only_mode permetta di bypassare il requisito windows.csv."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Crea solo i file base senza windows.csv
        employees_data = """employee_id,name,roles,max_week_hours,min_rest_hours,max_overtime_hours,skills
E1,Alice,nurse,40,8,10,first_aid
"""
        shifts_data = """shift_id,day,start,end,role,demand
S1,2025-10-07,06:00,14:00,nurse,1
"""
        availability_data = """employee_id,shift_id,is_available
E1,S1,1
"""
        
        with open(Path(temp_dir) / "employees.csv", 'w') as f:
            f.write(employees_data)
        with open(Path(temp_dir) / "shifts.csv", 'w') as f:
            f.write(shifts_data)
        with open(Path(temp_dir) / "availability.csv", 'w') as f:
            f.write(availability_data)
        
        # Dovrebbe funzionare con shifts_only_mode=True
        bundle = load_data_bundle(Path(temp_dir), shifts_only_mode=True)
        
        assert len(bundle.employees_df) == 1
        assert len(bundle.shifts_df) == 1
        assert len(bundle.windows_df) == 0  # Nessuna finestra caricata


def test_complete_v2_schema_works():
    """Test che lo schema completo v2.0 funzioni perfettamente."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Crea tutti i file con nuovo schema
        employees_data = """employee_id,name,roles,max_week_hours,min_rest_hours,max_overtime_hours,skills
E1,Alice,nurse,40,8,10,first_aid
E2,Bob,nurse,40,8,10,first_aid
"""
        shifts_data = """shift_id,day,start,end,role,demand
S1,2025-10-07,06:00,14:00,nurse,2
S2,2025-10-07,14:00,22:00,nurse,1
"""
        availability_data = """employee_id,shift_id,is_available
E1,S1,1
E1,S2,1
E2,S1,1
E2,S2,0
"""
        windows_data = """window_id,day,window_start,window_end,role,window_demand
WIN1,2025-10-07,08:00,12:00,nurse,1
WIN2,2025-10-07,16:00,20:00,nurse,1
"""
        
        with open(Path(temp_dir) / "employees.csv", 'w') as f:
            f.write(employees_data)
        with open(Path(temp_dir) / "shifts.csv", 'w') as f:
            f.write(shifts_data)
        with open(Path(temp_dir) / "availability.csv", 'w') as f:
            f.write(availability_data)
        with open(Path(temp_dir) / "windows.csv", 'w') as f:
            f.write(windows_data)
        
        # Dovrebbe funzionare perfettamente
        bundle = load_data_bundle(Path(temp_dir))
        
        # Verifica struttura completa
        assert len(bundle.employees_df) == 2
        assert len(bundle.shifts_df) == 2
        assert len(bundle.windows_df) == 2
        assert len(bundle.assign_mask_df) > 0
        
        # Verifica che demand sia usato correttamente
        s1 = bundle.shifts_df[bundle.shifts_df['shift_id'] == 'S1'].iloc[0]
        s2 = bundle.shifts_df[bundle.shifts_df['shift_id'] == 'S2'].iloc[0]
        
        assert s1['demand'] == 2
        assert s1['required_staff'] == 2  # Mappato da demand
        assert s2['demand'] == 1
        assert s2['required_staff'] == 1
        
        # Verifica finestre
        assert len(bundle.windows) == 2
        assert 'WIN1' in bundle.windows
        assert 'WIN2' in bundle.windows


def test_legacy_parameter_removed():
    """Test che il parametro allow_legacy_without_windows sia stato rimosso."""
    with tempfile.TemporaryDirectory() as temp_dir:
        employees_data = """employee_id,name,roles,max_week_hours,min_rest_hours,max_overtime_hours,skills
E1,Alice,nurse,40,8,10,first_aid
"""
        shifts_data = """shift_id,day,start,end,role,demand
S1,2025-10-07,06:00,14:00,nurse,1
"""
        availability_data = """employee_id,shift_id,is_available
E1,S1,1
"""
        
        with open(Path(temp_dir) / "employees.csv", 'w') as f:
            f.write(employees_data)
        with open(Path(temp_dir) / "shifts.csv", 'w') as f:
            f.write(shifts_data)
        with open(Path(temp_dir) / "availability.csv", 'w') as f:
            f.write(availability_data)
        
        # Il parametro allow_legacy_without_windows non dovrebbe più esistere
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            load_data_bundle(Path(temp_dir), allow_legacy_without_windows=True)


def test_internal_consistency_maintained():
    """Test che la consistenza interna sia mantenuta dopo la rimozione legacy."""
    with tempfile.TemporaryDirectory() as temp_dir:
        shifts_data = """shift_id,day,start,end,role,demand
S1,2025-10-07,06:00,14:00,nurse,3
S2,2025-10-07,14:00,22:00,nurse,1
"""
        shifts_path = Path(temp_dir) / "shifts.csv"
        with open(shifts_path, 'w') as f:
            f.write(shifts_data)
        
        df = load_shifts(shifts_path)
        
        # Verifica che required_staff sia sempre uguale a demand
        for _, row in df.iterrows():
            assert row['required_staff'] == row['demand'], f"Inconsistenza per {row['shift_id']}"
        
        # Verifica che non ci siano colonne legacy
        assert 'demand_id' in df.columns  # Questo può rimanere vuoto
        assert all(df['demand_id'] == "")  # Ma dovrebbe essere vuoto per default


def test_error_messages_clear():
    """Test che i messaggi di errore siano chiari e informativi."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Test 1: required_staff
        shifts_data_legacy = """shift_id,day,start,end,role,required_staff
S1,2025-10-07,06:00,14:00,nurse,2
"""
        shifts_path = Path(temp_dir) / "shifts_legacy.csv"
        with open(shifts_path, 'w') as f:
            f.write(shifts_data_legacy)
        
        with pytest.raises(ValueError) as exc_info:
            load_shifts(shifts_path)
        
        error_msg = str(exc_info.value)
        assert "Legacy 'required_staff' is no longer supported" in error_msg
        assert "'demand' column is required" in error_msg
        
        # Test 2: missing demand
        shifts_data_no_demand = """shift_id,day,start,end,role
S1,2025-10-07,06:00,14:00,nurse
"""
        shifts_path_2 = Path(temp_dir) / "shifts_no_demand.csv"
        with open(shifts_path_2, 'w') as f:
            f.write(shifts_data_no_demand)
        
        with pytest.raises(ValueError) as exc_info:
            load_shifts(shifts_path_2)
        
        error_msg = str(exc_info.value)
        assert "'demand' column is required" in error_msg
        assert "Legacy 'required_staff' is no longer supported" in error_msg
