import sys
from pathlib import Path
from datetime import time

import pandas as pd
import pytest

# Garantisce che i moduli in src siano importabili durante i test
sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import loader  # noqa: E402


def test_load_employees_duplicate_ids(tmp_path):
    csv_path = tmp_path / "employees.csv"
    csv_path.write_text(
        "employee_id,name,roles,max_week_hours,min_rest_hours,max_overtime_hours\n"
        "E1,Alice,role_a,40,11,5\n"
        "E1,Bob,role_b,38,10,4\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc:
        loader.load_employees(csv_path)

    assert "employee_id duplicati" in str(exc.value)


def test_merge_availability_respects_rules():
    quali = pd.DataFrame(
        [
            {"employee_id": "E1", "shift_id": "S1", "qual_ok": 1},
            {"employee_id": "E1", "shift_id": "S2", "qual_ok": 1},
        ]
    )
    availability = pd.DataFrame(
        [
            {"employee_id": "E1", "shift_id": "S1", "is_available": 0},
            {"employee_id": "E2", "shift_id": "S1", "is_available": 1},
        ]
    )

    merged = loader.merge_availability(quali, availability)

    can_assign_map = {
        (row.employee_id, row.shift_id): row.can_assign
        for row in merged.itertuples()
    }

    # Disponibile ma qualificato: 0 a causa indisponibilita' dichiarata
    assert can_assign_map[("E1", "S1")] == 0
    # Nessuna riga in availability -> assume 1
    assert can_assign_map[("E1", "S2")] == 1
    # Qualifica mancante -> sempre 0
    assert can_assign_map[("E2", "S1")] == 0




def test_merge_availability_includes_unqualified_from_availability():
    quali = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "qual_ok": 1},
    ])
    availability = pd.DataFrame([
        {"employee_id": "E2", "shift_id": "S1", "is_available": 1},
    ])

    merged = loader.merge_availability(quali, availability)

    row = merged[(merged["employee_id"] == "E2") & (merged["shift_id"] == "S1")]
    assert not row.empty
    assert row.iloc[0]["qual_ok"] == 0
    assert row.iloc[0]["can_assign"] == 0
