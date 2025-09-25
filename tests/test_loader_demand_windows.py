import sys
from pathlib import Path

import pandas as pd
import pytest
from textwrap import dedent

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import loader  # noqa: E402
import model_cp  # noqa: E402


def _write_text(path: Path, content: str) -> None:
    path.write_text(dedent(content).lstrip(), encoding="utf-8")


def test_load_demand_windows_parsing(tmp_path):
    csv = """demand_id,window_start,window_end,role,window_demand
W1,09:00,13:00,cassa,3
"""
    path = tmp_path / "demand_windows.csv"
    _write_text(path, csv)

    df = loader.load_demand_windows(path)
    assert df.loc[0, "demand_id"] == "W1"
    assert df.loc[0, "window_demand"] == 3
    assert df.loc[0, "window_start"].hour == 9
    assert df.loc[0, "window_end"].hour == 13


def _prepare_minimal_dataset(tmp_path: Path, *, demand_id: str | None, window_demand: int | None = None):
    employees_csv = """employee_id,name,roles,max_week_hours,min_rest_hours,max_overtime_hours
E1,Alice,cassa,40,0,0
E2,Bob,cassa,40,0,0
"""
    shifts_csv = (
        "shift_id,day,start,end,role,required_staff,demand,demand_id,skill_requirements\n"
        "S1,2025-01-01,09:00,13:00,cassa,1,0,{demand_id},\"{{}}\"\n"
        "S2,2025-01-01,09:00,13:00,cassa,1,0,{demand_id},\"{{}}\"\n"
    ).format(demand_id=demand_id or '')
    availability_csv = """employee_id,shift_id,is_available
E1,S1,1
E1,S2,1
E2,S1,1
E2,S2,1
"""
    overtime_csv = """role,overtime_cost_per_hour
cassa,0
"""

    data_dir = tmp_path
    _write_text(data_dir / "employees.csv", employees_csv)
    _write_text(data_dir / "shifts.csv", shifts_csv)
    _write_text(data_dir / "availability.csv", availability_csv)
    _write_text(data_dir / "overtime_costs.csv", overtime_csv)

    if window_demand is not None:
        demand_windows_csv = (
            "demand_id,window_start,window_end,role,window_demand\n"
            "{demand_id},09:00,13:00,cassa,{window_demand}\n"
        ).format(demand_id=demand_id, window_demand=window_demand)
        _write_text(data_dir / "demand_windows.csv", demand_windows_csv)

    return data_dir


def test_load_data_raises_for_missing_window_definition(tmp_path):
    data_dir = _prepare_minimal_dataset(tmp_path, demand_id="W1")

    with pytest.raises(ValueError):
        model_cp._load_data(data_dir, global_min_rest_hours=8)


def test_load_data_warns_when_window_demand_exceeds_capacity(tmp_path):
    data_dir = _prepare_minimal_dataset(tmp_path, demand_id="W1", window_demand=5)

    with pytest.warns(RuntimeWarning):
        model_cp._load_data(data_dir, global_min_rest_hours=8)
