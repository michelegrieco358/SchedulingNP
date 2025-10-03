"""End-to-end test exercising the repository data pipeline."""
from __future__ import annotations

from pathlib import Path

from src import config_loader, model_cp

DATA_DIR = Path("data")
CONFIG_PATH = Path("config.yaml")
REQUIRED_CSV_FILES = [
    DATA_DIR / "employees.csv",
    DATA_DIR / "shifts.csv",
    DATA_DIR / "availability.csv",
    DATA_DIR / "preferences.csv",
    DATA_DIR / "overtime_costs.csv",
    DATA_DIR / "windows.csv",
]


def test_e2e_solver_runs_on_repository_data():
    # Ensure that the baseline repository artefacts are present.
    assert CONFIG_PATH.exists(), f"Missing configuration file: {CONFIG_PATH}"
    for csv_path in REQUIRED_CSV_FILES:
        assert csv_path.exists(), f"Missing required CSV file: {csv_path}"

    cfg = config_loader.load_config(CONFIG_PATH)

    (
        employees,
        shifts,
        _availability,
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
        windows_df,
    ) = model_cp._load_data(DATA_DIR, cfg.rest.min_between_shifts, cfg)

    penalties = {
        "unmet_window": cfg.penalties.unmet_window,
        "unmet_demand": cfg.penalties.unmet_demand,
        "unmet_skill": cfg.penalties.unmet_skill,
        "overstaff": cfg.penalties.overstaff,
        "overtime": cfg.penalties.overtime,
        "external_use": cfg.penalties.external_use,
        "fairness": cfg.penalties.fairness,
        "preferences": cfg.penalties.preferences,
    }
    objective_priority = list(cfg.objective.priority)
    objective_weights = model_cp._build_objective_weights(objective_priority, penalties)

    max_seconds = cfg.solver.time_limit_sec if cfg.solver.time_limit_sec is not None else 30.0

    solver_cfg = model_cp.SolverConfig(
        max_seconds=max_seconds,
        log_search_progress=False,
        global_min_rest_hours=cfg.rest.min_between_shifts,
        overtime_priority=objective_weights.get("overtime", 0),
        shortfall_priority=objective_weights.get("unmet_demand", 0),
        window_shortfall_priority=objective_weights.get("unmet_window", 0),
        skill_shortfall_priority=objective_weights.get("unmet_skill", 0),
        external_use_weight=objective_weights.get("external_use", 0),
        preferences_weight=objective_weights.get("preferences", 0),
        fairness_weight=objective_weights.get("fairness", 0),
        default_overtime_cost_weight=model_cp.DEFAULT_OVERTIME_COST_WEIGHT,
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

    status_name = cp_solver.StatusName()
    assert status_name in {"OPTIMAL", "FEASIBLE"}, f"Unexpected solver status: {status_name}"

    assignments = solver.extract_assignments(cp_solver)
    required_columns = {"employee_id", "shift_id"}
    assert required_columns.issubset(assignments.columns), "Assignments missing required columns"

    if not assignments.empty:
        assert assignments[list(required_columns)].notna().all().all()
