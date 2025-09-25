import json
from pathlib import Path

import pandas as pd
import pytest

import loader


def _write_csv(path: Path, content: str) -> None:
    path.write_text(content.lstrip(), encoding="utf-8")


def test_load_employees_skills(tmp_path):
    csv_content = """employee_id,name,roles,max_week_hours,min_rest_hours,max_overtime_hours,skills
E1,Alice,front,40,8,5,"muletto, primo_soccorso"
E2,Bob,back,40,8,5,
"""
    path = tmp_path / "employees.csv"
    _write_csv(path, csv_content)

    df = loader.load_employees(path)
    skills_e1 = df.loc[df["employee_id"] == "E1", "skills_set"].iloc[0]
    skills_e2 = df.loc[df["employee_id"] == "E2", "skills_set"].iloc[0]

    assert skills_e1 == {"muletto", "primo_soccorso"}
    assert skills_e2 == set()


def test_load_shifts_skill_requirements_json(tmp_path):
    req = json.dumps({"muletto": 1, "primo_soccorso": 2})
    path = tmp_path / "shifts.csv"
    data = pd.DataFrame(
        [
            {
                "shift_id": "S1",
                "day": "2025-01-01",
                "start": "08:00",
                "end": "16:00",
                "role": "front",
                "demand": 3,
                "skill_requirements": req,
            }
        ]
    )
    data.to_csv(path, index=False)

    df = loader.load_shifts(path)
    reqs = df.loc[df["shift_id"] == "S1", "skill_requirements"].iloc[0]
    assert reqs == {"muletto": 1, "primo_soccorso": 2}


def test_load_shifts_skill_requirements_short_form(tmp_path):
    csv_content = """shift_id,day,start,end,role,demand,skill_requirements
S1,2025-01-01,08:00,16:00,front,2,"muletto=1, primo=1"
"""
    path = tmp_path / "shifts.csv"
    _write_csv(path, csv_content)

    df = loader.load_shifts(path)
    reqs = df.loc[df["shift_id"] == "S1", "skill_requirements"].iloc[0]
    assert reqs == {"muletto": 1, "primo": 1}


def test_load_shifts_skill_requirements_negative(tmp_path):
    csv_content = """shift_id,day,start,end,role,demand,skill_requirements
S1,2025-01-01,08:00,16:00,front,1,"muletto=-1"
"""
    path = tmp_path / "shifts.csv"
    _write_csv(path, csv_content)

    with pytest.raises(ValueError):
        loader.load_shifts(path)


def test_skill_requirements_warn_if_exceed_demand(tmp_path):
    csv_content = """shift_id,day,start,end,role,demand,skill_requirements
S1,2025-01-01,08:00,16:00,front,1,"muletto=1,primo=1"
"""
    path = tmp_path / "shifts.csv"
    _write_csv(path, csv_content)

    with pytest.warns(RuntimeWarning):
        loader.load_shifts(path)
