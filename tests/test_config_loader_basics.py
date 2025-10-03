from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src import config_loader


def test_load_config_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = config_loader.load_config()

    assert cfg.hours.max_weekly == 40
    assert cfg.skills.enable_slack is True
    assert cfg.objective.priority[0] == "unmet_window"
    assert list(cfg.objective.priority) == list(config_loader.PRIORITY_KEYS)


def test_load_config_custom(tmp_path: Path) -> None:
    cfg_path = tmp_path / "custom.yaml"
    yaml.safe_dump(
        {
            "hours": {"max_weekly": 50},
            "rest": {"min_between_shifts": 10},
            "skills": {"enable_slack": False},
            "logging": {"level": "debug"},
        },
        cfg_path.open("w", encoding="utf-8"),
    )

    cfg = config_loader.load_config(str(cfg_path))
    assert cfg.hours.max_weekly == 50
    assert cfg.rest.min_between_shifts == 10
    assert cfg.skills.enable_slack is False
    assert cfg.logging.level == "DEBUG"


def test_objective_mode_validation() -> None:
    cfg = config_loader.ObjectiveConfig()
    assert cfg.mode == "weighted"
    assert config_loader.ObjectiveConfig(mode="LEX").mode == "lex"
    with pytest.raises(ValueError):
        config_loader.ObjectiveConfig(mode="invalid")
