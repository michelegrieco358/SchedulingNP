from __future__ import annotations

from pathlib import Path

import pytest

from src import config_loader, model_cp
from tests.conftest import _write_csv


def _build_overstaff_dataset(base_dir: Path, window_demand: int = 0) -> Path:
    data_dir = base_dir / 'data'

    _write_csv(
        data_dir / 'employees.csv',
        [
            [
                'employee_id',
                'name',
                'roles',
                'max_week_hours',
                'min_rest_hours',
                'max_overtime_hours',
                'contracted_hours',
                'min_week_hours',
                'skills',
            ],
            ['E1', 'Alice', 'Nurse', '40', '11', '5', '', '0', ''],
            ['E2', 'Bob', 'Nurse', '40', '11', '5', '', '0', ''],
        ],
    )

    _write_csv(
        data_dir / 'shifts.csv',
        [
            ['shift_id', 'day', 'start', 'end', 'role', 'required_staff', 'demand_id'],
            ['S1', '2024-01-01', '08:00', '16:00', 'Nurse', '1', 'W1'],
        ],
    )

    _write_csv(
        data_dir / 'availability.csv',
        [
            ['employee_id', 'shift_id', 'is_available'],
            ['E1', 'S1', '1'],
            ['E2', 'S1', '1'],
        ],
    )

    _write_csv(
        data_dir / 'windows.csv',
        [
            ['window_id', 'day', 'window_start', 'window_end', 'role', 'window_demand', 'skills'],
            ['W1', '2024-01-01', '08:00', '16:00', 'Nurse', str(window_demand), ''],
        ],
    )

    _write_csv(
        data_dir / 'overtime_costs.csv',
        [['role', 'overtime_cost_per_hour'], ['Nurse', '20']],
    )

    _write_csv(
        data_dir / 'time_off.csv',
        [['employee_id', 'off_start', 'off_end']],
    )

    _write_csv(
        data_dir / 'preferences.csv',
        [['employee_id', 'shift_id', 'score']],
    )

    return data_dir


def test_shift_overstaff_penalty(tmp_path: Path) -> None:
    data_dir = _build_overstaff_dataset(tmp_path)

    cfg = config_loader.Config()
    cfg.penalties.overstaff = 0.3
    cfg.penalties.external_use = 0.0

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
        windows_df,
    ) = model_cp._load_data(data_dir, cfg.rest.min_between_shifts, cfg)

    penalties = {
        'unmet_window': cfg.penalties.unmet_window,
        'unmet_demand': cfg.penalties.unmet_demand,
        'unmet_skill': cfg.penalties.unmet_skill,
        'overstaff': cfg.penalties.overstaff,
        'overtime': cfg.penalties.overtime,
        'external_use': cfg.penalties.external_use,
        'preferences': cfg.penalties.preferences,
        'fairness': cfg.penalties.fairness,
    }

    objective_priority = list(cfg.objective.priority)
    objective_weights = model_cp._build_objective_weights(objective_priority, penalties)

    solver_cfg = model_cp.SolverConfig(
        max_seconds=10.0,
        log_search_progress=False,
        global_min_rest_hours=cfg.rest.min_between_shifts,
        overtime_priority=objective_weights.get('overtime', 0),
        shortfall_priority=objective_weights.get('unmet_demand', 0),
        window_shortfall_priority=objective_weights.get('unmet_window', 0),
        skill_shortfall_priority=objective_weights.get('unmet_skill', 0),
        external_use_weight=objective_weights.get('external_use', 0),
        preferences_weight=objective_weights.get('preferences', 0),
        fairness_weight=objective_weights.get('fairness', 0),
        default_overtime_cost_weight=objective_weights.get('overtime', 0),
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

    solver.build()
    solver.model.Add(solver.shift_aggregate_vars['S1'] == 2)
    cp_solver = solver.solve()

    assert cp_solver.StatusName() == 'OPTIMAL'
    assert 'S1' in solver.shift_overstaff_vars

    overstaff_val = cp_solver.Value(solver.shift_overstaff_vars['S1'])
    assert overstaff_val == 1

    assignments = solver.extract_assignments(cp_solver)
    assert len(assignments) == 2

    breakdown = solver.extract_objective_breakdown(cp_solver)
    overstaff_metrics = breakdown['overstaff']
    duration = solver.duration_minutes['S1']
    assert overstaff_metrics['minutes'] == duration
    expected_cost = duration * solver.objective_weights_minutes.get('overstaff', 0.0)
    assert overstaff_metrics['cost'] == pytest.approx(expected_cost)


def test_window_segment_overstaff_penalty(tmp_path: Path) -> None:
    data_dir = _build_overstaff_dataset(tmp_path, window_demand=1)

    cfg = config_loader.Config()
    cfg.penalties.overstaff = 0.3
    cfg.penalties.external_use = 0.0

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
        windows_df,
    ) = model_cp._load_data(data_dir, cfg.rest.min_between_shifts, cfg)

    penalties = {
        'unmet_window': cfg.penalties.unmet_window,
        'unmet_demand': cfg.penalties.unmet_demand,
        'unmet_skill': cfg.penalties.unmet_skill,
        'overstaff': cfg.penalties.overstaff,
        'overtime': cfg.penalties.overtime,
        'external_use': cfg.penalties.external_use,
        'preferences': cfg.penalties.preferences,
        'fairness': cfg.penalties.fairness,
    }

    objective_priority = list(cfg.objective.priority)
    objective_weights = model_cp._build_objective_weights(objective_priority, penalties)

    solver_cfg = model_cp.SolverConfig(
        max_seconds=10.0,
        log_search_progress=False,
        global_min_rest_hours=cfg.rest.min_between_shifts,
        overtime_priority=objective_weights.get('overtime', 0),
        shortfall_priority=objective_weights.get('unmet_demand', 0),
        window_shortfall_priority=objective_weights.get('unmet_window', 0),
        skill_shortfall_priority=objective_weights.get('unmet_skill', 0),
        external_use_weight=objective_weights.get('external_use', 0),
        preferences_weight=objective_weights.get('preferences', 0),
        fairness_weight=objective_weights.get('fairness', 0),
        default_overtime_cost_weight=objective_weights.get('overtime', 0),
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

    solver.build()
    solver.model.Add(solver.shift_aggregate_vars['S1'] == 2)
    assert solver.using_window_demands

    cp_solver = solver.solve()
    assert cp_solver.StatusName() == 'OPTIMAL'

    assert solver.segment_overstaff_vars, 'segment overstaff vars should exist'
    seg_id, seg_var = next(iter(solver.segment_overstaff_vars.items()))
    overstaff_value = cp_solver.Value(seg_var)
    segment_duration = solver._get_segment_duration_minutes(seg_id)
    assert overstaff_value == segment_duration

    if solver.shift_overstaff_vars:
        for var in solver.shift_overstaff_vars.values():
            assert cp_solver.Value(var) == 0

    assignments = solver.extract_assignments(cp_solver)
    assert len(assignments) == 2

    breakdown = solver.extract_objective_breakdown(cp_solver)
    overstaff_metrics = breakdown['overstaff']
    assert overstaff_metrics['minutes'] == segment_duration
    expected_cost = segment_duration * solver.objective_weights_minutes.get('overstaff', 0.0)
    assert overstaff_metrics['cost'] == pytest.approx(expected_cost)
