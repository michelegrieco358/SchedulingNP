"""CP-SAT shift scheduling model (MVP).

Questo modulo definisce lo scheletro del solver basato su OR-Tools.
Il modello prende in ingresso i DataFrame risultanti da loader.precompute.
"""
from __future__ import annotations

import argparse
import logging
import warnings
from datetime import datetime, timedelta
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple, Optional, Mapping, Sequence

import pandas as pd
from ortools.sat.python import cp_model

try:
    from . import loader, precompute, config_loader
except ImportError:  # fallback when running as a script
    import loader  # type: ignore
    import precompute  # type: ignore
    import config_loader  # type: ignore

DEFAULT_GLOBAL_MIN_REST_HOURS = 8.0
OBJECTIVE_MINUTE_SCALE = 6000
OVERTIME_COST_SCALE = 100


def _weight_per_hour_to_minutes(weight_hour: float | int | None) -> float:
    """Converte peso da persona-ora a persona-minuto per obiettivo unificato."""
    if weight_hour is None:
        return 0.0
    value = float(weight_hour)
    if value <= 0:
        return 0.0
    # Conversione diretta: peso_per_minuto = peso_per_ora / 60
    return value / 60.0



BASE_WINDOW_WEIGHT_H = 2.0
BASE_SHIFT_WEIGHT_H = 1.0
BASE_SKILL_WEIGHT_H = 0.8
BASE_OVERSTAFF_WEIGHT_H = 0.15
BASE_OVERTIME_WEIGHT_H = 0.3
BASE_FAIRNESS_WEIGHT_H = 0.05
BASE_PREFERENCES_WEIGHT_H = 0.05
BASE_EXTERNAL_USE_WEIGHT_H = 0.7

DEFAULT_WINDOW_SHORTFALL_PRIORITY = int(BASE_WINDOW_WEIGHT_H * 100)
DEFAULT_SHORTFALL_PRIORITY = int(BASE_SHIFT_WEIGHT_H * 100)
DEFAULT_SKILL_SHORTFALL_PRIORITY = int(BASE_SKILL_WEIGHT_H * 100)
DEFAULT_OVERSTAFF_PRIORITY = int(BASE_OVERSTAFF_WEIGHT_H * 100)
DEFAULT_OVERTIME_PRIORITY = int(BASE_OVERTIME_WEIGHT_H * 100)
DEFAULT_EXTERNAL_USE_WEIGHT = int(BASE_EXTERNAL_USE_WEIGHT_H * 100)
DEFAULT_FAIRNESS_WEIGHT = int(BASE_FAIRNESS_WEIGHT_H * 100)
DEFAULT_PREFERENCES_WEIGHT = int(BASE_PREFERENCES_WEIGHT_H * 100)
DEFAULT_OVERTIME_COST_WEIGHT = int(BASE_OVERTIME_WEIGHT_H * 100)
DEFAULT_GLOBAL_OVERTIME_CAP_MINUTES = None
DEFAULT_OBJECTIVE_PRIORITY = tuple(config_loader.PRIORITY_KEYS)

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _build_objective_weights(priority: Sequence[str], penalties: Mapping[str, float | int]) -> dict[str, int]:
    weights: dict[str, int] = {}
    for key in priority:
        penalty = penalties.get(key)
        if penalty is not None and penalty > 0:
            weight = int(float(penalty) * 100)
            weights[key] = weight
    for key, penalty in penalties.items():
        if key in weights:
            continue
        if penalty is not None and penalty > 0:
            weight = int(float(penalty) * 100)
            weights[key] = weight
    return weights



@dataclass
class SolverConfig:
    """Parametri base per il solver CP-SAT."""
    max_seconds: float | None = 30.0
    log_search_progress: bool = False
    global_min_rest_hours: float = DEFAULT_GLOBAL_MIN_REST_HOURS
    overtime_priority: int = DEFAULT_OVERTIME_PRIORITY
    shortfall_priority: int = DEFAULT_SHORTFALL_PRIORITY
    window_shortfall_priority: int = DEFAULT_WINDOW_SHORTFALL_PRIORITY
    skill_shortfall_priority: int = DEFAULT_SKILL_SHORTFALL_PRIORITY
    external_use_weight: int = DEFAULT_EXTERNAL_USE_WEIGHT
    preferences_weight: int = DEFAULT_PREFERENCES_WEIGHT
    fairness_weight: int = DEFAULT_FAIRNESS_WEIGHT
    default_overtime_cost_weight: int = DEFAULT_OVERTIME_COST_WEIGHT
    global_overtime_cap_minutes: int | None = None
    random_seed: int | None = None
    mip_gap: float | None = None
    skills_slack_enabled: bool = True
    objective_priority: tuple[str, ...] = DEFAULT_OBJECTIVE_PRIORITY
    objective_mode: str = "weighted"


class ShiftSchedulingCpSolver:
    """Costruisce e risolve il modello CP-SAT per l'MVP."""
    def __init__(
        self,
        employees: pd.DataFrame,
        shifts: pd.DataFrame,
        assign_mask: pd.DataFrame,
        rest_conflicts: pd.DataFrame | None = None,
        overtime_costs: pd.DataFrame | None = None,
        preferences: pd.DataFrame | None = None,
        emp_skills: Mapping[str, set[str]] | None = None,
        shift_skill_requirements: Mapping[str, Mapping[str, int]] | None = None,
        window_skill_requirements: Mapping[str, Mapping[str, int]] | None = None,
        window_demands: Mapping[str, int] | None = None,
        window_duration_minutes: Mapping[str, int] | None = None,
        config: SolverConfig | None = None,
        objective_priority: Sequence[str] | None = None,
        objective_weights: Mapping[str, int] | None = None,
        # Parametri per segmentazione temporale
        adaptive_slot_data: object | None = None,
        global_hours: object | None = None,
    ) -> None:
        self.employees = employees
        self.shifts = shifts
        self.assign_mask = assign_mask
        self.rest_conflicts = rest_conflicts
        self.overtime_costs = overtime_costs
        self.preferences = preferences
        self.config = config or SolverConfig()
        mode_value = str(getattr(self.config, "objective_mode", "weighted")).strip().lower()
        if mode_value not in {"weighted", "lex"}:
            mode_value = "weighted"
        self.config.objective_mode = mode_value
        self.objective_mode = mode_value
        self.global_hours = global_hours
        self.global_min_weekly = float(getattr(global_hours, "min_weekly", 0.0)) if global_hours is not None else 0.0
        max_weekly_value = getattr(global_hours, "max_weekly", None) if global_hours is not None else None
        self.global_max_weekly = float(max_weekly_value) if max_weekly_value is not None else None
        max_daily_value = getattr(global_hours, "max_daily", None) if global_hours is not None else None
        if max_daily_value is not None:
            max_daily_float = float(max_daily_value)
            if math.isfinite(max_daily_float):
                self.global_max_daily = max_daily_float
                self.global_max_daily_minutes = max(0, int(round(max_daily_float * 60)))
            else:
                self.global_max_daily = None
                self.global_max_daily_minutes = None
        else:
            self.global_max_daily = None
            self.global_max_daily_minutes = None

        raw_max_overtime = getattr(global_hours, "max_overtime", 0.0) if global_hours is not None else 0.0
        try:
            max_ot_float = float(raw_max_overtime)
        except (TypeError, ValueError):
            max_ot_float = 0.0
        if not math.isfinite(max_ot_float) or max_ot_float < 0:
            max_ot_float = 0.0
        self.global_max_overtime_hours = max_ot_float

        self.emp_skills = {str(emp_id): set(skills) for emp_id, skills in (emp_skills or {}).items()}
        valid_shift_ids = set(self.shifts["shift_id"].astype(str)) if "shift_id" in self.shifts.columns else set()

        cleaned_shift_skills: dict[str, dict[str, int]] = {}
        for shift_id, requirements in (shift_skill_requirements or {}).items():
            sid = str(shift_id)
            if valid_shift_ids and sid not in valid_shift_ids:
                continue
            if not isinstance(requirements, Mapping):
                continue
            cleaned_req: dict[str, int] = {}
            for skill_name, quantity in requirements.items():
                try:
                    qty_int = int(quantity)
                except (TypeError, ValueError):
                    logger.warning(
                        "Shift {}: requisito skill '{}' non numerico ({}), ignoro".format(
                            sid,
                            skill_name,
                            quantity,
                        )
                    )
                    continue
                if qty_int <= 0:
                    continue
                cleaned_req[str(skill_name)] = qty_int
            if cleaned_req:
                cleaned_shift_skills[sid] = cleaned_req
        self.shift_skill_requirements = cleaned_shift_skills

        cleaned_window_skills: dict[str, dict[str, int]] = {}
        for window_id, requirements in (window_skill_requirements or {}).items():
            if not isinstance(requirements, Mapping):
                continue
            cleaned_req: dict[str, int] = {}
            wid = str(window_id)
            for skill_name, quantity in requirements.items():
                try:
                    qty_int = int(quantity)
                except (TypeError, ValueError):
                    logger.warning(
                        "Finestra {}: requisito skill '{}' non numerico ({}), ignoro".format(
                            wid,
                            skill_name,
                            quantity,
                        )
                    )
                    continue
                if qty_int <= 0:
                    continue
                cleaned_req[str(skill_name)] = qty_int
            if cleaned_req:
                cleaned_window_skills[wid] = cleaned_req
        self.window_skill_requirements = cleaned_window_skills

        self.using_window_skills = bool(self.window_skill_requirements)
        self.using_shift_skills = bool(self.shift_skill_requirements) and not self.using_window_skills
        if self.using_window_skills and self.shift_skill_requirements:
            logger.info(
                "Requisiti skill definiti sulle finestre rilevati: ignoro quelli definiti sui turni."
            )

        self.window_demands = {}
        if window_demands:
            for window_id, demand in window_demands.items():
                demand_int = int(demand)
                if demand_int > 0:
                    self.window_demands[str(window_id)] = demand_int
        window_duration_src = window_duration_minutes or {}
        self.window_duration_minutes = {
            str(window_id): max(1, int(duration))
            for window_id, duration in window_duration_src.items()
            if int(duration) > 0
        }
        for window_id in self.window_demands:
            self.window_duration_minutes.setdefault(window_id, 60)
        self.using_window_demands = bool(self.window_demands)

        priority = list(objective_priority) if objective_priority is not None else list(self.config.objective_priority)
        self.objective_priority = priority
        base_weights = {
            "unmet_window": self.config.window_shortfall_priority,
            "unmet_demand": self.config.shortfall_priority,
            "unmet_skill": self.config.skill_shortfall_priority,
            "overtime": self.config.overtime_priority,
            "external_use": getattr(self.config, "external_use_weight", DEFAULT_EXTERNAL_USE_WEIGHT),
            "preferences": self.config.preferences_weight,
            "fairness": self.config.fairness_weight,
        }
        if objective_weights:
            base_weights.update(objective_weights)
        self.objective_weights = base_weights
        self.config.window_shortfall_priority = base_weights.get("unmet_window", self.config.window_shortfall_priority)
        self.config.shortfall_priority = base_weights.get("unmet_demand", self.config.shortfall_priority)
        self.config.skill_shortfall_priority = base_weights.get("unmet_skill", self.config.skill_shortfall_priority)
        self.config.external_use_weight = base_weights.get("external_use", getattr(self.config, "external_use_weight", DEFAULT_EXTERNAL_USE_WEIGHT))
        self.config.preferences_weight = base_weights.get("preferences", self.config.preferences_weight)
        self.config.fairness_weight = base_weights.get("fairness", self.config.fairness_weight)
        self.config.skills_slack_enabled = bool(self.config.skills_slack_enabled)
        self.config.objective_priority = tuple(priority)
        self._objective_priority_map: dict[str, tuple[cp_model.LinearExpr, bool]] | None = None

        self.model = cp_model.CpModel()
        self.assignment_vars: Dict[Tuple[str, str], cp_model.IntVar] = {}
        self.shift_aggregate_vars: Dict[str, cp_model.IntVar] = {}  # y[s] = sum_e x[e,s]
        self.duration_minutes: Dict[str, int] = {}
        self.total_required_minutes: int = 0
        self.workload_dev_vars: list[cp_model.IntVar] = []
        self._vars_by_shift: dict[str, list[cp_model.BoolVar]] = {}
        self._vars_by_shift_emp: dict[str, list[tuple[str, cp_model.BoolVar]]] = {}
        self._vars_by_emp: dict[str, list[tuple[str, cp_model.BoolVar]]] = {}
        self.shortfall_vars: dict[str, cp_model.IntVar] = {}
        self.shift_overstaff_vars: dict[str, cp_model.IntVar] = {}
        self.window_shortfall_vars: dict[str, cp_model.IntVar] = {}
        self.window_overstaff_vars: dict[str, cp_model.IntVar] = {}
        self.segment_overstaff_vars: dict[str, cp_model.IntVar] = {}
        self.skill_shortfall_vars: dict[Tuple[str, str], cp_model.IntVar] = {}
        self.overtime_vars: dict[str, cp_model.IntVar] = {}
        self.overtime_cost_weights: dict[str, int] = {}
        self.external_worker_usage_vars: dict[str, cp_model.BoolVar] = {}  # Variabili binarie per risorse esterne
        self.external_minutes_vars: dict[str, cp_model.IntVar] = {}
        self.overtime_limits: dict[str, int] = {}
        self.total_possible_overtime_minutes: int = 0
        self.preference_score_by_pair: Dict[Tuple[str, str], int] = {}
        self.global_overtime_cap_minutes: int | None = self.config.global_overtime_cap_minutes
        self.random_seed: int | None = self.config.random_seed
        self.mip_gap: float | None = self.config.mip_gap
        self.avg_shift_minutes: int = 60
        self.min_overtime_cost_per_hour: float | None = None
        self.role_overtime_costs: dict[str, float] = {}
        self.skill_slack_enabled: bool = bool(self.config.skills_slack_enabled)
        self._night_shift_ids: set[str] = set()
        if overtime_costs is not None and not overtime_costs.empty:
            self.role_overtime_costs = dict(zip(overtime_costs["role"], overtime_costs["overtime_cost_per_hour"]))
            positives = [float(val) for val in self.role_overtime_costs.values() if float(val) > 0]
            if positives:
                self.min_overtime_cost_per_hour = min(positives)
        else:
            self.role_overtime_costs = {}

        if preferences is not None and hasattr(preferences, "empty") and not preferences.empty:
            self.preference_score_by_pair = {
                (str(row["employee_id"]), str(row["shift_id"])): int(row["score"])
                for _, row in preferences.iterrows()
            }
        else:
            self.preference_score_by_pair = {}

        # Parametri per segmentazione temporale
        self.adaptive_slot_data = adaptive_slot_data
        self.window_bounds: dict[str, tuple] = {}
        self.slot_windows: dict[str, list[tuple[str, int]]] = {}
        if adaptive_slot_data is not None:
            self.window_bounds = getattr(adaptive_slot_data, "window_bounds", {}) or {}
            self.slot_windows = getattr(adaptive_slot_data, "slot_windows", {}) or {}
        
        # ModalitÃƒÂ  interpretazione domanda
        self.demand_mode = "headcount"  # Default, sarÃƒÂ  aggiornato dal config
        
        # Variabili per vincoli di segmenti con turni interi
        self.segment_shortfall_vars: dict[str, cp_model.IntVar] = {}  # short_segment[seg_id]
        self.shift_to_covering_segments: dict[str, list[str]] = {}  # Mappa turno -> segmenti che copre
        self.segment_demands: dict[str, int] = {}  # Domanda per segmento (headcount o persona-minuti)
        self.segment_skill_demands: dict[tuple[str, str], int] = {}  # Domanda skill per segmento
        self.segment_skill_shortfall_vars: dict[tuple[str, str], cp_model.IntVar] = {}

        # Pesi obiettivo in persona-minuti (conversione da persona-ora)
        self.objective_weights_minutes: dict[str, float] = {}
        self.mean_shift_minutes: int = 60  # Media durate turni per preferenze

    def build(self) -> None:
        """Costruisce variabili e vincoli base (placeholder)."""
        self._objective_priority_map = None
        self._build_assignment_variables()
        self._build_shift_aggregate_variables()
        # Copertura su turni attiva solo quando non usiamo la domanda per finestre
        self.shortfall_vars = {}
        if not self.using_window_demands:
            self._add_shift_coverage_constraints()
        else:
            if "required_staff" in self.shifts.columns:
                required_total = pd.to_numeric(self.shifts["required_staff"], errors="coerce").fillna(0).astype(int).sum()
                if required_total > 0:
                    logger.info("Ignoro required_staff dei turni perchÃ¯Â¿Â½ Ã¯Â¿Â½ attiva la domanda da windows")
        self._add_skill_coverage_constraints()
        
        # MODALITÃƒâ‚¬ UNICA: sempre turni interi con segmentazione
        # - Usa solo vincoli basati su segmenti temporali
        # - Ottimizza sui turni interi per coprire la domanda di ogni segmento
        self._add_segment_coverage_constraints()
        self._add_segment_skill_constraints()
        self.duration_minutes = self._compute_shift_duration_minutes()
        if self.duration_minutes:
            avg_minutes = sum(self.duration_minutes.values()) / len(self.duration_minutes)
            self.avg_shift_minutes = max(1, int(round(avg_minutes)))
        else:
            self.avg_shift_minutes = 60
        self.total_required_minutes = self._compute_total_required_minutes()
        
        self._initialize_objective_weights_minutes()
        self.mean_shift_minutes = self.avg_shift_minutes  # Per preferenze
        self._add_employee_max_hours_constraints()
        self._add_employee_daily_max_constraints()
        self._add_one_shift_per_day_constraints()
        self._add_night_shift_constraints()
        self._add_rest_conflict_constraints()
        self._add_min_rest_constraints()
        self._set_objective()

    def _build_assignment_variables(self) -> None:
        """Crea una variabile binaria per ogni coppia assegnabile employee/shift."""
        self.assignment_vars.clear()
        eligible = self.assign_mask[self.assign_mask["can_assign"] == 1]
        for _, row in eligible.iterrows():
            key = (row["employee_id"], row["shift_id"])
            var = self.model.NewBoolVar(f"assign__{row['employee_id']}__{row['shift_id']}")
            self.assignment_vars[key] = var

        self._vars_by_shift = {}
        self._vars_by_shift_emp = {}
        self._vars_by_emp = {}
        for (emp_id, shift_id), var in self.assignment_vars.items():
            self._vars_by_shift.setdefault(shift_id, []).append(var)
            self._vars_by_shift_emp.setdefault(shift_id, []).append((emp_id, var))
            self._vars_by_emp.setdefault(emp_id, []).append((shift_id, var))

    def _build_shift_aggregate_variables(self) -> None:
        """Crea variabili aggregate y[s] = sum_e x[e,s] per ogni turno."""
        self.shift_aggregate_vars.clear()
        
        for _, shift_row in self.shifts.iterrows():
            shift_id = shift_row["shift_id"]
            vars_for_shift = self._vars_by_shift.get(shift_id, [])
            
            # Upper bound: numero di dipendenti eleggibili per questo turno
            ub_s = len(vars_for_shift)
            
            # Crea variabile aggregata y[s]
            y_var = self.model.NewIntVar(0, ub_s, f"y__{shift_id}")
            self.shift_aggregate_vars[shift_id] = y_var
            
            # Vincolo di definizione: y[s] == sum_e x[e,s]
            if vars_for_shift:
                self.model.Add(y_var == sum(vars_for_shift))
            else:
                self.model.Add(y_var == 0)

    def _add_shift_coverage_constraints(self) -> None:
        """Gestisce la copertura dei turni consentendo scoperture penalizzate."""
        self.shortfall_vars = {}
        self.shift_overstaff_vars = {}
        for _, shift_row in self.shifts.iterrows():
            shift_id = shift_row["shift_id"]
            required_staff = int(shift_row["required_staff"])

            vars_for_shift = self._vars_by_shift.get(shift_id, [])

            if len(vars_for_shift) < required_staff:
                print(f"[WARN] Turno {shift_id}: capacita disponibili {len(vars_for_shift)} < required {required_staff}")

            if not vars_for_shift:
                print(f"[WARN] Turno {shift_id}: nessun dipendente assegnabile, verranno contabilizzati minuti di shortfall")

            shortfall_var = self.model.NewIntVar(0, max(0, required_staff), f"shortfall__{shift_id}")
            self.shortfall_vars[shift_id] = shortfall_var

            max_overstaff = max(0, len(vars_for_shift))
            overstaff_var = self.model.NewIntVar(0, max_overstaff, f"overstaff__{shift_id}")
            self.shift_overstaff_vars[shift_id] = overstaff_var

            assign_expr = sum(vars_for_shift) if vars_for_shift else 0
            self.model.Add(assign_expr + shortfall_var == required_staff + overstaff_var)


    def _add_skill_coverage_constraints(self) -> None:
        """Gestisce i requisiti di skill per ciascun turno."""
        self.skill_shortfall_vars = {}
        if not self.shift_skill_requirements:
            return

        for shift_id, requirements in self.shift_skill_requirements.items():
            if not requirements:
                continue
            vars_with_emp = self._vars_by_shift_emp.get(shift_id, [])
            for skill_name, required in requirements.items():
                req = int(required)
                if req <= 0:
                    continue
                eligible_vars = [
                    var
                    for emp_id, var in vars_with_emp
                    if skill_name in self.emp_skills.get(emp_id, set())
                ]
                if not eligible_vars and not self.skill_slack_enabled:
                    print(
                        f"[WARN] Turno {shift_id}: nessun dipendente con skill '{skill_name}' disponibile (vincolo hard)"
                    )

                assign_expr = sum(eligible_vars) if eligible_vars else 0
                if self.skill_slack_enabled:
                    safe_skill = ''.join(ch if ch.isalnum() or ch == '_' else '_' for ch in skill_name)
                    slack_var = self.model.NewIntVar(0, req, f"short_skill__{shift_id}__{safe_skill}")
                    self.skill_shortfall_vars[(shift_id, skill_name)] = slack_var
                    self.model.Add(assign_expr + slack_var >= req)
                else:
                    self.model.Add(assign_expr >= req)


    def _add_segment_coverage_constraints(self) -> None:
        """
        NUOVO: Implementa vincoli di copertura per segmenti con turni interi.

        Quando preserve_shift_integrity=True, questo metodo:
        1. Mantiene la segmentazione temporale per calcolare la domanda
        2. Ma usa solo variabili aggregate y[s] dei turni interi
        3. Ogni turno copre TUTTI i segmenti nel suo intervallo temporale
        
        Formulazione matematica:
        - Per ogni segmento s: Ã¢Ë†â€˜_{turni i che coprono s} a_{i,s} * y[i] >= d_s
        - Dove a_{i,s} = capacitÃƒÂ  fornita dal turno i nel segmento s (persona-minuti)
        - E d_s = domanda richiesta nel segmento s (persona-minuti)
        """
        self.segment_shortfall_vars = {}
        self.segment_overstaff_vars = {}

        has_window_demand = any(val > 0 for val in (self.window_demands or {}).values())

        # Solo se abbiamo dati di segmentazione
        if not self.adaptive_slot_data:
            if self.using_window_demands and has_window_demand:
                raise RuntimeError(
                    "Modalita domanda per finestra attiva ma dati di segmentazione assenti. "
                    "Verifica windows.csv e gli avvisi generati dal precompute."
                )
            logger.info("Vincoli segmenti con turni interi: nessun dato di segmentazione disponibile")
            return

        # Precomputa mappature turno -> segmenti e domande per segmento
        self._precompute_shift_to_segments_mapping()
        self._compute_segment_demands()

        if not self.segment_demands:
            if self.using_window_demands and has_window_demand:
                raise RuntimeError(
                    "Modalita domanda per finestra attiva ma nessuna domanda di segmento calcolata. "
                    "Controlla che le finestre siano mappate correttamente agli slot (ruoli/orari validi)."
                )
            logger.info("Vincoli segmenti con turni interi: nessuna domanda di segmento calcolata")
            return

        segment_count = 0
        constraint_count = 0

        for segment_id, demand_person_minutes in self.segment_demands.items():
            if demand_person_minutes < 0:
                continue

            segment_duration = self._get_segment_duration_minutes(segment_id)
            if segment_duration <= 0:
                continue

            segment_count += 1

            covering_shifts: list[str] = []
            for shift_id, segments in self.shift_to_covering_segments.items():
                if segment_id in segments:
                    covering_shifts.append(shift_id)

            covering_terms = []
            capacity_bound = 0
            for shift_id in covering_shifts:
                y_var = self.shift_aggregate_vars.get(shift_id)
                if y_var is None:
                    continue
                covering_terms.append(segment_duration * y_var)
                eligible = len(self._vars_by_shift.get(shift_id, []))
                if eligible > 0:
                    capacity_bound += segment_duration * eligible

            slack_var = self.model.NewIntVar(0, max(0, int(demand_person_minutes)), f"short_segment__{segment_id}")
            self.segment_shortfall_vars[segment_id] = slack_var

            overstaff_ub = max(0, capacity_bound)
            overstaff_var = self.model.NewIntVar(0, overstaff_ub, f"overstaff_segment__{segment_id}")
            self.segment_overstaff_vars[segment_id] = overstaff_var

            if covering_terms:
                self.model.Add(sum(covering_terms) + slack_var == demand_person_minutes + overstaff_var)
            else:
                self.model.Add(slack_var == demand_person_minutes)

            constraint_count += 1
        
        if segment_count > 0:
            logger.info(
                "Vincoli segmenti con turni interi: %d segmenti, %d vincoli (preserve_shift_integrity=True)",
                segment_count,
                constraint_count
            )
        else:
            logger.info("Vincoli segmenti con turni interi: nessun segmento con domanda trovato")

    def _add_segment_skill_constraints(self) -> None:
        """Vincoli skill su segmenti quando le skill sono definite nelle windows."""
        self.segment_skill_shortfall_vars = {}

        if not self.using_window_skills:
            return
        if not self.adaptive_slot_data:
            return

        if not self.segment_skill_demands:
            self._compute_segment_skill_demands()
        if not self.segment_skill_demands:
            return

        if not self.shift_to_covering_segments:
            self._precompute_shift_to_segments_mapping()

        for (segment_id, skill_name), demand_minutes in self.segment_skill_demands.items():
            if demand_minutes <= 0:
                continue

            segment_duration = self._get_segment_duration_minutes(segment_id)
            if segment_duration <= 0:
                continue

            covering_terms = []
            for shift_id, segments in self.shift_to_covering_segments.items():
                if segment_id not in segments:
                    continue
                vars_with_emp = self._vars_by_shift_emp.get(shift_id, [])
                eligible = [var for emp_id, var in vars_with_emp if skill_name in self.emp_skills.get(emp_id, set())]
                if not eligible:
                    continue
                covering_terms.append(segment_duration * sum(eligible))

            safe_skill = ''.join(ch if ch.isalnum() or ch == '_' else '_' for ch in skill_name)
            slack_var = self.model.NewIntVar(0, demand_minutes, f"short_segment_skill__{segment_id}__{safe_skill}")
            self.segment_skill_shortfall_vars[(segment_id, skill_name)] = slack_var

            if covering_terms:
                self.model.Add(sum(covering_terms) + slack_var >= demand_minutes)
            else:
                self.model.Add(slack_var >= demand_minutes)

    def _precompute_shift_to_segments_mapping(self) -> None:
        """Precomputa mappa turno -> segmenti che copre."""
        self.shift_to_covering_segments = {}
        
        if not self.adaptive_slot_data:
            return
            
        try:
            segments_of_s = getattr(self.adaptive_slot_data, 'segments_of_s', {})
            
            # Per ogni turno, ottieni la lista dei segmenti che copre
            for shift_id in self.shift_aggregate_vars.keys():
                segments = segments_of_s.get(str(shift_id), [])
                self.shift_to_covering_segments[shift_id] = list(segments)
                
        except AttributeError as e:
            logger.warning("Errore nell'accesso ai segmenti per turno: %s", e)
            # Fallback: nessun turno copre alcun segmento
            for shift_id in self.shift_aggregate_vars.keys():
                self.shift_to_covering_segments[shift_id] = []

    def _compute_segment_demands(self) -> None:
        """
        Calcola la domanda per ogni segmento basata su intersezioni reali con le finestre.
        
        Due modalità  supportate:
        - "headcount": domanda = somma tra le domande delle finestre che intersecano il segmento
        - "person_minutes": domanda = somma proporzionale dei contributi delle finestre intersecanti
        """
        self.segment_demands = {}
        
        if not self.adaptive_slot_data or not self.window_demands:
            return
        slot_windows = {}
        if self.adaptive_slot_data is not None:
            segment_bounds = {}
            segment_bounds = getattr(self.adaptive_slot_data, "segment_bounds", {}) or {}
            slot_windows = getattr(self.adaptive_slot_data, "slot_windows", {}) or {}

        if not segment_bounds:
            logger.info("Calcolo domande segmenti: segment_bounds mancante, esco")
            return

        raw_slot_windows = slot_windows or self.slot_windows or {}
        segment_to_windows: dict[str, list[tuple[str, int]]] = {}
        if raw_slot_windows:
            cover_map = getattr(self.adaptive_slot_data, "cover_segment", {}) or {}
            for (segment_id, slot_id), covers in cover_map.items():
                if not covers:
                    continue
                for win_info in raw_slot_windows.get(slot_id, []):
                    segment_to_windows.setdefault(segment_id, []).append(win_info)
            if not segment_to_windows and isinstance(raw_slot_windows, dict):
                segment_to_windows = raw_slot_windows  # fallback legacy mapping

        if not segment_to_windows:
            logger.info("Calcolo domande segmenti: mapping segmenti-finestre mancante, esco")
            return

        logger.info("Calcolo domande segmenti con demand_mode='%s'", self.demand_mode)

        for segment_id, seg_info in segment_bounds.items():
            seg_start_min, seg_end_min = self._unpack_segment_bounds(seg_info)
            segment_duration = max(0, seg_end_min - seg_start_min)
            if segment_duration <= 0:
                continue

            windows = segment_to_windows.get(segment_id, [])
            if not windows:
                continue

            if self.demand_mode == "headcount":
                headcount_sum = 0
                for win_info in windows:
                    if isinstance(win_info, tuple):
                        window_id = win_info[0]
                    else:
                        window_id = win_info
                    demand = self.window_demands.get(window_id, 0)
                    if demand > 0:
                        headcount_sum += demand
            
                # Domanda in persona-minuti per il segmento
                person_minutes = headcount_sum * segment_duration
                if person_minutes > 0:
                    self.segment_demands[segment_id] = person_minutes

            elif self.demand_mode == "person_minutes":
                total_person_minutes = 0
                for win_info in windows:
                    # se slot_windows contiene coppie (window_id, overlap_minutes)
                    if isinstance(win_info, tuple):
                        window_id, overlap = win_info
                        overlap_minutes = max(0, int(overlap))
                    else:
                        window_id = win_info
                        overlap_minutes = segment_duration

                    demand = self.window_demands.get(window_id, 0)
                    if demand <= 0 or overlap_minutes <= 0:
                        continue

                    total_person_minutes += demand * overlap_minutes

                if total_person_minutes > 0:
                    self.segment_demands[segment_id] = int(round(total_person_minutes))

        # Log statistiche finali
        if self.segment_demands:
            total_segments = len(self.segment_demands)
            total_demand = sum(self.segment_demands.values())
            avg_demand = total_demand / total_segments
            logger.info(
                "Domande segmenti (%s): %d segmenti, domanda totale %d, media %.1f",
                self.demand_mode,
                total_segments,
                total_demand,
                avg_demand
                )
        else:
            logger.info("Nessun segmento con domanda calcolata")
                    

    def _compute_segment_skill_demands(self) -> None:
        """Calcola la domanda di skill per ciascun segmento in persona-minuti."""
        self.segment_skill_demands = {}

        if not self.adaptive_slot_data or not self.window_skill_requirements:
            return
        segment_bounds = getattr(self.adaptive_slot_data, "segment_bounds", {}) or {}
        if not segment_bounds:
            logger.info("Calcolo domande skill segmenti: segment_bounds mancante, esco")
            return

        raw_slot_windows = getattr(self.adaptive_slot_data, "slot_windows", {}) or self.slot_windows or {}
        segment_to_windows: dict[str, list[tuple[str, int]]] = {}
        if raw_slot_windows:
            cover_map = getattr(self.adaptive_slot_data, "cover_segment", {}) or {}
            for (segment_id, slot_id), covers in cover_map.items():
                if not covers:
                    continue
                windows = raw_slot_windows.get(slot_id, [])
                if windows:
                    segment_to_windows.setdefault(segment_id, []).extend(windows)
            if not segment_to_windows and isinstance(raw_slot_windows, dict):
                segment_to_windows = raw_slot_windows  # fallback legacy mapping
        if not segment_to_windows:
            logger.info("Calcolo domande skill segmenti: mapping segmenti-finestre mancante, esco")
            return

        demand_mode = getattr(self, "demand_mode", "headcount")
        logger.info("Calcolo domande skill segmenti con demand_mode='%s'", demand_mode)

        for segment_id, seg_info in segment_bounds.items():
            seg_start_min, seg_end_min = self._unpack_segment_bounds(seg_info)
            segment_duration = max(0, seg_end_min - seg_start_min)
            if segment_duration <= 0:
                continue

            windows = segment_to_windows.get(segment_id, [])
            if not windows:
                continue

            if demand_mode == "person_minutes":
                skill_minutes: dict[str, int] = {}
                for win_info in windows:
                    if isinstance(win_info, tuple):
                        window_id, overlap = win_info
                        overlap_minutes = max(0, int(overlap))
                    else:
                        window_id = win_info
                        overlap_minutes = segment_duration
                    if overlap_minutes <= 0:
                        continue

                    skills = self.window_skill_requirements.get(window_id)
                    if not skills:
                        continue

                    for skill_name, qty in skills.items():
                        try:
                            qty_int = int(qty)
                        except (TypeError, ValueError):
                            continue
                        if qty_int <= 0:
                            continue
                        skill_minutes[skill_name] = skill_minutes.get(skill_name, 0) + qty_int * overlap_minutes

                for skill_name, minutes in skill_minutes.items():
                    if minutes > 0:
                        self.segment_skill_demands[(segment_id, skill_name)] = int(round(minutes))
            else:
                skill_headcount: dict[str, int] = {}
                for win_info in windows:
                    window_id = win_info[0] if isinstance(win_info, tuple) else win_info
                    skills = self.window_skill_requirements.get(window_id)
                    if not skills:
                        continue

                    for skill_name, qty in skills.items():
                        try:
                            qty_int = int(qty)
                        except (TypeError, ValueError):
                            continue
                        if qty_int <= 0:
                            continue
                        skill_headcount[skill_name] = skill_headcount.get(skill_name, 0) + qty_int

                for skill_name, headcount in skill_headcount.items():
                    person_minutes = headcount * segment_duration
                    if person_minutes > 0:
                        self.segment_skill_demands[(segment_id, skill_name)] = person_minutes

    def _unpack_segment_bounds(self, val):
        """Unpacks segment bounds handling both (start, end) and (day, role, start, end) formats."""
        if isinstance(val, (list, tuple)):
            if len(val) == 2:
                return int(val[0]), int(val[1])
            elif len(val) >= 4:
                return int(val[-2]), int(val[-1])
        raise ValueError(f"Formato segment_bounds inatteso: {val}")

    def _get_segment_duration_minutes(self, segment_id: str) -> int:
        """Ottiene la durata di un segmento in minuti."""
        if not self.adaptive_slot_data:
            return self.avg_shift_minutes  # Fallback
            
        try:
            segment_bounds = getattr(self.adaptive_slot_data, 'segment_bounds', {})
            if segment_id in segment_bounds:
                start_min, end_min = self._unpack_segment_bounds(segment_bounds[segment_id])
                return max(1, int(end_min - start_min))
        except (AttributeError, ValueError):
            pass
            
        return self.avg_shift_minutes  # Fallback


    def _compute_shift_duration_minutes(self) -> Dict[str, int]:
        """Pre-calcola la durata di ogni turno in minuti interi."""
        if "duration_h" not in self.shifts.columns:
            raise ValueError("La tabella shifts deve includere la colonna 'duration_h'.")

        durations: Dict[str, int] = {}
        for _, row in self.shifts.iterrows():
            minutes = max(1, int(round(float(row["duration_h"]) * 60)))
            durations[row["shift_id"]] = minutes

        return durations

    def _compute_total_required_minutes(self) -> int:
        """
        Ritorna il fabbisogno complessivo espresso in persona-minuti.

        1. Se abbiamo giÃƒÂ  calcolato le domande per segmento (modalitÃƒÂ  windows),
           usiamo la somma dei segmenti.
        2. Altrimenti, facciamo il fallback sulla domanda dei turni,
           cioÃƒÂ¨ durata_turno * required_staff.
        """
        total_from_segments = 0

        # Caso finestre/segmenti: somma delle domande per segmento giÃƒÂ  calcolate
        if getattr(self, "segment_demands", None):
            total_from_segments = int(sum(self.segment_demands.values()))

        if total_from_segments > 0:
            return total_from_segments

        # Fallback: usa i dati dei turni (required_staff)
        total_from_shifts = 0
        for _, shift_row in self.shifts.iterrows():
            shift_id = shift_row["shift_id"]
            required_staff = int(shift_row.get("required_staff", 0) or 0)
            if required_staff <= 0:
                continue
            duration_min = self.duration_minutes.get(shift_id)
            if duration_min is None:
                raise ValueError(f"Durata mancante per il turno {shift_id}")
            total_from_shifts += duration_min * required_staff

        return total_from_shifts


    def _add_one_shift_per_day_constraints(self) -> None:
        """Consente al massimo un turno per dipendente in ogni giorno."""
        if "start_dt" not in self.shifts.columns:
            raise ValueError("La tabella shifts deve includere la colonna 'start_dt'.")

        start_dt_by_shift = self.shifts.set_index("shift_id")["start_dt"]

        for emp_id, pairs in self._vars_by_emp.items():
            vars_by_day: dict = {}
            for shift_id, var in pairs:
                start_dt = start_dt_by_shift.get(shift_id)
                if start_dt is None:
                    continue
                start_day = start_dt.date() if hasattr(start_dt, 'date') else start_dt
                vars_by_day.setdefault(start_day, []).append(var)

            for vars_list in vars_by_day.values():
                if len(vars_list) > 1:
                    self.model.Add(sum(vars_list) <= 1)


    def _add_night_shift_constraints(self) -> None:
        """Vieta notti consecutive e limita a 3 notti per settimana."""
        required_cols = {"start_dt", "end_dt"}
        if not required_cols.issubset(self.shifts.columns):
            raise ValueError("La tabella shifts deve includere le colonne 'start_dt' ed 'end_dt'.")

        night_shift_ids = []
        night_start_map = {}
        night_week_map = {}
        for _, row in self.shifts.iterrows():
            shift_id = row["shift_id"]
            start_dt = row["start_dt"]
            end_dt = row["end_dt"]

            if self._is_night_shift(start_dt, end_dt):
                night_shift_ids.append(shift_id)
                night_start_map[shift_id] = start_dt
                iso = start_dt.isocalendar()
                night_week_map[shift_id] = (iso.year, iso.week)

        if not night_shift_ids:
            self._night_shift_ids = set()
            return

        self._night_shift_ids = set(night_shift_ids)

        for emp_id in self.employees["employee_id"]:
            night_vars = []
            for shift_id in night_shift_ids:
                var = self.assignment_vars.get((emp_id, shift_id))
                if var is not None:
                    night_vars.append((night_start_map[shift_id], shift_id, var))

            if len(night_vars) < 2:
                continue

            night_vars.sort(key=lambda item: item[0])
            for (day1, _sid1, var1), (day2, _sid2, var2) in zip(night_vars, night_vars[1:]):
                if (day2 - day1).days == 1:
                    self.model.Add(var1 + var2 <= 1)

            nights_by_week = {}
            for day, sid, var in night_vars:
                week_key = night_week_map[sid]
                nights_by_week.setdefault(week_key, []).append(var)

            for vars_list in nights_by_week.values():
                if len(vars_list) > 3:
                    self.model.Add(sum(vars_list) <= 3)


    def _add_rest_conflict_constraints(self) -> None:
        """Applica vincoli da rest_conflicts precomputati (se forniti)."""
        if self.rest_conflicts is None or self.rest_conflicts.empty:
            return

        required_cols = {"shift_id_from", "shift_id_to"}
        if not required_cols.issubset(self.rest_conflicts.columns):
            raise ValueError("rest_conflicts deve includere le colonne 'shift_id_from' e 'shift_id_to'.")

        for _, row in self.rest_conflicts.iterrows():
            sid_from = row["shift_id_from"]
            sid_to = row["shift_id_to"]
            if sid_from == sid_to:
                continue

            for emp_id in self.employees["employee_id"]:
                var_from = self.assignment_vars.get((emp_id, sid_from))
                var_to = self.assignment_vars.get((emp_id, sid_to))
                if var_from is None or var_to is None:
                    continue
                # Evita che lo stesso dipendente prenda entrambe le istanze
                self.model.Add(var_from + var_to <= 1)


    def _add_employee_max_hours_constraints(self) -> None:
        """
        NUOVA LOGICA: Gestisce ore contrattuali e straordinari usando esplicitamente contracted_hours.
        Distingue tra lavoratori contrattualizzati (contracted_hours valorizzata) e non contrattualizzati.
        """
        if not self.duration_minutes:
            raise ValueError("Le durate dei turni devono essere calcolate prima di applicare il vincolo sulle ore massime.")

        self.overtime_vars = {}
        self.overtime_cost_weights = {}
        self.external_minutes_vars = {}
        self.overtime_limits = {}
        self.total_possible_overtime_minutes = 0
        total_possible_ot = 0

        for _, emp_row in self.employees.iterrows():
            emp_id = emp_row["employee_id"]
            
            # NUOVA LOGICA: Usa contracted_hours come base per la distinzione
            contracted_h = emp_row.get("contracted_hours")
            min_h_raw = emp_row.get("min_week_hours")
            default_min = self.global_min_weekly
            min_h = float(min_h_raw) if pd.notna(min_h_raw) else default_min

            max_h_raw = emp_row.get("max_week_hours")
            if pd.notna(max_h_raw):
                max_h = float(max_h_raw)
            else:
                max_h = self.global_max_weekly if self.global_max_weekly is not None else float("inf")

            overtime_raw = emp_row.get("max_overtime_hours")
            if pd.notna(overtime_raw):
                overtime = float(overtime_raw)
            elif getattr(self, "global_max_overtime_hours", None) is not None:
                overtime = float(self.global_max_overtime_hours)
            else:
                overtime = 0.0
            if not math.isfinite(overtime):
                overtime = 0.0
            overtime = max(0.0, overtime)
            overtime_minutes = int(round(overtime * 60)) if overtime > 0 else 0

            # Determina se ÃƒÂ¨ contrattualizzato basandosi su contracted_hours
            is_contracted = pd.notna(contracted_h)
            
            pairs = self._vars_by_emp.get(emp_id, [])
            terms = [self.duration_minutes[shift_id] * var for shift_id, var in pairs]
            assigned_expr = sum(terms) if terms else 0

            if is_contracted:
                # Ã°Å¸Â§Â° Caso 1: Lavoratore CONTRATTUALIZZATO (contracted_hours valorizzata)
                # - Usa contracted_hours come base per i vincoli
                # - Permetti straordinari se specificati
                # - Considera le assenze (time_off) come ore giÃƒÂ  conteggiate verso il contratto
                
                contracted_minutes = int(float(contracted_h) * 60)
                overtime_var = self.model.NewIntVar(0, overtime_minutes, f"overtime_min__{emp_id}")
                self.overtime_vars[emp_id] = overtime_var
                self.overtime_limits[emp_id] = overtime_minutes

                # Calcola time_off_minutes per questo dipendente
                time_off_minutes = self._calculate_time_off_minutes(emp_id)
                
                # Vincoli: worked_minutes + time_off_minutes >= contracted_minutes
                #          worked_minutes + time_off_minutes <= contracted_minutes + overtime_minutes
                self.model.Add(assigned_expr + time_off_minutes >= contracted_minutes)
                self.model.Add(assigned_expr + time_off_minutes <= contracted_minutes + overtime_var)
                
                zero_ext = self.model.NewIntVar(0, 0, f"external_minutes__{emp_id}")
                self.external_minutes_vars[emp_id] = zero_ext
                logger.debug(f"Worker {emp_id}: CONTRATTUALIZZATO - contracted={contracted_h}h, overtime_max={overtime}h, time_off={time_off_minutes}min")

            else:
                # Ã°Å¸â€œÅ  Caso 2: Lavoratore NON CONTRATTUALIZZATO (contracted_hours vuota)
                # - Usa min_hours e max_week_hours per definire i limiti orari
                # - Vincoli condizionali: puÃƒÂ² non essere usato (0 ore) oppure nel range [min_hours, max_hours]
                # - NON creare variabili di overtime
                
                min_minutes = int(min_h * 60)
                if math.isfinite(max_h):
                    max_minutes = int(max_h * 60)
                else:
                    max_minutes = sum(self.duration_minutes.get(shift_id, 0) for shift_id, _ in pairs)
                max_minutes = max(max_minutes, min_minutes)
                
                # Nessuna variabile straordinari per lavoratori non contrattualizzati
                self.overtime_vars[emp_id] = self.model.NewIntVar(0, 0, f"overtime_min__{emp_id}")  # Sempre 0
                self.overtime_limits[emp_id] = 0

                # VINCOLI CONDIZIONALI: Usa solo se conveniente
                # Variabile binaria: risorsa esterna ÃƒÂ¨ utilizzata?
                use_external = self.model.NewBoolVar(f"use_external__{emp_id}")
                self.external_worker_usage_vars[emp_id] = use_external
                
                # Se non usata: 0 ore (senza penalitÃƒÂ )
                self.model.Add(assigned_expr == 0).OnlyEnforceIf(use_external.Not())
                
                # Se usata: deve essere nel range [min_minutes, max_minutes]
                self.model.Add(assigned_expr >= min_minutes).OnlyEnforceIf(use_external)
                self.model.Add(assigned_expr <= max_minutes).OnlyEnforceIf(use_external)
                
                # Collegamento logico: se assigned_expr > 0 allora use_external = True
                self.model.Add(assigned_expr > 0).OnlyEnforceIf(use_external)
                self.model.Add(assigned_expr == 0).OnlyEnforceIf(use_external.Not())
                
                ext_minutes_var = self.model.NewIntVar(0, max_minutes, f"external_minutes__{emp_id}")
                self.model.Add(ext_minutes_var == assigned_expr)
                self.external_minutes_vars[emp_id] = ext_minutes_var
                logger.debug(f"Worker {emp_id}: RISORSA ESTERNA - min={min_h}h, max={max_h}h (attivazione condizionale)")

            total_possible_ot += overtime_minutes
            self.overtime_cost_weights[emp_id] = self._resolve_overtime_cost_weight(emp_row)

        self.total_possible_overtime_minutes = total_possible_ot

        if self.config.global_overtime_cap_minutes is not None and self.overtime_vars:
            cap_minutes = min(self.config.global_overtime_cap_minutes, total_possible_ot) if total_possible_ot > 0 else 0
            self.global_overtime_cap_minutes = cap_minutes
            self.model.Add(sum(self.overtime_vars.values()) <= cap_minutes)
        else:
            self.global_overtime_cap_minutes = None

    def _add_employee_daily_max_constraints(self) -> None:
        """Applica il limite giornaliero globale di ore per ciascun dipendente."""
        if getattr(self, "global_max_daily_minutes", None) is None:
            return
        if "start_dt" not in self.shifts.columns:
            raise ValueError("La tabella shifts deve includere la colonna 'start_dt'.")

        daily_limit_minutes = int(self.global_max_daily_minutes)
        start_dt_series = self.shifts.set_index("shift_id")["start_dt"]
        end_dt_series = self.shifts.set_index("shift_id")["end_dt"]

        daily_minutes_by_shift: dict[str, dict] = {}
        for shift_id, start_dt in start_dt_series.items():
            if pd.isna(start_dt):
                continue
            end_dt = end_dt_series.get(shift_id)
            if pd.isna(end_dt):
                continue
            current_start = start_dt
            segments: dict = {}
            while current_start < end_dt:
                next_midnight = datetime.combine(current_start.date(), datetime.min.time()) + timedelta(days=1)
                current_end = min(end_dt, next_midnight)
                delta_minutes = int((current_end - current_start).total_seconds() / 60)
                if delta_minutes > 0:
                    segments[current_start.date()] = segments.get(current_start.date(), 0) + delta_minutes
                current_start = current_end
            if not segments:
                duration = self.duration_minutes.get(shift_id)
                if duration is not None:
                    day_key = start_dt.date() if hasattr(start_dt, "date") else start_dt
                    segments[day_key] = int(duration)
            if segments:
                daily_minutes_by_shift[str(shift_id)] = segments

        for emp_id, pairs in self._vars_by_emp.items():
            minutes_by_day: dict = {}
            for shift_id, var in pairs:
                segments = daily_minutes_by_shift.get(str(shift_id))
                if not segments:
                    duration = self.duration_minutes.get(shift_id)
                    if duration is None:
                        raise ValueError(f"Durata mancante per il turno {shift_id}")
                    start_dt = start_dt_series.get(shift_id)
                    if start_dt is None:
                        continue
                    day_key = start_dt.date() if hasattr(start_dt, "date") else start_dt
                    minutes_by_day.setdefault(day_key, []).append((int(duration), var))
                    continue
                for day_key, minutes in segments.items():
                    minutes_by_day.setdefault(day_key, []).append((minutes, var))

            for entries in minutes_by_day.values():
                if not entries:
                    continue
                expr = sum(minutes * var for minutes, var in entries)
                self.model.Add(expr <= daily_limit_minutes)


    def _calculate_time_off_minutes(self, emp_id: str) -> int:
        """
        Calcola i minuti di time_off per un dipendente specifico.
        
        Questo metodo somma tutti i minuti di assenza (ferie, malattie, etc.) 
        per il dipendente specificato nel periodo di schedulazione.
        
        Args:
            emp_id: ID del dipendente
            
        Returns:
            Totale minuti di time_off per il dipendente
        """
        # Per ora restituisce 0 come placeholder
        # In una implementazione completa, questo metodo dovrebbe:
        # 1. Accedere ai dati time_off dal loader (self.time_off_data)
        # 2. Filtrare per emp_id nel periodo di schedulazione
        # 3. Calcolare l'intersezione temporale con i turni
        # 4. Sommare i minuti totali di assenza
        
        # Esempio di implementazione futura:
        # if hasattr(self, 'time_off_data') and self.time_off_data is not None:
        #     emp_time_off = self.time_off_data[self.time_off_data['employee_id'] == emp_id]
        #     total_minutes = 0
        #     for _, row in emp_time_off.iterrows():
        #         start_dt = pd.to_datetime(row['start_datetime'])
        #         end_dt = pd.to_datetime(row['end_datetime'])
        #         duration_hours = (end_dt - start_dt).total_seconds() / 3600
        #         total_minutes += int(duration_hours * 60)
        #     return total_minutes
        
        return 0

    def _resolve_overtime_cost_weight(self, emp_row: pd.Series) -> int:
        roles_set = emp_row.get("roles_set", set())
        candidates = [self.role_overtime_costs[role] for role in roles_set if role in self.role_overtime_costs]
        if not candidates:
            primary_role = emp_row.get("primary_role")
            if primary_role and primary_role in self.role_overtime_costs:
                candidates = [self.role_overtime_costs[primary_role]]
        if not candidates:
            return int(round(self.config.overtime_priority))

        cost_per_hour = min(candidates)
        base_cost = self.min_overtime_cost_per_hour if self.min_overtime_cost_per_hour not in (None, 0) else cost_per_hour
        ratio = cost_per_hour / base_cost if base_cost else 1.0
        weight = int(round(self.config.overtime_priority * ratio))
        return max(0, weight)

    def _compute_shortfall_cost_expr(self):
        if not self.shortfall_vars:
            return 0, False

        if not self.duration_minutes:
            raise ValueError("Le durate dei turni devono essere disponibili per calcolare il costo di shortfall.")

        terms = []
        for shift_id, var in self.shortfall_vars.items():
            duration = self.duration_minutes.get(shift_id)
            if duration is None:
                raise ValueError(f"Durata non disponibile per il turno {shift_id}")
            terms.append(duration * var)

        if not terms:
            return 0, False

        return sum(terms), True

    def _compute_window_shortfall_expr(self):
        if not self.window_shortfall_vars:
            return 0, False

        terms = []
        for window_id, var in self.window_shortfall_vars.items():
            duration = self.window_duration_minutes.get(window_id, self.avg_shift_minutes)
            duration = max(1, int(duration))
            terms.append(duration * var)

        if not terms:
            return 0, False
        return sum(terms), True

    def _compute_overstaff_expr(self):
        terms: list[cp_model.LinearExpr] = []

        if self.shift_overstaff_vars:
            if not self.duration_minutes:
                raise ValueError("Le durate dei turni devono essere disponibili per calcolare il costo di overstaff.")
            for shift_id, var in self.shift_overstaff_vars.items():
                duration = self.duration_minutes.get(shift_id)
                if duration is None:
                    continue
                terms.append(duration * var)

        segment_vars = getattr(self, "segment_overstaff_vars", None)
        if segment_vars:
            for segment_id, var in segment_vars.items():
                if var is None:
                    continue
                terms.append(var)

        if not terms:
            return 0, False
        return sum(terms), True

    def _compute_skill_shortfall_expr(self):
        if not self.skill_shortfall_vars:
            return 0, False

        terms = []
        for (shift_id, _skill), var in self.skill_shortfall_vars.items():
            duration = self.duration_minutes.get(shift_id)
            if duration is None:
                continue
            terms.append(duration * var)

        if not terms:
            return 0, False
        return sum(terms), True

  
    def _compute_overtime_cost_expr(self):
        if not self.overtime_vars:
            return 0, False

        terms = []
        for emp_id, var in self.overtime_vars.items():
            weight = self.overtime_cost_weights.get(emp_id)
            if weight is None:
                weight = int(round(self.config.overtime_priority))
            terms.append(weight * var)
        if not terms:
            return 0, False

        return sum(terms), True

    def _compute_external_usage_expr(self):
        """Somma i minuti assegnati alle risorse esterne."""
        if not self.external_minutes_vars:
            return 0, False

        terms = list(self.external_minutes_vars.values())
        if not terms:
            return 0, False
        return sum(terms), True

    def _compute_preference_cost_expr(self):
        if not self.assignment_vars:
            return 0, False

        avg_minutes = max(1, int(self.avg_shift_minutes))
        terms = []
        for (emp_id, shift_id), var in self.assignment_vars.items():
            score = self.preference_score_by_pair.get((emp_id, shift_id), 0)
            if score == 0:
                continue
            coeff = -score * avg_minutes
            if coeff != 0:
                terms.append(coeff * var)

        if not terms:
            return 0, False

        return sum(terms), True

    def _compute_fair_workload_expr(self):
        contracted_ids = [emp_id for emp_id, limit in self.overtime_limits.items() if limit > 0 and emp_id in self.overtime_vars]
        if len(contracted_ids) <= 1:
            return 0, False

        if self.total_possible_overtime_minutes <= 0:
            return 0, False

        total_expr = sum(self.overtime_vars[emp_id] for emp_id in contracted_ids)
        num_active = len(contracted_ids)
        deviation_bound = max(1, self.total_possible_overtime_minutes)

        self.workload_dev_vars = []
        for emp_id in contracted_ids:
            ot_var = self.overtime_vars[emp_id]
            over = self.model.NewIntVar(0, deviation_bound, f"workload_over__{emp_id}")
            under = self.model.NewIntVar(0, deviation_bound, f"workload_under__{emp_id}")
            self.model.Add(num_active * ot_var - total_expr == num_active * over - num_active * under)
            self.workload_dev_vars.extend([over, under])

        if not self.workload_dev_vars:
            return 0, False
        return sum(self.workload_dev_vars), True


    def _compute_segment_shortfall_expr(self):
        """NUOVO: Calcola l'espressione per shortfall dei segmenti con turni interi."""
        if not self.segment_shortfall_vars:
            return 0, False

        terms = []
        for segment_id, var in self.segment_shortfall_vars.items():
            if self.demand_mode == "headcount":
                # MODALITÃƒâ‚¬ HEADCOUNT: shortfall in persone, moltiplicare per durata segmento
                # per ottenere persona-minuti per coerenza con funzione obiettivo
                segment_duration = self._get_segment_duration_minutes(segment_id)
                terms.append(segment_duration * var)
            else:
                # MODALITÃƒâ‚¬ PERSON_MINUTES: shortfall giÃƒÂ  in persona-minuti
                terms.append(var)

        if not terms:
            return 0, False
        return sum(terms), True

    def _compute_segment_skill_shortfall_expr(self):
        """Calcola l'espressione di shortfall skill aggregata sui segmenti."""
        if not self.segment_skill_shortfall_vars:
            return 0, False

        terms = list(self.segment_skill_shortfall_vars.values())
        if not terms:
            return 0, False
        return sum(terms), True

    def _assemble_objective_priority_map(self) -> dict[str, tuple[cp_model.LinearExpr, bool]]:
        """Costruisce e memorizza le espressioni disponibili per ogni obiettivo."""
        if self._objective_priority_map is not None:
            return self._objective_priority_map

        window_expr, has_window = self._compute_window_shortfall_expr()
        shortfall_expr, has_shortfall = self._compute_shortfall_cost_expr()
        skill_expr, has_skill = self._compute_skill_shortfall_expr()
        segment_skill_expr, has_segment_skill = self._compute_segment_skill_shortfall_expr()
        overstaff_expr, has_overstaff = self._compute_overstaff_expr()
        overtime_expr, has_overtime = self._compute_overtime_cost_expr()
        external_expr, has_external = self._compute_external_usage_expr()
        pref_expr, has_pref = self._compute_preference_cost_expr()
        fairness_expr, has_fairness = self._compute_fair_workload_expr()
        segment_expr, has_segment = self._compute_segment_shortfall_expr()

        priority_map = {
            "unmet_window": (window_expr, has_window),
            "unmet_demand": (shortfall_expr, has_shortfall),
            "unmet_skill": (skill_expr, has_skill),
            "overstaff": (overstaff_expr, has_overstaff),
            "overtime": (overtime_expr, has_overtime),
            "external_use": (external_expr, has_external),
            "preferences": (pref_expr, has_pref),
            "fairness": (fairness_expr, has_fairness),
        }

        if has_segment:
            priority_map["unmet_window"] = (segment_expr, has_segment)
        if has_segment_skill:
            priority_map["unmet_skill"] = (segment_skill_expr, has_segment_skill)

        self._objective_priority_map = priority_map
        return priority_map

    def _set_objective(self) -> None:
        priority_map = self._assemble_objective_priority_map()

        terms: list[cp_model.LinearExpr] = []
        for key in self.objective_priority:
            expr, available = priority_map.get(key, (0, False))
            weight = self.objective_weights.get(key, 0)
            if available and weight:
                terms.append(weight * expr)

        if not terms:
            self.model.Minimize(0)
            return

        self.model.Minimize(sum(terms))

    def _collect_lex_stages(self) -> list[tuple[str, cp_model.LinearExpr]]:
        """Restituisce la sequenza (chiave, espressione) per ottimizzazione lessicografica."""
        priority_map = self._assemble_objective_priority_map()
        stages: list[tuple[str, cp_model.LinearExpr]] = []
        for key in self.objective_priority:
            expr, available = priority_map.get(key, (0, False))
            weight = self.objective_weights.get(key, 0)
            if not available or not weight:
                continue
            stages.append((key, expr))
        return stages

    def _add_min_rest_constraints(self) -> None:
        """Impedisce che un dipendente prenda due turni troppo ravvicinati."""
        required_columns = {"start_dt", "end_dt"}
        if not required_columns.issubset(self.shifts.columns):
            raise ValueError("La tabella shifts deve includere le colonne 'start_dt' ed 'end_dt'.")

        start_map = dict(zip(self.shifts["shift_id"], self.shifts["start_dt"]))
        end_map = dict(zip(self.shifts["shift_id"], self.shifts["end_dt"]))

        for _, emp_row in self.employees.iterrows():
            emp_id = emp_row["employee_id"]
            min_rest = float(emp_row["min_rest_hours"])
            if min_rest <= 0:
                continue

            if min_rest <= self.config.global_min_rest_hours and self.rest_conflicts is not None:
                # vincoli globali gia' coprono questo intervallo
                continue

            pairs = [
                (sid, var)
                for sid, var in self._vars_by_emp.get(emp_id, [])
                if sid in start_map and sid in end_map
            ]
            for i, (sid1, var1) in enumerate(pairs):
                for sid2, var2 in pairs[i + 1:]:
                    if self._shifts_violate_rest(start_map, end_map, sid1, sid2, min_rest):
                        self.model.Add(var1 + var2 <= 1)

    @staticmethod
    def _is_night_shift(start_dt, end_dt) -> bool:
        """Identifica i turni notturni in base agli orari di inizio/fine."""
        if end_dt.date() > start_dt.date():
            return True
        start_hour = start_dt.hour
        return start_hour >= 22 or start_hour < 6


    @staticmethod
    def _shifts_violate_rest(start_map, end_map, sid1, sid2, min_rest) -> bool:
        """True se i due turni non rispettano il riposo minimo."""
        start1, end1 = start_map[sid1], end_map[sid1]
        start2, end2 = start_map[sid2], end_map[sid2]

        if end1 <= start2:
            gap = (start2 - end1).total_seconds() / 3600.0
            return gap < min_rest
        if end2 <= start1:
            gap = (start1 - end2).total_seconds() / 3600.0
            return gap < min_rest
        return True

    def solve(self) -> cp_model.CpSolver:
        """Esegue la risoluzione CP-SAT."""
        if self.objective_mode == "lex":
            return self._solve_lex()

        solver = cp_model.CpSolver()
        if self.config.max_seconds is not None:
            solver.parameters.max_time_in_seconds = float(self.config.max_seconds)
        solver.parameters.log_search_progress = self.config.log_search_progress
        if self.random_seed is not None:
            solver.parameters.random_seed = int(self.random_seed)
        if self.mip_gap is not None:
            solver.parameters.relative_gap_limit = float(self.mip_gap)

        solver.Solve(self.model)
        return solver

    def _solve_lex(self) -> cp_model.CpSolver:
        """Risoluzione lessicografica pura tramite passaggi successivi."""
        stages = self._collect_lex_stages()
        if not stages:
            solver = cp_model.CpSolver()
            if self.config.max_seconds is not None:
                solver.parameters.max_time_in_seconds = float(self.config.max_seconds)
            solver.parameters.log_search_progress = self.config.log_search_progress
            if self.random_seed is not None:
                solver.parameters.random_seed = int(self.random_seed)
            if self.mip_gap is not None:
                solver.parameters.relative_gap_limit = float(self.mip_gap)
            solver.Solve(self.model)
            return solver

        total_limit = float(self.config.max_seconds) if self.config.max_seconds else None
        per_stage = None
        if total_limit and total_limit > 0:
            per_stage = max(1.0, total_limit / len(stages))

        clear_hints = getattr(self.model, "ClearHints", None)
        if callable(clear_hints):
            clear_hints()

        last_solver: cp_model.CpSolver | None = None
        solver: cp_model.CpSolver | None = None

        for index, (key, expr) in enumerate(stages, start=1):
            self.model.Minimize(expr)

            solver = cp_model.CpSolver()
            if per_stage is not None:
                solver.parameters.max_time_in_seconds = float(per_stage)
            elif self.config.max_seconds is not None:
                solver.parameters.max_time_in_seconds = float(self.config.max_seconds)
            solver.parameters.log_search_progress = self.config.log_search_progress
            if self.random_seed is not None:
                solver.parameters.random_seed = int(self.random_seed)
            if self.mip_gap is not None:
                solver.parameters.relative_gap_limit = float(self.mip_gap)

            if last_solver is not None and self.assignment_vars:
                if callable(clear_hints):
                    clear_hints()
                for var in self.assignment_vars.values():
                    try:
                        hint_val = last_solver.Value(var)
                    except Exception:
                        continue
                    self.model.AddHint(var, hint_val)

            status = solver.Solve(self.model)
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                return solver

            best_val = solver.ObjectiveValue()
            try:
                best_int = int(round(best_val))
            except (TypeError, ValueError):
                best_int = None
            if best_int is not None:
                self.model.Add(expr <= best_int)

            last_solver = solver
            logger.debug(
                "Lexicographic stage %d/%d (%s): objective=%s",
                index,
                len(stages),
                key,
                best_val,
            )

        assert solver is not None
        return last_solver if last_solver is not None else solver

    def extract_assignments(self, solver: cp_model.CpSolver) -> pd.DataFrame:
        """Costruisce un DataFrame con le assegnazioni attive nel modello risolto."""
        if not self.assignment_vars:
            return pd.DataFrame(columns=["employee_id", "shift_id"])

        shift_lookup = self.shifts.set_index("shift_id")
        extra_cols = [c for c in ["day", "start_dt", "end_dt", "duration_h", "role", "required_staff"] if c in shift_lookup.columns]
        rows = []
        for (emp_id, shift_id), var in self.assignment_vars.items():
            if solver.Value(var):
                row = {"employee_id": emp_id, "shift_id": shift_id}
                if shift_id in shift_lookup.index:
                    info = shift_lookup.loc[shift_id]
                    for col in extra_cols:
                        row[col] = info[col]
                rows.append(row)

        columns = ["employee_id", "shift_id"] + extra_cols
        if not rows:
            return pd.DataFrame(columns=columns)
        return pd.DataFrame(rows, columns=columns)

    def extract_overtime_summary(self, solver: cp_model.CpSolver) -> pd.DataFrame:
        """Restituisce i minuti di straordinario assegnati per dipendente."""
        if not self.overtime_vars:
            return pd.DataFrame(columns=["employee_id", "overtime_minutes", "overtime_hours"])

        rows = []
        for emp_id, var in self.overtime_vars.items():
            minutes = solver.Value(var)
            rows.append(
                {"employee_id": emp_id, "overtime_minutes": minutes, "overtime_hours": minutes / 60.0}
            )

        return pd.DataFrame(rows, columns=["employee_id", "overtime_minutes", "overtime_hours"])

    def extract_shortfall_summary(self, solver: cp_model.CpSolver) -> pd.DataFrame:
        """Riepiloga la scopertura residua per ciascun turno."""
        columns = ["shift_id", "shortfall_units", "shortfall_staff_minutes"]

        slot_shortfall_vars = getattr(self, "slot_shortfall_vars", None) or {}
        if slot_shortfall_vars:
            segment_owner: dict[str, str] = {}
            if self.adaptive_slot_data is not None:
                segment_owner = getattr(self.adaptive_slot_data, "segment_owner", {}) or {}
            if not segment_owner and getattr(self, "shift_to_covering_segments", None):
                for shift_id, segments in self.shift_to_covering_segments.items():
                    for segment_id in segments:
                        segment_owner.setdefault(segment_id, shift_id)

            totals: dict[str, int] = {}
            for slot_id, var in slot_shortfall_vars.items():
                units = int(solver.Value(var))
                if units <= 0:
                    continue

                owner_shift = segment_owner.get(slot_id)
                if owner_shift is None and isinstance(slot_id, str) and "__" in slot_id:
                    owner_shift = slot_id.split("__", 1)[0]
                if owner_shift is None:
                    owner_shift = str(slot_id)

                shift_key = str(owner_shift)
                totals[shift_key] = totals.get(shift_key, 0) + units

            if not totals:
                return pd.DataFrame(columns=columns)

            rows = []
            for shift_id, total_units in totals.items():
                if total_units <= 0:
                    continue
                duration = self.duration_minutes.get(shift_id)
                if duration is None:
                    duration = self.duration_minutes.get(str(shift_id), 0)
                rows.append(
                    {
                        "shift_id": shift_id,
                        "shortfall_units": total_units,
                        "shortfall_staff_minutes": total_units * (duration or 0),
                    }
                )

            if not rows:
                return pd.DataFrame(columns=columns)

            return pd.DataFrame(rows, columns=columns)

        if not self.shortfall_vars:
            return pd.DataFrame(columns=columns)

        rows = []
        for shift_id, var in self.shortfall_vars.items():
            units = solver.Value(var)
            if units <= 0:
                continue
            duration = self.duration_minutes.get(shift_id, 0)
            rows.append(
                {"shift_id": shift_id, "shortfall_units": units, "shortfall_staff_minutes": units * duration}
            )

        if not rows:
            return pd.DataFrame(columns=columns)

        return pd.DataFrame(rows, columns=columns)

    def extract_preference_summary(self, solver: cp_model.CpSolver) -> pd.DataFrame:
        """Riepiloga l'applicazione delle preferenze per dipendente."""
        if not self.assignment_vars:
            return pd.DataFrame(columns=["employee_id", "liked_assigned", "disliked_assigned", "total_score"])

        rows = []
        for emp_id in self.employees["employee_id"]:
            pairs = self._vars_by_emp.get(emp_id, [])
            if not pairs:
                continue

            liked = 0
            disliked = 0
            total_score = 0
            for shift_id, var in pairs:
                if not solver.Value(var):
                    continue
                score = self.preference_score_by_pair.get((emp_id, shift_id), 0)
                total_score += score
                if score > 0:
                    liked += 1
                elif score < 0:
                    disliked += 1

            if liked or disliked or total_score:
                rows.append(
                    {
                        "employee_id": emp_id,
                        "liked_assigned": liked,
                        "disliked_assigned": disliked,
                        "total_score": total_score,
                    }
                )

        if not rows:
            return pd.DataFrame(columns=["employee_id", "liked_assigned", "disliked_assigned", "total_score"])

        return pd.DataFrame(rows, columns=["employee_id", "liked_assigned", "disliked_assigned", "total_score"])

    def extract_skill_coverage_summary(self, solver: cp_model.CpSolver) -> pd.DataFrame:
        columns = ["shift_id", "skill", "required", "covered", "shortfall"]
        if not self.shift_skill_requirements:
            return pd.DataFrame(columns=columns)

        rows = []
        for shift_id, requirements in self.shift_skill_requirements.items():
            if not requirements:
                continue
            vars_with_emp = self._vars_by_shift_emp.get(shift_id, [])
            for skill_name, required in requirements.items():
                covered = 0
                for emp_id, var in vars_with_emp:
                    if skill_name in self.emp_skills.get(emp_id, set()):
                        covered += int(solver.Value(var))
                shortfall = 0
                if (shift_id, skill_name) in self.skill_shortfall_vars:
                    shortfall = int(solver.Value(self.skill_shortfall_vars[(shift_id, skill_name)]))
                rows.append(
                    {
                        "shift_id": shift_id,
                        "skill": skill_name,
                        "required": int(required),
                        "covered": covered,
                        "shortfall": shortfall,
                    }
                )

        if not rows:
            return pd.DataFrame(columns=columns)
        return pd.DataFrame(rows, columns=columns)

    def log_employee_summary(self, solver: cp_model.CpSolver) -> None:
        """Logga minuti assegnati, straordinari e notti per dipendente (totali e per settimana)."""
        if not self._vars_by_emp:
            print("\nNessun dipendente con variabili attive.")
            return

        start_dt_map = dict(zip(self.shifts["shift_id"], self.shifts["start_dt"]))
        print("\n=== Consumi per dipendente ===")
        for emp_id in self.employees["employee_id"]:
            pairs = self._vars_by_emp.get(emp_id, [])
            assigned_minutes = 0
            week_minutes: dict[tuple[int, int], int] = {}
            nights_assigned = 0

            for shift_id, var in pairs:
                val = solver.Value(var)
                if not val:
                    continue
                minutes = self.duration_minutes.get(shift_id, 0) * val
                assigned_minutes += minutes

                start_dt = start_dt_map.get(shift_id)
                if start_dt is not None and hasattr(start_dt, "isocalendar"):
                    iso = start_dt.isocalendar()
                    week_key = (iso[0], iso[1])
                    week_minutes[week_key] = week_minutes.get(week_key, 0) + minutes

                if shift_id in self._night_shift_ids:
                    nights_assigned += val

            overtime_minutes = solver.Value(self.overtime_vars.get(emp_id)) if emp_id in self.overtime_vars else 0

            if assigned_minutes == 0 and overtime_minutes == 0 and nights_assigned == 0:
                continue

            assigned_hours = assigned_minutes / 60.0
            overtime_hours = overtime_minutes / 60.0

            week_parts = []
            for (year, week), minutes in sorted(week_minutes.items()):
                week_parts.append(f"{year}-W{week:02d}:{minutes/60.0:.1f}h")
            weeks_str = "; ".join(week_parts) if week_parts else "-"

            print("- {emp}: {assign_min} min ({assign_h:.1f}h), straordinario {ot_min} min ({ot_h:.1f}h), notti {n}".format(
                emp=emp_id,
                assign_min=assigned_minutes,
                assign_h=assigned_hours,
                ot_min=overtime_minutes,
                ot_h=overtime_hours,
                n=nights_assigned,
            ))
            print(f"  settimane: {weeks_str}")

    def verify_aggregate_variables(self, solver: cp_model.CpSolver) -> bool:
        """Verifica che y[s] = sum_e x[e,s] per ogni turno."""
        all_correct = True
        
        for shift_id, y_var in self.shift_aggregate_vars.items():
            y_value = solver.Value(y_var)
            
            # Calcola la somma delle variabili x[e,s] per questo turno
            vars_for_shift = self._vars_by_shift.get(shift_id, [])
            x_sum = sum(solver.Value(var) for var in vars_for_shift)
            
            if y_value != x_sum:
                print(f"[ERROR] Turno {shift_id}: y[s]={y_value} != sum(x[e,s])={x_sum}")
                all_correct = False
            else:
                print(f"[OK] Turno {shift_id}: y[s]={y_value} == sum(x[e,s])={x_sum}")
        
        return all_correct

    def _initialize_objective_weights_minutes(self) -> None:
        """Inizializza pesi obiettivo in persona-minuti da config (persona-ora)."""
        self.objective_weights_minutes = {}
        
        # I pesi effettivi sono in self.objective_weights (scala *100 rispetto alle ore).
        for key, weight_value in self.objective_weights.items():
            try:
                weight_per_hour = float(weight_value) / 100.0
            except (TypeError, ValueError):
                logger.debug("Peso obiettivo %s non numerico (%s), ignoro", key, weight_value)
                continue
            weight_per_minute = _weight_per_hour_to_minutes(weight_per_hour)
            if weight_per_minute > 0:
                self.objective_weights_minutes[key] = weight_per_minute
        
        logger.info(
            "Pesi obiettivo (persona-minuto): %s",
            {k: f"{v:.4f}" for k, v in self.objective_weights_minutes.items()}
        )


    def extract_objective_breakdown(self, solver: cp_model.CpSolver) -> dict[str, dict[str, float]]:
        """Calcola breakdown dettagliato dell'obiettivo per componente."""
        breakdown = {}
        
        # 1. Finestre (modalitÃƒÂ  unica segmenti)
        window_minutes = 0
        window_cost = 0.0
        if hasattr(self, 'segment_shortfall_vars') and self.segment_shortfall_vars:
            weight_per_min = self.objective_weights_minutes.get("unmet_window", 0.0)
            for segment_id, var in self.segment_shortfall_vars.items():
                shortfall_units = solver.Value(var)
                if shortfall_units > 0:
                    segment_duration = self._get_segment_duration_minutes(segment_id)
                    minutes = shortfall_units * segment_duration
                    window_minutes += minutes
                    window_cost += minutes * weight_per_min
        
        breakdown["unmet_window"] = {
            "minutes": window_minutes,
            "cost": window_cost,
            "weight_per_min": self.objective_weights_minutes.get("unmet_window", 0.0)
        }
        
        # 2. Turni (shortfall hard)
        shift_minutes = 0
        shift_cost = 0.0
        weight_per_min = self.objective_weights_minutes.get("unmet_demand", 0.0)
        for shift_id, var in self.shortfall_vars.items():
            shortfall_units = solver.Value(var)
            if shortfall_units > 0:
                shift_duration = self.duration_minutes.get(shift_id, self.avg_shift_minutes)
                minutes = shortfall_units * shift_duration
                shift_minutes += minutes
                shift_cost += minutes * weight_per_min
        
        breakdown["unmet_demand"] = {
            "minutes": shift_minutes,
            "cost": shift_cost,
            "weight_per_min": weight_per_min
        }
        
        # 3. Skill shortfall
        skill_minutes = 0
        skill_cost = 0.0
        weight_per_min = self.objective_weights_minutes.get("unmet_skill", 0.0)
        for (shift_id, skill_name), var in self.skill_shortfall_vars.items():
            shortfall_units = solver.Value(var)
            if shortfall_units > 0:
                shift_duration = self.duration_minutes.get(shift_id, self.avg_shift_minutes)
                minutes = shortfall_units * shift_duration
                skill_minutes += minutes
                skill_cost += minutes * weight_per_min
        
        breakdown["unmet_skill"] = {
            "minutes": skill_minutes,
            "cost": skill_cost,
            "weight_per_min": weight_per_min
        }
        
        # 4. Utilizzo risorse esterne
        external_minutes = 0
        external_cost = 0.0
        weight_per_min = self.objective_weights_minutes.get("external_use", 0.0)
        for emp_id, var in self.external_minutes_vars.items():
            minutes = solver.Value(var)
            if minutes > 0:
                external_minutes += minutes
                external_cost += minutes * weight_per_min

        breakdown["external_use"] = {
            "minutes": external_minutes,
            "cost": external_cost,
            "weight_per_min": weight_per_min,
        }

        # 5. Overstaff
        overstaff_minutes = 0
        overstaff_cost = 0.0
        weight_per_min = self.objective_weights_minutes.get("overstaff", 0.0)

        if self.shift_overstaff_vars:
            for shift_id, var in self.shift_overstaff_vars.items():
                units = solver.Value(var)
                if units > 0:
                    duration = self.duration_minutes.get(shift_id, self.avg_shift_minutes)
                    minutes = units * duration
                    overstaff_minutes += minutes
                    overstaff_cost += minutes * weight_per_min

        segment_vars = getattr(self, "segment_overstaff_vars", None)
        if segment_vars:
            for segment_id, var in segment_vars.items():
                value = solver.Value(var)
                if value > 0:
                    overstaff_minutes += value
                    overstaff_cost += value * weight_per_min

        breakdown["overstaff"] = {
            "minutes": overstaff_minutes,
            "cost": overstaff_cost,
            "weight_per_min": weight_per_min
        }

        # 7. Straordinari
        overtime_minutes = 0
        overtime_cost = 0.0
        weight_per_min = self.objective_weights_minutes.get("overtime", 0.0)
        for emp_id, var in self.overtime_vars.items():
            ot_minutes = solver.Value(var)
            if ot_minutes > 0:
                overtime_minutes += ot_minutes
                overtime_cost += ot_minutes * weight_per_min

        breakdown["overtime"] = {
            "minutes": overtime_minutes,
            "cost": overtime_cost,
            "weight_per_min": weight_per_min
        }

        # 8. Preferenze (violazioni pesate)
        pref_violations = 0
        pref_cost = 0.0
        weight_per_min = self.objective_weights_minutes.get("preferences", 0.0)
        for (emp_id, shift_id), var in self.assignment_vars.items():
            if solver.Value(var):
                score = self.preference_score_by_pair.get((emp_id, shift_id), 0)
                if score < 0:
                    pref_violations += abs(score)
                    pref_cost += abs(score) * self.mean_shift_minutes * weight_per_min

        breakdown["preferences"] = {
            "violations": pref_violations,
            "cost": pref_cost,
            "weight_per_min": weight_per_min,
            "mean_shift_minutes": self.mean_shift_minutes
        }

        # 9. Fairness (deviazioni workload)
        fairness_deviations = 0
        fairness_cost = 0.0
        weight_per_min = self.objective_weights_minutes.get("fairness", 0.0)
        for var in self.workload_dev_vars:
            deviation = solver.Value(var)
            if deviation > 0:
                fairness_deviations += deviation
                fairness_cost += deviation * weight_per_min

        breakdown["fairness"] = {
            "deviations_minutes": fairness_deviations,
            "cost": fairness_cost,
            "weight_per_min": weight_per_min
        }

        return breakdown

    def log_objective_breakdown(self, solver: cp_model.CpSolver) -> None:
        """Log compatto del breakdown obiettivo post-solve."""
        breakdown = self.extract_objective_breakdown(solver)
        
        print("\n=== Breakdown Obiettivo (persona-minuti) ===")
        total_cost = 0.0
        
        for component, data in breakdown.items():
            cost = data.get("cost", 0.0)
            total_cost += cost
            
            if component == "preferences":
                violations = data.get("violations", 0)
                mean_min = data.get("mean_shift_minutes", 0)
                print(f"- {component:12}: {violations:3d} violazioni Ãƒâ€” {mean_min:3d}min = {cost:8.4f}")
            elif component == "fairness":
                dev_min = data.get("deviations_minutes", 0)
                print(f"- {component:12}: {dev_min:6.0f} dev-min = {cost:8.4f}")
            else:
                minutes = data.get("minutes", 0)
                print(f"- {component:12}: {minutes:6.0f} min = {cost:8.4f}")
        
        print(f"- {'TOTALE':12}: {total_cost:8.4f}")
        
        # Top-5 componenti piÃƒÂ¹ costosi
        sorted_components = sorted(
            [(k, v.get("cost", 0.0)) for k, v in breakdown.items()],
            key=lambda x: x[1],
            reverse=True
        )
        top_5 = [f"{comp}({cost:.3f})" for comp, cost in sorted_components[:5] if cost > 0]
        if top_5:
            print(f"Top-5 costi: {', '.join(top_5)}")

    def export_objective_breakdown_csv(self, solver: cp_model.CpSolver, output_path: Path) -> None:
        """Export breakdown obiettivo in CSV."""
        breakdown = self.extract_objective_breakdown(solver)
        
        # Crea directory se non esiste
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        rows = []
        for component, data in breakdown.items():
            row = {
                "component": component,
                "cost": data.get("cost", 0.0),
                "weight_per_min": data.get("weight_per_min", 0.0),
            }
            
            if component == "preferences":
                row.update({
                    "violations": data.get("violations", 0),
                    "mean_shift_minutes": data.get("mean_shift_minutes", 0),
                    "minutes": data.get("violations", 0) * data.get("mean_shift_minutes", 0)
                })
            elif component == "fairness":
                row.update({
                    "deviations_minutes": data.get("deviations_minutes", 0),
                    "minutes": data.get("deviations_minutes", 0)
                })
            else:
                row.update({
                    "minutes": data.get("minutes", 0)
                })
            
            rows.append(row)
        
        # Aggiungi riga totale
        total_cost = sum(data.get("cost", 0.0) for data in breakdown.values())
        total_minutes = sum(row.get("minutes", 0) for row in rows)
        rows.append({
            "component": "TOTAL",
            "cost": total_cost,
            "minutes": total_minutes,
            "weight_per_min": 0.0
        })
        
        df = pd.DataFrame(rows)
        df.to_csv(output_path, index=False, float_format="%.6f")
        print(f"Breakdown obiettivo salvato in {output_path}")


def _load_data(
    data_dir: Path,
    global_min_rest_hours: float,
    cfg: config_loader.Config,
) -> tuple[
    pd.DataFrame,  # employees
    pd.DataFrame,  # shifts
    pd.DataFrame,  # availability
    pd.DataFrame,  # assign_mask
    pd.DataFrame,  # rest_conflicts
    pd.DataFrame,  # overtime_costs
    pd.DataFrame,  # preferences
    dict[str, set[str]],  # emp_skills
    dict[str, dict[str, int]],  # shift_skill_req
    dict[str, int],  # window_demand_map
    dict[str, int],  # window_duration_map
    dict[str, dict[str, int]],  # window_skill_requirements
    precompute.AdaptiveSlotData | None,  # adaptive_slot_data
    pd.DataFrame,  # windows_df
]:
    employees = loader.load_employees(data_dir / "employees.csv")
    shifts = loader.load_shifts(data_dir / "shifts.csv", max_daily_hours=getattr(cfg.hours, "max_daily", None))
    availability = loader.load_availability(data_dir / "availability.csv", employees, shifts)

    shifts_norm = precompute.normalize_shift_times(shifts)
    quali_mask = loader.build_quali_mask(employees, shifts)
    assign_mask = loader.merge_availability(quali_mask, availability)
    time_off = loader.load_time_off(data_dir / "time_off.csv", employees)
    assign_mask = loader.apply_time_off(assign_mask, time_off, shifts_norm)

    preferences_raw = loader.load_preferences(data_dir / "preferences.csv", employees, shifts)
    assignable_pairs = assign_mask[assign_mask["can_assign"] == 1][["employee_id", "shift_id"]].drop_duplicates().copy()
    preferences_filtered = assignable_pairs.merge(
        preferences_raw, on=["employee_id", "shift_id"], how="left"
    )
    if not preferences_filtered.empty:
        preferences_filtered["score"] = (
            pd.to_numeric(preferences_filtered["score"], errors="coerce")
            .fillna(0)
            .astype(int)
        )
    else:
        preferences_filtered = assignable_pairs.assign(score=0)

    # NUOVO: Usa windows.csv invece di demand_windows.csv (obsoleto)
    coverage_source = getattr(cfg.shifts, "coverage_source", "windows")
    coverage_source = coverage_source.strip().lower()
    use_windows = coverage_source == "windows"

    window_demand_map: dict[str, int] = {}
    window_duration_map: dict[str, int] = {}
    window_skill_req: dict[str, dict[str, int]] = {}
    adaptive_data: precompute.AdaptiveSlotData | None = None

    windows_df: pd.DataFrame
    if use_windows:
        windows_path = data_dir / "windows.csv"
        windows_df = loader.load_windows(windows_path, shifts_norm)

        if not windows_df.empty:
            window_demand_map = {
                str(row["window_id"]): int(row["window_demand"])
                for _, row in windows_df.iterrows()
                if int(row["window_demand"]) > 0
            }
            window_duration_map = {
                str(row["window_id"]): int(row["window_minutes"])
                for _, row in windows_df.iterrows()
            }

            if "skill_requirements" in windows_df.columns:
                for _, row in windows_df.iterrows():
                    demand = window_demand_map.get(str(row["window_id"]), 0)
                    if demand <= 0:
                        continue
                    requirements = row.get("skill_requirements")
                    if not isinstance(requirements, dict):
                        continue
                    cleaned: dict[str, int] = {}
                    for skill_name, qty in requirements.items():
                        try:
                            qty_int = int(qty)
                        except (TypeError, ValueError):
                            continue
                        if qty_int > 0:
                            cleaned[str(skill_name)] = qty_int
                    if cleaned:
                        window_skill_req[str(row["window_id"])] = cleaned

            try:
                adaptive_data = precompute.build_adaptive_slots(shifts_norm, cfg, windows_df)
                adaptive_data, _, _ = precompute.map_windows_to_slots(adaptive_data, windows_df, merge_signatures=True)
            except Exception as exc:  # pragma: no cover - diagnostica
                warnings.warn(
                    f"Impossibile costruire gli slot adattivi: {exc}",
                    RuntimeWarning,
                )
                adaptive_data = None
    else:
        windows_df = pd.DataFrame()
        windows_path = data_dir / "windows.csv"
        if windows_path.exists():
            logger.info("coverage_source='shifts': ignoro windows.csv per la domanda")

    emp_skills = {
        str(row["employee_id"]): set(row.get("skills_set", set()))
        for _, row in employees.iterrows()
    }

    if "skill_requirements" in shifts_norm.columns:
        shift_skill_req = {
            str(row["shift_id"]): dict(row["skill_requirements"]) if isinstance(row["skill_requirements"], dict) else {}
            for _, row in shifts_norm.iterrows()
        }
    else:
        shift_skill_req = {}

    # Map shifts to windows via demand_id (for legacy compatibility)

    rest_conflicts = precompute.conflict_pairs_for_rest(shifts_norm, global_min_rest_hours)
    overtime_costs = loader.load_overtime_costs(data_dir / "overtime_costs.csv")

    return (
        employees,
        shifts_norm,
        availability,
        assign_mask,
        rest_conflicts,
        overtime_costs,
        preferences_filtered,
        emp_skills,
        shift_skill_req,
        window_demand_map,
        window_duration_map,
        window_skill_req,
        adaptive_data,
        windows_df,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Solver CP-SAT per shift scheduling")
    parser.add_argument("--config", type=str, default=None, help="Percorso del file di configurazione YAML/JSON")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Cartella con i CSV di input")
    parser.add_argument("--max-seconds", type=float, default=None, help="Tempo massimo di risoluzione (sovrascrive la configurazione)")
    parser.add_argument("--log-search", action="store_true", help="Mostra i log del solver CP-SAT")
    parser.add_argument("--output", type=Path, default=None, help="Percorso di salvataggio per le assegnazioni in CSV")
    parser.add_argument("--global-rest-hours", type=float, default=None, help="Soglia globale di riposo minimo (ore)")
    parser.add_argument("--overtime-priority", type=float, default=None, help="Peso per penalizzare lo straordinario (costo per persona-ora)")
    parser.add_argument("--fairness-weight", type=float, default=None, help="Peso per la fairness nel carico di lavoro (persona-ora)")
    parser.add_argument("--preferences-weight", type=float, default=None, help="Peso per le preferenze di assegnazione (persona-ora)")
    parser.add_argument("--default-ot-weight", type=int, default=None, help="Peso predefinito per costo straordinario se il ruolo manca")
    parser.add_argument("--global-ot-cap-hours", type=float, default=None, help="Tetto settimanale globale di straordinari (ore)")
    args = parser.parse_args(argv)

    cfg = config_loader.load_config(args.config)
    resolved_cfg = config_loader.config_to_dict(cfg)

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, force=True)
    level_name = resolved_cfg["logging"]["level"]
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)

    default_time_limit = 30.0
    max_seconds = args.max_seconds if args.max_seconds is not None else (cfg.solver.time_limit_sec if cfg.solver.time_limit_sec is not None else default_time_limit)
    global_rest_hours = args.global_rest_hours if args.global_rest_hours is not None else cfg.rest.min_between_shifts
    default_ot_weight = args.default_ot_weight if args.default_ot_weight is not None else DEFAULT_OVERTIME_COST_WEIGHT

    global_ot_cap_minutes = None
    if args.global_ot_cap_hours is not None:
        global_ot_cap_minutes = max(0, int(round(args.global_ot_cap_hours * 60)))

    penalties = {
        "unmet_window": cfg.penalties.unmet_window,
        "unmet_demand": cfg.penalties.unmet_demand,
        "unmet_skill": cfg.penalties.unmet_skill,
        "overstaff": cfg.penalties.overstaff,
        "overtime": args.overtime_priority if args.overtime_priority is not None else cfg.penalties.overtime,
        "external_use": cfg.penalties.external_use,
        "fairness": args.fairness_weight if args.fairness_weight is not None else cfg.penalties.fairness,
        "preferences": args.preferences_weight if args.preferences_weight is not None else cfg.penalties.preferences,
    }

    objective_priority = list(cfg.objective.priority)
    objective_weights = _build_objective_weights(objective_priority, penalties)

    solver_cfg = SolverConfig(
        max_seconds=max_seconds,
        log_search_progress=args.log_search,
        global_min_rest_hours=global_rest_hours,
        overtime_priority=objective_weights.get("overtime", 0),
        shortfall_priority=objective_weights.get("unmet_demand", 0),
        window_shortfall_priority=objective_weights.get("unmet_window", 0),
        skill_shortfall_priority=objective_weights.get("unmet_skill", 0),
        external_use_weight=objective_weights.get("external_use", 0),
        preferences_weight=objective_weights.get("preferences", 0),
        fairness_weight=objective_weights.get("fairness", 0),
        default_overtime_cost_weight=default_ot_weight,
        global_overtime_cap_minutes=global_ot_cap_minutes,
        random_seed=cfg.random.seed,
        mip_gap=cfg.solver.mip_gap,
        skills_slack_enabled=cfg.skills.enable_slack,
        objective_priority=tuple(objective_priority),
        objective_mode=cfg.objective.mode,
    )

    summary = {
        "priority": objective_priority,
        "penalties": penalties,
        "rest_min_hours": global_rest_hours,
        "solver_time_limit": max_seconds,
        "solver_mip_gap": cfg.solver.mip_gap,
        "random_seed": cfg.random.seed,
        "skills_slack_enabled": cfg.skills.enable_slack,
        "objective_mode": cfg.objective.mode,
    }
    logger.info("Configurazione risolta: %s", summary)

    (
        employees,
        shifts_norm,
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
    ) = _load_data(args.data_dir, solver_cfg.global_min_rest_hours, cfg)

    skill_emp_with_tags = sum(1 for skills in emp_skills.values() if skills)
    skill_shift_with_req = sum(1 for req in shift_skill_req.values() if req)
    if skill_shift_with_req:
        sample_items: list[str] = []
        for sid, req in shift_skill_req.items():
            if not req:
                continue
            sample_items.append(f"{sid}:{dict(list(req.items())[:2])}")
            if len(sample_items) >= 3:
                break
        logger.info(
            "Requisiti skill attivi su %d turni (%d dipendenti con skill); esempi: %s",
            skill_shift_with_req,
            skill_emp_with_tags,
            ", ".join(sample_items) if sample_items else "-",
        )
    else:
        logger.info("Nessun requisito di skill attivo nei dati di input")

    if window_demand_map:
        window_count = len(window_demand_map)
        logger.info("Domande aggregate attive: %d finestre", window_count)
    else:
        logger.info("Nessuna domanda aggregata attiva")

    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts_norm,
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
    
    # Imposta demand_mode dal config
    solver.demand_mode = cfg.shifts.demand_mode
    solver.build()
    cp_solver = solver.solve()

    status = cp_solver.StatusName()
    print("Stato solver:", status)
    if status not in {"OPTIMAL", "FEASIBLE"}:
        return 1

    # Generazione report diagnostici tramite ScheduleReporter
    try:
        from . import reporting
    except ImportError:
        import reporting
    
    # Usa directory reports di default se non specificata
    report_dir = Path("reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    
    # Salvataggio delle assegnazioni
    assignments_df = solver.extract_assignments(cp_solver)
    if args.output is not None:
        output_path = args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        assignments_df.to_csv(output_path, index=False)
        print(f"Assegnazioni salvate in {output_path}")
    elif not assignments_df.empty:
        print("Assegnazioni attive (prime 10 righe):")
        print(assignments_df.head(10).to_string(index=False))

    reporter = reporting.ScheduleReporter(
        solver,
        cp_solver,
        assignments_df=assignments_df,
        windows_df=windows_df,
    )

    # Generazione report CSV
    reporter.generate_segment_coverage_report()
    reporter.generate_constraint_report()
    reporter.generate_objective_breakdown()
    
    # Log breakdown obiettivo direttamente dal solver
    solver.log_objective_breakdown(cp_solver)

    # Report dettagliati sempre abilitati
    overtime_df = solver.extract_overtime_summary(cp_solver)
    if not overtime_df.empty:
        overtime_df.to_csv(report_dir / "overtime_report.csv", index=False)

    shortfall_df = solver.extract_shortfall_summary(cp_solver)
    if not shortfall_df.empty:
        shortfall_df.to_csv(report_dir / "shortfall_report.csv", index=False)

    skill_df = solver.extract_skill_coverage_summary(cp_solver)
    if not skill_df.empty:
        skill_df.to_csv(report_dir / "skill_coverage_report.csv", index=False)

    preference_df = solver.extract_preference_summary(cp_solver)
    if not preference_df.empty:
        preference_df.to_csv(report_dir / "preference_report.csv", index=False)

    # Verifica variabili aggregate
    print("\n=== Verifica variabili aggregate y[s] ===")
    if solver.verify_aggregate_variables(cp_solver):
        print("Ã¢Å“â€œ Tutte le variabili aggregate sono corrette: y[s] = sum_e x[e,s]")
    else:
        print("Ã¢Å“â€” Errore nelle variabili aggregate!")

    return 0



if __name__ == "__main__":
    raise SystemExit(main())
