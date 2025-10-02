from __future__ import annotations

import csv
from pathlib import Path

from src import model_cp


EXPECTED_ASSIGNMENTS = {"employee_id": {"E1"}, "shift_id": {"S1"}}


def test_solver_handles_window_skills(sample_environment):
    solver = sample_environment.solver
    cp_solver = sample_environment.cp_solver

    assert solver.using_window_skills, "window skills should be detected"
    assert solver.window_skill_requirements == {"W1": {"skillA": 1}}
    assert not solver.using_shift_skills

    assert cp_solver.StatusName() == "OPTIMAL"

    assignments = solver.extract_assignments(cp_solver)
    assert not assignments.empty
    assert set(assignments["employee_id"]) == EXPECTED_ASSIGNMENTS["employee_id"]
    assert set(assignments["shift_id"]) == EXPECTED_ASSIGNMENTS["shift_id"]

    shortfall_df = solver.extract_shortfall_summary(cp_solver)
    assert shortfall_df.empty


def test_solver_excludes_unskilled_workers(sample_environment):
    data_dir = sample_environment.data_dir

    employees_csv = data_dir / "employees.csv"
    with employees_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "employee_id",
            "name",
            "roles",
            "max_week_hours",
            "min_rest_hours",
            "max_overtime_hours",
            "contracted_hours",
            "min_week_hours",
            "skills",
        ])
        writer.writerow(["E1", "Alice", "Nurse", "40", "11", "5", "", "0", "skillA"])
        writer.writerow(["E2", "Bob", "Nurse", "40", "11", "5", "", "0", ""])

    availability_csv = data_dir / "availability.csv"
    with availability_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["employee_id", "shift_id", "is_available"])
        writer.writerow(["E1", "S1", "1"])
        writer.writerow(["E2", "S1", "1"])

    cfg = sample_environment.cfg
    (
        employees,
        shifts,
        availability,
        assign_mask,
        rest_conflicts,
        overtime_costs,
        preferences,
        emp_skills,
        shift_skill_req,
        window_demand_map,
        window_duration_map,
        window_skill_req,
        adaptive_data,
    ) = model_cp._load_data(data_dir, cfg.rest.min_between_shifts, cfg)

    objective_priority = list(cfg.objective.priority)
    objective_weights = model_cp._build_objective_weights(
        objective_priority,
        {
            "unmet_window": cfg.penalties.unmet_window,
            "unmet_demand": cfg.penalties.unmet_demand,
            "unmet_skill": cfg.penalties.unmet_skill,
            "overstaff": cfg.penalties.overstaff,
            "overtime": cfg.penalties.overtime,
            "external_use": cfg.penalties.external_use,
            "preferences": cfg.penalties.preferences,
            "fairness": cfg.penalties.fairness,
        },
    )

    solver_cfg = model_cp.SolverConfig(
        max_seconds=10.0,
        log_search_progress=False,
        global_min_rest_hours=cfg.rest.min_between_shifts,
        overtime_priority=objective_weights.get("overtime", 0),
        shortfall_priority=objective_weights.get("unmet_demand", 0),
        window_shortfall_priority=objective_weights.get("unmet_window", 0),
        skill_shortfall_priority=objective_weights.get("unmet_skill", 0),
        external_use_weight=objective_weights.get("external_use", 0),
        preferences_weight=objective_weights.get("preferences", 0),
        fairness_weight=objective_weights.get("fairness", 0),
        default_overtime_cost_weight=objective_weights.get("overtime", 0),
        global_overtime_cap_minutes=None,
        random_seed=cfg.random.seed,
        mip_gap=cfg.solver.mip_gap,
        skills_slack_enabled=cfg.skills.enable_slack,
        objective_priority=tuple(objective_priority),
    )

    solver = model_cp.ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        rest_conflicts=rest_conflicts,
        overtime_costs=overtime_costs,
        preferences=preferences,
        emp_skills=emp_skills,
        shift_skill_requirements=shift_skill_req,
        window_skill_requirements=window_skill_req,
        window_demands=window_demand_map,
        window_duration_minutes=window_duration_map,
        config=solver_cfg,
        objective_priority=objective_priority,
        objective_weights=objective_weights,
        adaptive_slot_data=adaptive_data,
        global_hours=cfg.hours,
    )
    solver.demand_mode = cfg.shifts.demand_mode
    solver.build()
    cp_solver = solver.solve()

    assert cp_solver.StatusName() == "OPTIMAL"
    assignments = solver.extract_assignments(cp_solver)
    if not assignments.empty:
        assert "E2" not in set(assignments["employee_id"])
    for (emp_id, _), var in solver.assignment_vars.items():
        if emp_id == "E2":
            assert cp_solver.Value(var) == 0


def test_shift_skill_requirements_parsed_from_string(sample_environment):
    data_dir = sample_environment.data_dir

    shifts_csv = data_dir / "shifts.csv"
    with shifts_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "shift_id",
            "day",
            "start",
            "end",
            "role",
            "required_staff",
            "demand_id",
            "skills",
        ])
        writer.writerow(["S1", "2024-01-01", "08:00", "16:00", "Nurse", "1", "W1", "skillA:1"])

    cfg = sample_environment.cfg
    cfg.shifts.coverage_source = "shifts"
    cfg.skills.enable_slack = False

    (
        employees,
        shifts,
        availability,
        assign_mask,
        rest_conflicts,
        overtime_costs,
        preferences,
        emp_skills,
        shift_skill_req,
        window_demand_map,
        window_duration_map,
        window_skill_req,
        adaptive_data,
    ) = model_cp._load_data(data_dir, cfg.rest.min_between_shifts, cfg)

    assert shift_skill_req == {"S1": {"skillA": 1}}

    objective_priority = list(cfg.objective.priority)
    objective_weights = model_cp._build_objective_weights(
        objective_priority,
        {
            "unmet_window": cfg.penalties.unmet_window,
            "unmet_demand": cfg.penalties.unmet_demand,
            "unmet_skill": cfg.penalties.unmet_skill,
            "overstaff": cfg.penalties.overstaff,
            "overtime": cfg.penalties.overtime,
            "external_use": cfg.penalties.external_use,
            "preferences": cfg.penalties.preferences,
            "fairness": cfg.penalties.fairness,
        },
    )

    solver_cfg = model_cp.SolverConfig(
        max_seconds=10.0,
        log_search_progress=False,
        global_min_rest_hours=cfg.rest.min_between_shifts,
        overtime_priority=objective_weights.get("overtime", 0),
        shortfall_priority=objective_weights.get("unmet_demand", 0),
        window_shortfall_priority=objective_weights.get("unmet_window", 0),
        skill_shortfall_priority=objective_weights.get("unmet_skill", 0),
        external_use_weight=objective_weights.get("external_use", 0),
        preferences_weight=objective_weights.get("preferences", 0),
        fairness_weight=objective_weights.get("fairness", 0),
        default_overtime_cost_weight=objective_weights.get("overtime", 0),
        global_overtime_cap_minutes=None,
        random_seed=cfg.random.seed,
        mip_gap=cfg.solver.mip_gap,
        skills_slack_enabled=cfg.skills.enable_slack,
        objective_priority=tuple(objective_priority),
    )

    solver = model_cp.ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        rest_conflicts=rest_conflicts,
        overtime_costs=overtime_costs,
        preferences=preferences,
        emp_skills=emp_skills,
        shift_skill_requirements=shift_skill_req,
        window_skill_requirements=window_skill_req,
        window_demands=window_demand_map,
        window_duration_minutes=window_duration_map,
        config=solver_cfg,
        objective_priority=objective_priority,
        objective_weights=objective_weights,
        adaptive_slot_data=adaptive_data,
        global_hours=cfg.hours,
    )

    solver.demand_mode = cfg.shifts.demand_mode
    solver.build()
    cp_solver = solver.solve()

    assert cp_solver.StatusName() == "OPTIMAL"
    assert solver.using_shift_skills is True
    assert solver.using_window_skills is False

    assignments = solver.extract_assignments(cp_solver)
    assert not assignments.empty
    assert set(assignments["employee_id"]) == {"E1"}
