from __future__ import annotations

from types import SimpleNamespace

import math
import numpy as np
import pandas as pd
import pytest

from src import reporting


def test_reporting_generates_files(sample_environment, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    reporter = reporting.ScheduleReporter(sample_environment.solver, sample_environment.cp_solver)

    segment_df = reporter.generate_segment_coverage_report()
    assert list(segment_df.columns) == [
        "segment_id",
        "day",
        "role",
        "start_minute",
        "end_minute",
        "start_time",
        "end_time",
        "demand",
        "assigned",
        "shortfall",
        "overstaffing",
    ]

    objective_df = reporter.generate_objective_breakdown()
    assert "name" in objective_df.columns

    constraint_df = reporter.generate_constraint_report()
    if not constraint_df.empty:
        assert set(constraint_df.columns) == {
            "name",
            "satisfied",
            "binding",
            "violation",
        }


def test_objective_breakdown_matches_solver(sample_environment, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    reporter = reporting.ScheduleReporter(sample_environment.solver, sample_environment.cp_solver)
    solver_breakdown = sample_environment.solver.extract_objective_breakdown(sample_environment.cp_solver)
    report_df = reporter.generate_objective_breakdown()

    for name, data in solver_breakdown.items():
        report_row = report_df.loc[report_df["name"] == name]
        assert not report_row.empty, f"component {name} missing in report"
        assert pytest.approx(data.get("cost", 0.0)) == report_row.iloc[0]["contribution"]


def test_plot_coverage_accepts_time_objects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    reporter = reporting.ScheduleReporter(solver=None, cp_solver=None)

    coverage_df = pd.DataFrame(
        [
            {
                "segment_id": "2024-01-01__Nurse__0540_0600",
                "day": "2024-01-01",
                "role": "Nurse",
                "start_minute": 9 * 60,
                "end_minute": 10 * 60,
                "start_time": "09:00",
                "end_time": "10:00",
                "demand": 60,
                "assigned": 0,
                "shortfall": 60,
                "overstaffing": 0,
            }
        ]
    )

    created_arrays: list[np.ndarray] = []
    original_zeros = np.zeros

    def tracking_zeros(shape, dtype=float):
        arr = original_zeros(shape, dtype=dtype)
        created_arrays.append(arr)
        return arr

    monkeypatch.setattr(np, "zeros", tracking_zeros)

    reporter._plot_coverage(coverage_df)

    assert created_arrays, "expected demand matrix allocation"
    demand_matrix = created_arrays[0]
    assert demand_matrix.sum() > 0

    plot_path = reporter.output_dir / "coverage_plot.png"
    assert plot_path.exists()
    assert plot_path.stat().st_size > 0


class DummyCpSolver:
    def __init__(self, values):
        self._values = values

    def Value(self, var):
        return self._values.get(var, 0)


class DelegatingCpSolver:
    def __init__(self, base_solver, overrides):
        self._base_solver = base_solver
        self._overrides = overrides

    def Value(self, var):
        if var in self._overrides:
            return self._overrides[var]
        return self._base_solver.Value(var)


def test_extract_shortfall_summary_prefers_slot_vars(sample_environment, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    solver = sample_environment.solver

    assert solver.shift_to_covering_segments, "expected at least one shift with covering segments"
    shift_id, segments = next(iter(solver.shift_to_covering_segments.items()))
    assert segments, "expected segments mapped to the shift"

    base_segment = segments[0]
    extra_segment = f"{base_segment}__extra"

    slot_var_a = object()
    slot_var_b = object()
    legacy_var = object()

    solver.slot_shortfall_vars = {base_segment: slot_var_a, extra_segment: slot_var_b}
    solver.shortfall_vars = {shift_id: legacy_var}

    if hasattr(solver, "adaptive_slot_data") and solver.adaptive_slot_data is not None:
        segment_owner = getattr(solver.adaptive_slot_data, "segment_owner", {})
        segment_owner[base_segment] = shift_id
        segment_owner[extra_segment] = shift_id

    solver.shift_to_covering_segments.setdefault(shift_id, []).append(extra_segment)

    cp_solver = DummyCpSolver({slot_var_a: 2, slot_var_b: 1, legacy_var: 99})

    shortfall_df = solver.extract_shortfall_summary(cp_solver)
    expected_units = 3
    expected_minutes = expected_units * solver.duration_minutes.get(shift_id, 0)

    assert shortfall_df.to_dict("records") == [
        {
            "shift_id": shift_id,
            "shortfall_units": expected_units,
            "shortfall_staff_minutes": expected_minutes,
        }
    ]

    report_dir = tmp_path / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    output_path = report_dir / "shortfall_report.csv"
    shortfall_df.to_csv(output_path, index=False)

    saved_df = pd.read_csv(output_path)
    pd.testing.assert_frame_equal(saved_df, shortfall_df)


def test_extract_shortfall_summary_falls_back_to_segments(sample_environment, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    solver = sample_environment.solver

    solver.slot_shortfall_vars = {}
    solver.shortfall_vars = {}

    assert solver.segment_shortfall_vars, "expected segment shortfall variables"
    assert solver.shift_to_covering_segments, "expected mapping shift -> segments"

    shift_id, segments = next(iter(solver.shift_to_covering_segments.items()))
    assert segments, "expected at least one segment for the shift"

    # Select up to two segments for aggregation checks
    segment_ids = segments[:2]

    overrides = {}
    expected_minutes = 0
    expected_units = 0.0

    for idx, segment_id in enumerate(segment_ids):
        var = solver.segment_shortfall_vars.get(segment_id)
        if var is None:
            continue
        minutes = 15 + idx * 10
        overrides[var] = minutes
        expected_minutes += minutes
        segment_duration = max(1, solver._get_segment_duration_minutes(segment_id))
        expected_units += minutes / segment_duration

        if hasattr(solver, "adaptive_slot_data") and solver.adaptive_slot_data is not None:
            segment_owner = getattr(solver.adaptive_slot_data, "segment_owner", None)
            if segment_owner is None:
                segment_owner = {}
                setattr(solver.adaptive_slot_data, "segment_owner", segment_owner)
            segment_owner[segment_id] = shift_id

    assert overrides, "expected at least one overridden segment shortfall"

    cp_solver = DelegatingCpSolver(sample_environment.cp_solver, overrides)

    shortfall_df = solver.extract_shortfall_summary(cp_solver)
    assert not shortfall_df.empty

    row = shortfall_df.loc[shortfall_df["shift_id"] == shift_id].iloc[0]
    assert row["shortfall_staff_minutes"] == expected_minutes
    assert row["shortfall_units"] == int(round(expected_units))

    report_dir = tmp_path / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    output_path = report_dir / "shortfall_report.csv"
    shortfall_df.to_csv(output_path, index=False)
    assert output_path.exists()
    saved_df = pd.read_csv(output_path)
    pd.testing.assert_frame_equal(saved_df, shortfall_df)


def test_constraint_report_prefers_slot_shortfalls(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    slot_var_a = object()
    slot_var_b = object()
    legacy_var = object()
    slot_skill_var = object()
    legacy_skill_var = object()

    solver = SimpleNamespace(
        shortfall_vars={"legacy": legacy_var},
        slot_shortfall_vars={"slot-a": slot_var_a, "slot-b": slot_var_b},
        rest_violations={},
        skill_shortfall_vars={("legacy", "skill") : legacy_skill_var},
        slot_skill_shortfall_vars={("slot", "skill") : slot_skill_var},
    )

    cp_solver = DummyCpSolver(
        {
            slot_var_a: 2,
            slot_var_b: 3,
            legacy_var: 99,
            slot_skill_var: 4,
            legacy_skill_var: 42,
        }
    )

    reporter = reporting.ScheduleReporter(solver, cp_solver)
    df = reporter.generate_constraint_report()

    coverage_row = df[df["name"] == "coverage_constraints"].iloc[0]
    assert coverage_row["violation"] == pytest.approx(5)

    skill_row = df[df["name"] == "skill_constraints"].iloc[0]
    assert skill_row["violation"] == pytest.approx(4)

    csv_df = pd.read_csv(reporter.output_dir / "constraint_status.csv")
    csv_coverage = csv_df[csv_df["name"] == "coverage_constraints"].iloc[0]
    assert csv_coverage["violation"] == pytest.approx(5)

    csv_skill = csv_df[csv_df["name"] == "skill_constraints"].iloc[0]
    assert csv_skill["violation"] == pytest.approx(4)


def test_objective_breakdown_uses_slot_shortfalls(sample_environment):
    solver = sample_environment.solver
    base_solver = sample_environment.cp_solver

    baseline = solver.extract_objective_breakdown(base_solver)
    base_window_minutes = baseline["unmet_window"].get("minutes", 0)
    base_skill_minutes = baseline["unmet_skill"].get("minutes", 0)

    adaptive_data = getattr(solver, "adaptive_slot_data", None)
    slot_minutes = getattr(adaptive_data, "slot_minutes", {}) if adaptive_data is not None else {}
    assert slot_minutes, "expected slot minutes in adaptive data"

    slot_id, slot_duration = next(iter(slot_minutes.items()))

    slot_var = object()
    slot_skill_var = object()

    had_slot_attr = hasattr(solver, "slot_shortfall_vars")
    had_slot_skill_attr = hasattr(solver, "slot_skill_shortfall_vars")
    original_slot_vars = getattr(solver, "slot_shortfall_vars", None)
    original_slot_skill_vars = getattr(solver, "slot_skill_shortfall_vars", None)

    try:
        solver.slot_shortfall_vars = {slot_id: slot_var}
        solver.slot_skill_shortfall_vars = {(slot_id, "skillA"): slot_skill_var}

        overrides = {slot_var: 2, slot_skill_var: 3}
        cp_solver = DelegatingCpSolver(base_solver, overrides)

        updated = solver.extract_objective_breakdown(cp_solver)

        expected_window_minutes = base_window_minutes + overrides[slot_var] * slot_duration
        assert updated["unmet_window"]["minutes"] == expected_window_minutes
        expected_window_cost = expected_window_minutes * solver.objective_weights_minutes.get("unmet_window", 0.0)
        assert updated["unmet_window"]["cost"] == pytest.approx(expected_window_cost)

        expected_skill_minutes = base_skill_minutes + overrides[slot_skill_var] * slot_duration
        assert updated["unmet_skill"]["minutes"] == expected_skill_minutes
        expected_skill_cost = expected_skill_minutes * solver.objective_weights_minutes.get("unmet_skill", 0.0)
        assert updated["unmet_skill"]["cost"] == pytest.approx(expected_skill_cost)
    finally:
        if had_slot_attr:
            solver.slot_shortfall_vars = original_slot_vars
        elif hasattr(solver, "slot_shortfall_vars"):
            delattr(solver, "slot_shortfall_vars")

        if had_slot_skill_attr:
            solver.slot_skill_shortfall_vars = original_slot_skill_vars
        elif hasattr(solver, "slot_skill_shortfall_vars"):
            delattr(solver, "slot_skill_shortfall_vars")

def test_skill_coverage_report_uses_slot_shortfalls(sample_environment, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    solver = sample_environment.solver

    assert not solver.shifts.empty, "expected at least one shift"
    shift_id = solver.shifts.iloc[0]["shift_id"]
    assert shift_id, "missing shift identifier"
    shift_id_str = str(shift_id)

    assert solver.emp_skills, "expected employee skills"
    first_skill_set = next(iter(solver.emp_skills.values()))
    assert first_skill_set, "expected at least one skill"
    skill_name = next(iter(first_skill_set))

    solver.shift_skill_requirements = {shift_id_str: {str(skill_name): 1}}

    segments = solver.shift_to_covering_segments.get(shift_id_str, [])
    assert segments, "expected at least one covering segment"
    base_segment = segments[0]
    extra_segment = f"{base_segment}__extra"

    slot_var_a = object()
    slot_var_b = object()
    legacy_var = object()

    solver.slot_skill_shortfall_vars = {
        (base_segment, skill_name): slot_var_a,
        (extra_segment, skill_name): slot_var_b,
    }
    solver.skill_shortfall_vars = {(shift_id_str, str(skill_name)): legacy_var}

    if hasattr(solver, "adaptive_slot_data") and solver.adaptive_slot_data is not None:
        segment_owner = getattr(solver.adaptive_slot_data, "segment_owner", {})
        if segment_owner is not None:
            segment_owner[base_segment] = shift_id
            segment_owner[extra_segment] = shift_id

    solver.shift_to_covering_segments.setdefault(shift_id_str, []).append(extra_segment)

    cp_solver = DummyCpSolver({slot_var_a: 2, slot_var_b: 1, legacy_var: 99})

    coverage_df = solver.extract_skill_coverage_summary(cp_solver)
    assert not coverage_df.empty

    row = coverage_df[
        (coverage_df["shift_id"] == shift_id_str) & (coverage_df["skill"] == str(skill_name))
    ]
    assert not row.empty
    assert row.iloc[0]["shortfall"] == 3

    report_dir = tmp_path / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    output_path = report_dir / "skill_coverage_report.csv"
    coverage_df.to_csv(output_path, index=False)

    saved_df = pd.read_csv(output_path)
    saved_row = saved_df[
        (saved_df["shift_id"] == shift_id_str) & (saved_df["skill"] == str(skill_name))
    ]
    assert not saved_row.empty
    assert saved_row.iloc[0]["shortfall"] == 3


def test_skill_coverage_report_uses_segment_shortfalls(sample_environment, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    solver = sample_environment.solver

    assert not solver.shifts.empty, "expected at least one shift"
    shift_id = solver.shifts.iloc[0]["shift_id"]
    shift_id_str = str(shift_id)

    assert solver.emp_skills, "expected employee skills"
    first_skill_set = next(iter(solver.emp_skills.values()))
    assert first_skill_set, "expected at least one skill"
    skill_name = str(next(iter(first_skill_set)))

    solver.shift_skill_requirements = {shift_id_str: {skill_name: 1}}
    solver.slot_skill_shortfall_vars = {}
    solver.skill_shortfall_vars = {}

    segments = solver.shift_to_covering_segments.get(shift_id_str, [])
    assert segments, "expected at least one covering segment"
    segment_id = segments[0]

    segment_var = object()
    solver.segment_skill_shortfall_vars = {(segment_id, skill_name): segment_var}

    if hasattr(solver, "adaptive_slot_data") and solver.adaptive_slot_data is not None:
        segment_owner = getattr(solver.adaptive_slot_data, "segment_owner", None)
        if segment_owner is None:
            segment_owner = {}
            setattr(solver.adaptive_slot_data, "segment_owner", segment_owner)
        segment_owner[segment_id] = shift_id_str

    shift_duration = max(1, int(solver.duration_minutes.get(shift_id_str, 0) or 0))
    overrides = {segment_var: shift_duration}

    cp_solver = DelegatingCpSolver(sample_environment.cp_solver, overrides)

    coverage_df = solver.extract_skill_coverage_summary(cp_solver)
    assert not coverage_df.empty

    row = coverage_df[
        (coverage_df["shift_id"] == shift_id_str) & (coverage_df["skill"] == skill_name)
    ]
    assert not row.empty

    expected_shortfall = 1
    assert row.iloc[0]["shortfall"] == expected_shortfall

    report_dir = tmp_path / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    output_path = report_dir / "skill_coverage_report.csv"
    coverage_df.to_csv(output_path, index=False)

    saved_df = pd.read_csv(output_path)
    saved_row = saved_df[
        (saved_df["shift_id"] == shift_id_str) & (saved_df["skill"] == skill_name)
    ]
    assert not saved_row.empty
    assert saved_row.iloc[0]["shortfall"] == expected_shortfall


def test_skill_coverage_report_segment_only_requirements(sample_environment, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    solver = sample_environment.solver

    solver.shift_skill_requirements = {}
    solver.slot_skill_shortfall_vars = {}
    solver.skill_shortfall_vars = {}

    assert solver.segment_skill_shortfall_vars, "expected segment skill shortfall variables"
    assert solver.segment_skill_demands, "expected segment skill demands"

    (segment_id, skill_name), segment_var = next(iter(solver.segment_skill_shortfall_vars.items()))

    shift_id = None
    for candidate_shift, segments in solver.shift_to_covering_segments.items():
        if segment_id in segments:
            shift_id = candidate_shift
            break
    if shift_id is None and isinstance(segment_id, str) and "__" in segment_id:
        shift_id = segment_id.split("__", 1)[0]
    if shift_id is None:
        shift_id = segment_id

    shift_id_str = str(shift_id)

    duration = solver.duration_minutes.get(shift_id)
    if duration is None:
        duration = solver.duration_minutes.get(shift_id_str)
    duration = max(1, int(duration or 0))

    shortfall_minutes = duration
    overrides = {segment_var: shortfall_minutes}
    cp_solver = DelegatingCpSolver(sample_environment.cp_solver, overrides)

    coverage_df = solver.extract_skill_coverage_summary(cp_solver)
    assert not coverage_df.empty

    row = coverage_df[
        (coverage_df["shift_id"].astype(str) == shift_id_str)
        & (coverage_df["skill"] == str(skill_name))
    ]
    assert not row.empty

    demand_minutes = solver.segment_skill_demands.get((segment_id, skill_name), 0)
    expected_required = int(math.ceil(demand_minutes / duration)) if demand_minutes else 0
    expected_shortfall = int(math.ceil(shortfall_minutes / duration))

    assert row.iloc[0]["required"] == expected_required
    assert row.iloc[0]["shortfall"] == expected_shortfall

    report_dir = tmp_path / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    output_path = report_dir / "skill_coverage_report.csv"
    coverage_df.to_csv(output_path, index=False)

    saved_df = pd.read_csv(output_path)
    saved_row = saved_df[
        (saved_df["shift_id"].astype(str) == shift_id_str)
        & (saved_df["skill"] == str(skill_name))
    ]
    assert not saved_row.empty
    assert saved_row.iloc[0]["shortfall"] == expected_shortfall
