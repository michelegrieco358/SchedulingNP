import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import config_loader  # noqa: E402


def test_load_config_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = config_loader.load_config()
    assert cfg.hours.max_daily == 8
    assert cfg.rest.min_between_shifts == 8
    assert cfg.penalties.unmet_window == 2.0
    assert cfg.penalties.unmet_demand == 1.0
    assert cfg.penalties.unmet_skill == 0.8
    assert cfg.penalties.unmet_shift == 0.6
    assert cfg.skills.enable_slack is True
    assert list(cfg.objective.priority) == list(config_loader.PRIORITY_KEYS)


def test_load_config_partial(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    cfg_path = tmp_path / "config.yaml"
    yaml.safe_dump({"penalties": {"overtime": 7}}, cfg_path.open("w", encoding="utf-8"))

    caplog.set_level("WARNING")
    cfg = config_loader.load_config(None)

    assert cfg.penalties.overtime == 7
    assert cfg.penalties.unmet_window == 2.0
    assert cfg.penalties.unmet_demand == 1.0  # default
    assert cfg.penalties.unmet_skill == 0.8
    assert cfg.penalties.unmet_shift == 0.6
    assert any("valori mancanti" in record.message for record in caplog.records)


def test_load_config_invalid_type(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg_path = tmp_path / "config.yaml"
    yaml.safe_dump({"penalties": {"unmet_demand": "bad"}}, cfg_path.open("w", encoding="utf-8"))

    with pytest.raises(ValueError):
        config_loader.load_config()


def test_load_config_invalid_hours(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg_path = tmp_path / "config.yaml"
    yaml.safe_dump({"hours": {"min_weekly": 50, "max_weekly": 40}}, cfg_path.open("w", encoding="utf-8"))

    with pytest.raises(ValueError):
        config_loader.load_config()


def test_load_config_invalid_priority(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg_path = tmp_path / "config.yaml"
    yaml.safe_dump({"objective": {"priority": ["unmet_demand", "unknown"]}}, cfg_path.open("w", encoding="utf-8"))

    with pytest.raises(ValueError):
        config_loader.load_config()
