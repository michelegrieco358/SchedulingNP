from __future__ import annotations

import pytest

from src import config_loader
from tests.conftest import build_solver_from_data


def test_objective_weights_reflect_config(sample_data_dir) -> None:
    cfg = config_loader.Config()
    cfg.penalties.fairness = 2.4

    env = build_solver_from_data(sample_data_dir, cfg)

    assert "fairness" in env.solver.objective_weights_minutes
    expected = cfg.penalties.fairness / 60.0
    assert env.solver.objective_weights_minutes["fairness"] == pytest.approx(expected)


def test_objective_weight_zero_penalty_removed(sample_data_dir) -> None:
    cfg = config_loader.Config()
    cfg.penalties.overtime = 0.0

    env = build_solver_from_data(sample_data_dir, cfg)

    assert env.solver.objective_weights_minutes.get("overtime", 0.0) == 0.0

