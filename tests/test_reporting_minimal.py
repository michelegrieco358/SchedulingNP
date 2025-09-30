from __future__ import annotations

from src import reporting
import pytest


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
