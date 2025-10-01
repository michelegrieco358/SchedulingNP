from __future__ import annotations

import json
import logging
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Optional, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_CANDIDATES = ("config.yaml", "config.yml", "config.json")
PRIORITY_KEYS = ("unmet_window", "unmet_demand", "unmet_skill", "unmet_shift", "overstaff", "overtime", "fairness", "preferences")


class HoursConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_weekly: float = Field(0, ge=0)
    max_weekly: float = Field(40, ge=0)
    max_daily: float = Field(8, ge=0)

    @model_validator(mode="after")
    def check_ranges(cls, values: "HoursConfig") -> "HoursConfig":
        if values.max_weekly < values.min_weekly:
            raise ValueError("max_weekly deve essere >= min_weekly")
        return values


class RestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_between_shifts: float = Field(8, ge=0)


class SkillsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enable_slack: bool = True
    skill_mode: str = Field("by_shift")

    @field_validator("skill_mode")
    @classmethod
    def validate_skill_mode(cls, value: str) -> str:
        modes = {"by_shift", "by_segment"}
        value = value.strip().lower()
        if value not in modes:
            raise ValueError(f"skill_mode deve essere uno tra {sorted(modes)}")
        return value

class WindowsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    midnight_policy: str = Field("split")
    warn_slots_threshold: int = Field(0, ge=0)
    hard_slots_threshold: int = Field(0, ge=0)

    @field_validator("midnight_policy")
    @classmethod
    def check_policy(cls, value: str) -> str:
        allowed = {"split", "extend"}  # o le opzioni che vuoi supportare
        value = value.strip().lower()
        if value not in allowed:
            raise ValueError(f"midnight_policy deve essere uno tra {sorted(allowed)}")
        return value



class ShiftsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    demand_mode: str = Field("headcount")

    @field_validator("demand_mode")
    @classmethod
    def validate_demand_mode(cls, value: str) -> str:
        modes = {"headcount", "person_minutes"}
        value = value.strip().lower()
        if value not in modes:
            raise ValueError(f"demand_mode deve essere uno tra {sorted(modes)}")
        return value


class PenaltiesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unmet_window: float = Field(2.0, ge=0)
    unmet_demand: float = Field(1.0, ge=0)
    unmet_skill: float = Field(0.8, ge=0)
    unmet_shift: float = Field(1.0, ge=0)
    overstaff: float = Field(0.15, ge=0)
    overtime: float = Field(0.30, ge=0)
    fairness: float = Field(0.05, ge=0)
    preferences: float = Field(0.33, ge=0)


class ObjectiveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: list[str] = Field(default_factory=lambda: list(PRIORITY_KEYS))

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, value: list[str]) -> list[str]:
        unknown = [item for item in value if item not in PRIORITY_KEYS]
        if unknown:
            raise ValueError(f"Chiavi priorita non riconosciute: {unknown} (valide: {PRIORITY_KEYS})")
        if len(set(value)) != len(value):
            raise ValueError("objective.priority non puo contenere duplicati")
        return value


class RandomConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: Optional[int] = Field(123, ge=0)


class SolverOptionsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_limit_sec: Optional[float] = Field(None, ge=0)
    mip_gap: Optional[float] = Field(None, gt=0)


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"

    @field_validator("level")
    @classmethod
    def normalize_level(cls, value: str) -> str:
        return value.upper()


class ReportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    output_dir: str = "reports"


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hours: HoursConfig = Field(default_factory=HoursConfig)
    rest: RestConfig = Field(default_factory=RestConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    windows: WindowsConfig = Field(default_factory=WindowsConfig)
    shifts: ShiftsConfig = Field(default_factory=ShiftsConfig)
    penalties: PenaltiesConfig = Field(default_factory=PenaltiesConfig)
    objective: ObjectiveConfig = Field(default_factory=ObjectiveConfig)
    random: RandomConfig = Field(default_factory=RandomConfig)
    solver: SolverOptionsConfig = Field(default_factory=SolverOptionsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    reports: ReportConfig = Field(default_factory=ReportConfig)


_DEFAULT_CONFIG = Config()
_DEFAULT_DICT = _DEFAULT_CONFIG.model_dump()


def load_config(path: Optional[str] = None) -> Config:
    """Load configuration from YAML/JSON, falling back to defaults."""
    raw_data: Dict[str, Any] = {}
    config_path: Optional[Path] = None

    if path is not None:
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"File di configurazione non trovato: {config_path}")
    else:
        for candidate in DEFAULT_CONFIG_CANDIDATES:
            candidate_path = Path(candidate)
            if candidate_path.exists():
                config_path = candidate_path
                break

    if config_path is not None:
        suffix = config_path.suffix.lower()
        try:
            if suffix in {".yaml", ".yml"}:
                with config_path.open("r", encoding="utf-8") as fh:
                    raw_data = yaml.safe_load(fh) or {}
            elif suffix == ".json":
                with config_path.open("r", encoding="utf-8") as fh:
                    raw_data = json.load(fh)
            else:
                raise ValueError(f"Formato configurazione non supportato: {config_path.suffix}")
        except (yaml.YAMLError, json.JSONDecodeError) as exc:
            raise ValueError(f"Errore nel parse della configurazione {config_path}: {exc}") from exc
        if not isinstance(raw_data, dict):
            raise ValueError("Il file di configurazione deve rappresentare un oggetto/dizionario")

    try:
        config = Config(**raw_data)
    except ValidationError as exc:
        raise ValueError(exc.errors()) from exc

    if config_path is not None and raw_data:
        _log_missing_keys(raw_data, config_path)
    elif config_path is None:
        logger.info("Configurazione esterna non trovata, uso i valori di default")

    return config


def config_to_dict(cfg: Config) -> Mapping[str, Any]:
    resolved = cfg.model_dump()
    return MappingProxyType(resolved)  # type: ignore[arg-type]


def _log_missing_keys(raw: Dict[str, Any], origin: Path) -> None:
    missing = sorted(_collect_missing(_DEFAULT_DICT, raw))
    if missing:
        logger.warning("Configurazione %s: valori mancanti, uso default per %s", origin, ", ".join(missing))


def _collect_missing(defaults: Dict[str, Any], provided: Dict[str, Any], prefix: str = "") -> list[str]:
    missing: list[str] = []
    for key, default_value in defaults.items():
        full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if key not in provided or provided[key] is None:
            missing.append(full_key)
            continue
        provided_value = provided[key]
        if isinstance(default_value, dict) and isinstance(provided_value, dict):
            missing.extend(_collect_missing(default_value, provided_value, full_key))
    return missing
