from __future__ import annotations

import datetime as dt

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
                "segment_id": "seg-1",
                "start_time": "09:00",
                "end_time": "10:00",
                "demand": 2,
                "assigned": 0,
                "shortfall": 2,
                "overstaffing": 0,
            }
        ]
    )

    reporter.assignments_df = pd.DataFrame(
        [
            {
                "employee": "emp-1",
                "day": "2024-01-01",
                "start_dt": pd.Timestamp("2024-01-01 09:00"),
                "end_dt": pd.Timestamp("2024-01-01 10:00"),
            }
        ]
    )

    reporter.windows_df = pd.DataFrame(
        [
            {
                "window_id": "win-1",
                "day": "2024-01-01",
                "window_start": dt.time(9, 0),
                "window_end": dt.time(10, 0),
                "window_start_min": 9 * 60,
                "window_end_min": 10 * 60,
                "window_demand": 2,
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
