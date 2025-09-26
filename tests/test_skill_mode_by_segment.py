"""Test per skill_mode by_segment - gestione skills a livello di finestre temporali."""
import pytest
import pandas as pd
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from src.config_loader import Config
from src.loader import load_windows, _parse_window_skills


def test_parse_window_skills():
    """Test parsing skills dalle finestre temporali."""
    # Test formato base
    result = _parse_window_skills("first_aid:1,cpr:2", "WIN_TEST")
    assert result == {"first_aid": 1, "cpr": 2}
    
    # Test skill singola
    result = _parse_window_skills("emergency:1", "WIN_TEST")
    assert result == {"emergency": 1}
    
    # Test stringa vuota
    result = _parse_window_skills("", "WIN_TEST")
    assert result == {}
    
    # Test None
    result = _parse_window_skills(None, "WIN_TEST")
    assert result == {}
    
    # Test con spazi
    result = _parse_window_skills(" first_aid : 1 , cpr : 2 ", "WIN_TEST")
    assert result == {"first_aid": 1, "cpr": 2}
    
    # Test skill con quantità zero (dovrebbe essere ignorata)
    result = _parse_window_skills("first_aid:0,cpr:2", "WIN_TEST")
    assert result == {"cpr": 2}


def test_parse_window_skills_errors():
    """Test errori nel parsing skills."""
    # Test formato errato (manca :)
    with pytest.raises(ValueError, match="deve usare formato skill:quantity"):
        _parse_window_skills("first_aid", "WIN_TEST")
    
    # Test quantità non numerica
    with pytest.raises(ValueError, match="valore non intero"):
        _parse_window_skills("first_aid:abc", "WIN_TEST")
    
    # Test quantità negativa
    with pytest.raises(ValueError, match="valore negativo"):
        _parse_window_skills("first_aid:-1", "WIN_TEST")
    
    # Test skill vuota
    with pytest.raises(ValueError, match="skill vuota"):
        _parse_window_skills(":1", "WIN_TEST")


def test_load_windows_with_skills():
    """Test caricamento finestre con skills."""
    # Crea un file temporaneo con skills
    test_data = """window_id,day,window_start,window_end,role,window_demand,skills
WIN_1,2025-10-07,08:00,16:00,nurse,2,"first_aid:1,cpr:2"
WIN_2,2025-10-07,16:00,23:59,nurse,1,emergency:1
WIN_3,2025-10-07,08:00,20:00,doctor,1,
"""
    
    # Scrivi il file temporaneo
    test_file = Path("test_windows_skills.csv")
    test_file.write_text(test_data)
    
    try:
        # Carica le finestre
        windows = load_windows(test_file)
        
        # Verifica che la colonna skill_requirements sia presente
        assert "skill_requirements" in windows.columns
        
        # Verifica il parsing delle skills
        win1 = windows[windows["window_id"] == "WIN_1"].iloc[0]
        assert win1["skill_requirements"] == {"first_aid": 1, "cpr": 2}
        
        win2 = windows[windows["window_id"] == "WIN_2"].iloc[0]
        assert win2["skill_requirements"] == {"emergency": 1}
        
        win3 = windows[windows["window_id"] == "WIN_3"].iloc[0]
        assert win3["skill_requirements"] == {}
        
    finally:
        # Pulisci il file temporaneo
        if test_file.exists():
            test_file.unlink()


def test_load_windows_without_skills():
    """Test caricamento finestre senza colonna skills."""
    # Crea un file temporaneo senza skills
    test_data = """window_id,day,window_start,window_end,role,window_demand
WIN_1,2025-10-07,08:00,16:00,nurse,2
WIN_2,2025-10-07,16:00,23:59,nurse,1
"""
    
    # Scrivi il file temporaneo
    test_file = Path("test_windows_no_skills.csv")
    test_file.write_text(test_data)
    
    try:
        # Carica le finestre
        windows = load_windows(test_file)
        
        # Verifica che la colonna skill_requirements sia presente ma vuota
        assert "skill_requirements" in windows.columns
        
        # Verifica che tutte le skills siano vuote
        for _, row in windows.iterrows():
            assert row["skill_requirements"] == {}
            
    finally:
        # Pulisci il file temporaneo
        if test_file.exists():
            test_file.unlink()


def test_config_skill_mode():
    """Test configurazione skill_mode."""
    # Test default
    config = Config()
    assert config.skills.skill_mode == "by_shift"
    
    # Test configurazione esplicita
    config_data = {
        "skills": {
            "skill_mode": "by_segment"
        }
    }
    config = Config(**config_data)
    assert config.skills.skill_mode == "by_segment"


def test_config_skill_mode_validation():
    """Test validazione skill_mode."""
    # Test valore valido
    config_data = {
        "skills": {
            "skill_mode": "by_shift"
        }
    }
    config = Config(**config_data)
    assert config.skills.skill_mode == "by_shift"
    
    config_data = {
        "skills": {
            "skill_mode": "by_segment"
        }
    }
    config = Config(**config_data)
    assert config.skills.skill_mode == "by_segment"
    
    # Test valore non valido
    with pytest.raises(ValueError, match="skill_mode deve essere uno tra"):
        config_data = {
            "skills": {
                "skill_mode": "invalid_mode"
            }
        }
        Config(**config_data)


def test_skill_mode_case_insensitive():
    """Test che skill_mode sia case insensitive."""
    config_data = {
        "skills": {
            "skill_mode": "BY_SHIFT"
        }
    }
    config = Config(**config_data)
    assert config.skills.skill_mode == "by_shift"
    
    config_data = {
        "skills": {
            "skill_mode": "By_Segment"
        }
    }
    config = Config(**config_data)
    assert config.skills.skill_mode == "by_segment"


def test_example_windows_with_skills():
    """Test del file di esempio con skills."""
    example_file = Path("examples/windows_with_skills.csv")
    if not example_file.exists():
        pytest.skip("File di esempio non trovato")
    
    # Carica il file di esempio
    windows = load_windows(example_file)
    
    # Verifica che ci siano finestre con skills
    has_skills = any(bool(row["skill_requirements"]) for _, row in windows.iterrows())
    assert has_skills, "Il file di esempio dovrebbe contenere almeno una finestra con skills"
    
    # Verifica formato skills
    for _, row in windows.iterrows():
        skills = row["skill_requirements"]
        assert isinstance(skills, dict)
        for skill_name, quantity in skills.items():
            assert isinstance(skill_name, str)
            assert isinstance(quantity, int)
            assert quantity > 0


def test_integration_skill_mode_by_segment():
    """Test integrazione skill_mode by_segment (placeholder per future implementazioni)."""
    # Questo test sarà espanso quando implementeremo i vincoli by_segment
    config = Config()
    config.skills.skill_mode = "by_segment"
    
    # Per ora verifichiamo solo che la configurazione sia corretta
    assert config.skills.skill_mode == "by_segment"
    
    # TODO: Aggiungere test per:
    # - Generazione segmenti con skill requirements
    # - Vincoli di copertura skill by_segment
    # - Funzione obiettivo con penalizzazioni skill
    # - Reporting skill by_segment
