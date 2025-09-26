"""CP-SAT shift scheduling model (MVP).

Questo modulo definisce lo scheletro del solver basato su OR-Tools.
Il modello prende in ingresso i DataFrame risultanti da loader.precompute.
"""
from __future__ import annotations

import argparse
import logging
import warnings
from datetime import datetime, timedelta
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

def _weight_per_hour_to_int(weight_hour: float | int | None) -> int:
    """Legacy: converte peso da persona-ora a intero scalato (per compatibilità)."""
    if weight_hour is None:
        return 0
    value = float(weight_hour)
    if value <= 0:
        return 0
    scaled = value * OBJECTIVE_MINUTE_SCALE / 60.0
    return max(1, int(round(scaled)))


BASE_WINDOW_WEIGHT_H = 2.0
BASE_SHIFT_WEIGHT_H = 1.0
BASE_SKILL_WEIGHT_H = 0.8
BASE_SHIFT_SOFT_WEIGHT_H = 0.6
BASE_OVERTIME_WEIGHT_H = 0.3
BASE_FAIRNESS_WEIGHT_H = 0.05
BASE_PREFERENCES_WEIGHT_H = 0.05

DEFAULT_WINDOW_SHORTFALL_PRIORITY = _weight_per_hour_to_int(BASE_WINDOW_WEIGHT_H)
DEFAULT_SHORTFALL_PRIORITY = _weight_per_hour_to_int(BASE_SHIFT_WEIGHT_H)
DEFAULT_SKILL_SHORTFALL_PRIORITY = _weight_per_hour_to_int(BASE_SKILL_WEIGHT_H)
DEFAULT_SHIFT_SOFT_PRIORITY = _weight_per_hour_to_int(BASE_SHIFT_SOFT_WEIGHT_H)
DEFAULT_OVERTIME_PRIORITY = _weight_per_hour_to_int(BASE_OVERTIME_WEIGHT_H)
DEFAULT_FAIRNESS_WEIGHT = _weight_per_hour_to_int(BASE_FAIRNESS_WEIGHT_H)
DEFAULT_PREFERENCES_WEIGHT = _weight_per_hour_to_int(BASE_PREFERENCES_WEIGHT_H)
DEFAULT_OVERTIME_COST_WEIGHT = _weight_per_hour_to_int(BASE_OVERTIME_WEIGHT_H)
DEFAULT_GLOBAL_OVERTIME_CAP_MINUTES = None
DEFAULT_OBJECTIVE_PRIORITY = tuple(config_loader.PRIORITY_KEYS)

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _build_objective_weights(priority: Sequence[str], penalties: Mapping[str, float | int]) -> dict[str, int]:
    weights: dict[str, int] = {}
    for key in priority:
        weight = _weight_per_hour_to_int(penalties.get(key))
        if weight > 0:
            weights[key] = weight
    for key, penalty in penalties.items():
        if key in weights:
            continue
        weight = _weight_per_hour_to_int(penalty)
        if weight > 0:
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
    shift_shortfall_priority: int = DEFAULT_SHIFT_SOFT_PRIORITY
    preferences_weight: int = DEFAULT_PREFERENCES_WEIGHT
    fairness_weight: int = DEFAULT_FAIRNESS_WEIGHT
    default_overtime_cost_weight: int = DEFAULT_OVERTIME_COST_WEIGHT
    global_overtime_cap_minutes: int | None = None
    random_seed: int | None = None
    mip_gap: float | None = None
    skills_slack_enabled: bool = True
    objective_priority: tuple[str, ...] = DEFAULT_OBJECTIVE_PRIORITY


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
        window_demands: Mapping[str, int] | None = None,
        window_shifts: Mapping[str, Sequence[str]] | None = None,
        window_duration_minutes: Mapping[str, int] | None = None,
        shift_soft_demands: Mapping[str, int] | None = None,
        config: SolverConfig | None = None,
        objective_priority: Sequence[str] | None = None,
        objective_weights: Mapping[str, int] | None = None,
        # Nuovi parametri per slot adattivi
        adaptive_slot_data: object | None = None,
        slots_in_window: Mapping[str, Sequence[str]] | None = None,
        coverage_mode: str = "disabled",
        enable_slot_slack: bool = True,
        # Nuovo parametro per integrità turni
        preserve_shift_integrity: bool = True,
    ) -> None:
        self.employees = employees
        self.shifts = shifts
        self.assign_mask = assign_mask
        self.rest_conflicts = rest_conflicts
        self.overtime_costs = overtime_costs
        self.preferences = preferences
        self.config = config or SolverConfig()

        self.emp_skills = {str(emp_id): set(skills) for emp_id, skills in (emp_skills or {}).items()}
        self.shift_skill_requirements = {
            str(shift_id): {str(skill): int(qty) for skill, qty in reqs.items() if int(qty) > 0}
            for shift_id, reqs in (shift_skill_requirements or {}).items()
        }
        valid_shift_ids = set(self.shifts["shift_id"].astype(str)) if "shift_id" in self.shifts.columns else set()
        self.window_demands = {}
        if window_demands:
            for window_id, demand in window_demands.items():
                demand_int = int(demand)
                if demand_int > 0:
                    self.window_demands[str(window_id)] = demand_int
        provided_window_shifts = window_shifts or {}
        self.window_shifts = {
            str(window_id): [str(shift_id) for shift_id in shifts_list if str(shift_id) in valid_shift_ids]
            for window_id, shifts_list in provided_window_shifts.items()
        }
        window_duration_src = window_duration_minutes or {}
        self.window_duration_minutes = {
            str(window_id): max(1, int(duration))
            for window_id, duration in window_duration_src.items()
            if int(duration) > 0
        }
        for window_id in self.window_demands:
            self.window_shifts.setdefault(window_id, [])
            self.window_duration_minutes.setdefault(window_id, 60)
        self.shift_soft_demands = {}
        if shift_soft_demands:
            for shift_id, demand in shift_soft_demands.items():
                demand_int = int(demand)
                if demand_int > 0 and (not valid_shift_ids or str(shift_id) in valid_shift_ids):
                    self.shift_soft_demands[str(shift_id)] = demand_int

        priority = list(objective_priority) if objective_priority is not None else list(self.config.objective_priority)
        self.objective_priority = priority
        base_weights = {
            "unmet_window": self.config.window_shortfall_priority,
            "unmet_demand": self.config.shortfall_priority,
            "unmet_skill": self.config.skill_shortfall_priority,
            "unmet_shift": self.config.shift_shortfall_priority,
            "overtime": 1,
            "preferences": self.config.preferences_weight,
            "fairness": self.config.fairness_weight,
        }
        if objective_weights:
            base_weights.update(objective_weights)
        self.objective_weights = base_weights
        self.config.window_shortfall_priority = base_weights.get("unmet_window", self.config.window_shortfall_priority)
        self.config.shortfall_priority = base_weights.get("unmet_demand", self.config.shortfall_priority)
        self.config.skill_shortfall_priority = base_weights.get("unmet_skill", self.config.skill_shortfall_priority)
        self.config.shift_shortfall_priority = base_weights.get("unmet_shift", self.config.shift_shortfall_priority)
        self.config.preferences_weight = base_weights.get("preferences", self.config.preferences_weight)
        self.config.fairness_weight = base_weights.get("fairness", self.config.fairness_weight)
        self.config.skills_slack_enabled = bool(self.config.skills_slack_enabled)
        self.config.objective_priority = tuple(priority)

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
        self.window_shortfall_vars: dict[str, cp_model.IntVar] = {}
        self.shift_soft_shortfall_vars: dict[str, cp_model.IntVar] = {}
        self.skill_shortfall_vars: dict[Tuple[str, str], cp_model.IntVar] = {}
        self.overtime_vars: dict[str, cp_model.IntVar] = {}
        self.overtime_cost_weights: dict[str, int] = {}
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

        # Parametri per slot adattivi (STEP 3B)
        self.adaptive_slot_data = adaptive_slot_data
        self.slots_in_window = {str(k): list(v) for k, v in (slots_in_window or {}).items()}
        self.coverage_mode = str(coverage_mode).lower()
        self.enable_slot_slack = bool(enable_slot_slack)
        
        # Variabili per vincoli di slot
        self.slot_shortfall_vars: dict[tuple[str, str], cp_model.IntVar] = {}  # short_slot[w,t]
        self.slot_to_covering_shifts: dict[str, list[str]] = {}  # Mappa precomputata slot -> turni che lo coprono
        
        # NUOVO: Parametro per preservare integrità turni
        self.preserve_shift_integrity = bool(preserve_shift_integrity)
        
        # NUOVO: Modalità interpretazione domanda (solo con preserve_shift_integrity=True)
        self.demand_mode = "headcount"  # Default, sarà aggiornato dal config
        
        # Variabili per vincoli di segmenti con turni interi (quando preserve_shift_integrity=True)
        self.segment_shortfall_vars: dict[str, cp_model.IntVar] = {}  # short_segment[seg_id]
        self.shift_to_covering_segments: dict[str, list[str]] = {}  # Mappa turno -> segmenti che copre
        self.segment_demands: dict[str, int] = {}  # Domanda per segmento (headcount o persona-minuti)
        
        # STEP 4A: Pesi obiettivo in persona-minuti (conversione da persona-ora)
        self.objective_weights_minutes: dict[str, float] = {}
        self.slot_minutes: dict[str, int] = {}  # Durata slot in minuti per termini finestra
        self.mean_shift_minutes: int = 60  # Media durate turni per preferenze

    def build(self) -> None:
        """Costruisce variabili e vincoli base (placeholder)."""
        self._build_assignment_variables()
        self._build_shift_aggregate_variables()
        self._add_shift_coverage_constraints()
        self._add_shift_soft_demand_constraints()
        self._add_skill_coverage_constraints()
        
        # MODALITÀ OPERATIVE DISTINTE basate su preserve_shift_integrity
        if self.preserve_shift_integrity:
            # MODALITÀ TURNI INTERI: usa solo vincoli basati su segmenti
            # - NON costruisce vincoli su slot adattivi
            # - Ottimizza solo sui turni interi per coprire la domanda di ogni segmento
            self._add_segment_coverage_constraints()
        else:
            # MODALITÀ SLOT ADATTIVI: comportamento legacy
            # - Costruisce vincoli basati su slot e finestre
            # - Permette frammentazione dei turni tramite slot
            self._add_window_coverage_constraints()
            self._add_adaptive_slot_coverage_constraints()
        self.duration_minutes = self._compute_shift_duration_minutes()
        if self.duration_minutes:
            avg_minutes = sum(self.duration_minutes.values()) / len(self.duration_minutes)
            self.avg_shift_minutes = max(1, int(round(avg_minutes)))
        else:
            self.avg_shift_minutes = 60
        self.total_required_minutes = self._compute_total_required_minutes()
        
        # STEP 4A: Inizializza pesi in persona-minuti e durate per obiettivo unificato
        self._initialize_objective_weights_minutes()
        self._compute_slot_minutes()
        self.mean_shift_minutes = self.avg_shift_minutes  # Per preferenze
        self._add_employee_max_hours_constraints()
        self._add_one_shift_per_day_constraints()
        self._add_night_shift_constraints()
        self._add_rest_conflict_constraints()
        self._add_min_rest_constraints()
        self._set_objective()
        # TODO: aggiungere vincoli (riposi, ...)

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
        for _, shift_row in self.shifts.iterrows():
            shift_id = shift_row["shift_id"]
            required_staff = int(shift_row["required_staff"])

            vars_for_shift = self._vars_by_shift.get(shift_id, [])

            if len(vars_for_shift) < required_staff:
                print(f"[WARN] Turno {shift_id}: capacita disponibili {len(vars_for_shift)} < required {required_staff}")

            if not vars_for_shift:
                print(f"[WARN] Turno {shift_id}: nessun dipendente assegnabile, verranno contabilizzati minuti di shortfall")

            shortfall_var = self.model.NewIntVar(0, required_staff, f"shortfall__{shift_id}")
            self.shortfall_vars[shift_id] = shortfall_var

            assign_expr = sum(vars_for_shift) if vars_for_shift else 0
            self.model.Add(assign_expr + shortfall_var == required_staff)

    def _add_window_coverage_constraints(self) -> None:
        """Imposta la copertura per le finestre aggregate di domanda."""
        self.window_shortfall_vars = {}
        if not self.window_demands:
            return

        for window_id, demand in self.window_demands.items():
            if demand <= 0:
                continue
            shift_ids = self.window_shifts.get(window_id, [])
            window_vars: list[cp_model.IntVar] = []
            for shift_id in shift_ids:
                window_vars.extend(self._vars_by_shift.get(shift_id, []))
            slack = self.model.NewIntVar(0, demand, f"short_window__{window_id}")
            self.window_shortfall_vars[window_id] = slack
            if window_vars:
                self.model.Add(sum(window_vars) + slack >= demand)
            else:
                self.model.Add(slack >= demand)

    def _add_shift_soft_demand_constraints(self) -> None:
        """Applica i minimi di domanda per singolo turno (soft)."""
        self.shift_soft_shortfall_vars = {}
        if not self.shift_soft_demands:
            return

        for shift_id, demand in self.shift_soft_demands.items():
            if demand <= 0:
                continue
            # Usa la variabile aggregata y[s] invece di sum(x[e,s])
            y_var = self.shift_aggregate_vars.get(shift_id)
            slack = self.model.NewIntVar(0, demand, f"short_shift_soft__{shift_id}")
            self.shift_soft_shortfall_vars[shift_id] = slack
            if y_var is not None:
                self.model.Add(y_var + slack >= demand)
            else:
                self.model.Add(slack >= demand)


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

    def _add_adaptive_slot_coverage_constraints(self) -> None:
        """Implementa vincoli di copertura istantanea per slot adattivi (STEP 3B)."""
        self.slot_shortfall_vars = {}
        
        # Solo se coverage_mode == "adaptive_slots" e ci sono finestre e slot
        if self.coverage_mode != "adaptive_slots":
            return
            
        if not self.slots_in_window or not self.adaptive_slot_data:
            return
            
        # Precomputa mappa slot -> turni che lo coprono per ottimizzazione
        self._precompute_slot_to_shifts_mapping()
        
        slot_count = 0
        constraint_count = 0
        
        # Per ogni finestra w e per ogni slot t ∈ slots_in_window[w]
        for window_id, slot_ids in self.slots_in_window.items():
            if window_id not in self.window_demands:
                continue
                
            window_demand = self.window_demands[window_id]
            if window_demand <= 0:
                continue
                
            for slot_id in slot_ids:
                slot_count += 1
                
                # Trova turni che coprono questo slot
                covering_shifts = self.slot_to_covering_shifts.get(slot_id, [])
                
                # Somma delle variabili aggregate y[s] per turni che coprono lo slot
                covering_y_vars = []
                for shift_id in covering_shifts:
                    y_var = self.shift_aggregate_vars.get(shift_id)
                    if y_var is not None:
                        covering_y_vars.append(y_var)
                
                # Crea variabile di slack se abilitata
                if self.enable_slot_slack:
                    slack_var = self.model.NewIntVar(0, window_demand, f"short_slot__{window_id}__{slot_id}")
                    self.slot_shortfall_vars[(window_id, slot_id)] = slack_var
                    
                    # Vincolo: ∑_{turni s che coprono t} y[s] + short_slot[w,t] >= window_demand[w]
                    if covering_y_vars:
                        self.model.Add(sum(covering_y_vars) + slack_var >= window_demand)
                    else:
                        self.model.Add(slack_var >= window_demand)
                else:
                    # Vincolo hard: ∑_{turni s che coprono t} y[s] >= window_demand[w]
                    if covering_y_vars:
                        self.model.Add(sum(covering_y_vars) >= window_demand)
                    # Se non ci sono turni che coprono lo slot, il vincolo è impossibile
                    # ma questo dovrebbe essere già stato rilevato nel precompute
                
                constraint_count += 1
        
        if slot_count > 0:
            logger.info(
                "Vincoli slot adattivi: %d finestre, %d slot, %d vincoli (slack: %s)",
                len(self.slots_in_window),
                slot_count,
                constraint_count,
                "abilitato" if self.enable_slot_slack else "disabilitato"
            )

    def _add_segment_coverage_constraints(self) -> None:
        """
        NUOVO: Implementa vincoli di copertura per segmenti con turni interi.
        
        Quando preserve_shift_integrity=True, questo metodo:
        1. Mantiene la segmentazione temporale per calcolare la domanda
        2. Ma usa solo variabili aggregate y[s] dei turni interi
        3. Ogni turno copre TUTTI i segmenti nel suo intervallo temporale
        
        Formulazione matematica:
        - Per ogni segmento s: ∑_{turni i che coprono s} a_{i,s} * y[i] >= d_s
        - Dove a_{i,s} = capacità fornita dal turno i nel segmento s (persona-minuti)
        - E d_s = domanda richiesta nel segmento s (persona-minuti)
        """
        self.segment_shortfall_vars = {}
        
        # Solo se abbiamo dati di segmentazione
        if not self.adaptive_slot_data:
            logger.info("Vincoli segmenti con turni interi: nessun dato di segmentazione disponibile")
            return
            
        # Precomputa mappature turno -> segmenti e domande per segmento
        self._precompute_shift_to_segments_mapping()
        self._compute_segment_demands()
        
        if not self.segment_demands:
            logger.info("Vincoli segmenti con turni interi: nessuna domanda di segmento calcolata")
            return
        
        segment_count = 0
        constraint_count = 0
        
        # Per ogni segmento con domanda > 0
        for segment_id, demand_person_minutes in self.segment_demands.items():
            if demand_person_minutes <= 0:
                continue
                
            segment_count += 1
            
            # Trova tutti i turni che coprono questo segmento
            covering_shifts = []
            for shift_id, segments in self.shift_to_covering_segments.items():
                if segment_id in segments:
                    covering_shifts.append(shift_id)
            
            # Calcola capacità fornita da ogni turno nel segmento
            covering_terms = []
            for shift_id in covering_shifts:
                y_var = self.shift_aggregate_vars.get(shift_id)
                if y_var is not None:
                    # Capacità = durata_segmento * y[shift] (persona-minuti)
                    segment_duration = self._get_segment_duration_minutes(segment_id)
                    if segment_duration > 0:
                        # Termine: segment_duration * y[shift_id]
                        covering_terms.append(segment_duration * y_var)
            
            # Crea variabile di slack per segmento
            slack_var = self.model.NewIntVar(0, demand_person_minutes, f"short_segment__{segment_id}")
            self.segment_shortfall_vars[segment_id] = slack_var
            
            # Vincolo: ∑_{turni che coprono segmento} capacità + slack >= domanda
            if covering_terms:
                self.model.Add(sum(covering_terms) + slack_var >= demand_person_minutes)
            else:
                # Nessun turno copre il segmento -> tutto shortfall
                self.model.Add(slack_var >= demand_person_minutes)
            
            constraint_count += 1
        
        if segment_count > 0:
            logger.info(
                "Vincoli segmenti con turni interi: %d segmenti, %d vincoli (preserve_shift_integrity=True)",
                segment_count,
                constraint_count
            )
        else:
            logger.info("Vincoli segmenti con turni interi: nessun segmento con domanda trovato")

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
        NUOVO: Calcola la domanda per ogni segmento basata su demand_mode.
        
        Due modalità supportate (solo con preserve_shift_integrity=True):
        - "headcount": domanda costante per ogni segmento (numero persone simultanee)
        - "person_minutes": domanda proporzionale alla durata (volume di lavoro)
        """
        self.segment_demands = {}
        
        if not self.adaptive_slot_data or not self.window_demands:
            return
            
        # Solo applicabile quando preserve_shift_integrity=True
        if not self.preserve_shift_integrity:
            logger.info("demand_mode ignorato: preserve_shift_integrity=False")
            return
            
        try:
            # Accede ai dati di segmentazione
            segment_bounds = getattr(self.adaptive_slot_data, 'segment_bounds', {})
            
            logger.info("Calcolo domande segmenti con demand_mode='%s'", self.demand_mode)
            
            # Per ogni segmento, calcola la domanda basata sulla modalità
            for segment_id, (seg_start_min, seg_end_min) in segment_bounds.items():
                segment_duration = max(1, int(seg_end_min - seg_start_min))
                total_demand = 0
                
                # Trova finestre che intersecano questo segmento
                for window_id, window_demand in self.window_demands.items():
                    if window_demand <= 0:
                        continue
                    
                    # TODO: Implementare calcolo intersezione esatta
                    # Per ora assumiamo che il segmento sia contenuto nella finestra
                    intersects = True  # Semplificazione
                    
                    if not intersects:
                        continue
                    
                    if self.demand_mode == "headcount":
                        # MODALITÀ HEADCOUNT: domanda costante per segmento
                        # La domanda rappresenta il numero minimo di persone simultanee
                        # Ogni segmento interamente contenuto nella finestra richiede window_demand persone
                        contribution = window_demand
                        
                    elif self.demand_mode == "person_minutes":
                        # MODALITÀ PERSON_MINUTES: domanda proporzionale alla durata
                        # La domanda rappresenta il volume totale di lavoro in persona-minuti
                        window_duration = self.window_duration_minutes.get(window_id, 60)
                        
                        # Contributo proporzionale alla durata dell'intersezione
                        # contribution = window_demand * (segment_duration / window_duration)
                        contribution = window_demand * segment_duration / max(1, window_duration)
                        
                    else:
                        logger.warning("demand_mode non riconosciuto: %s, uso headcount", self.demand_mode)
                        contribution = window_demand
                    
                    total_demand += contribution
                
                if total_demand > 0:
                    if self.demand_mode == "headcount":
                        # Per headcount, prendiamo il massimo tra le finestre che intersecano
                        # (un segmento può essere coperto da più finestre, ma serve il max simultaneo)
                        max_demand = max(
                            window_demand for window_id, window_demand in self.window_demands.items()
                            if window_demand > 0  # TODO: controllare intersezione reale
                        ) if self.window_demands else 0
                        self.segment_demands[segment_id] = max(1, int(max_demand))
                    else:
                        # Per person_minutes, sommiamo i contributi
                        self.segment_demands[segment_id] = max(1, int(round(total_demand)))
            
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
                    
        except AttributeError as e:
            logger.warning("Errore nel calcolo domande segmenti: %s", e)

    def _get_segment_duration_minutes(self, segment_id: str) -> int:
        """Ottiene la durata di un segmento in minuti."""
        if not self.adaptive_slot_data:
            return self.avg_shift_minutes  # Fallback
            
        try:
            segment_bounds = getattr(self.adaptive_slot_data, 'segment_bounds', {})
            if segment_id in segment_bounds:
                start_min, end_min = segment_bounds[segment_id]
                return max(1, int(end_min - start_min))
        except AttributeError:
            pass
            
        return self.avg_shift_minutes  # Fallback

    def _precompute_slot_to_shifts_mapping(self) -> None:
        """Precomputa mappa slot -> turni che coprono lo slot per ottimizzazione."""
        self.slot_to_covering_shifts = {}
        
        if not self.adaptive_slot_data:
            return
            
        # Accede ai dati degli slot adattivi
        try:
            segments_of_s = getattr(self.adaptive_slot_data, 'segments_of_s', {})
            cover_segment = getattr(self.adaptive_slot_data, 'cover_segment', {})
            
            # Per ogni slot, trova tutti i turni che lo coprono
            for window_id, slot_ids in self.slots_in_window.items():
                for slot_id in slot_ids:
                    covering_shifts = []
                    
                    # Controlla ogni turno per vedere se copre questo slot
                    for shift_id in self.shift_aggregate_vars.keys():
                        shift_segments = segments_of_s.get(str(shift_id), [])
                        
                        # Un turno copre uno slot se almeno uno dei suoi segmenti copre interamente lo slot
                        for segment_id in shift_segments:
                            if cover_segment.get((segment_id, slot_id), 0) == 1:
                                covering_shifts.append(shift_id)
                                break  # Basta un segmento che copre lo slot
                    
                    self.slot_to_covering_shifts[slot_id] = covering_shifts
                    
        except AttributeError as e:
            logger.warning("Errore nell'accesso ai dati slot adattivi: %s", e)
            # Fallback: nessun turno copre alcuno slot
            for window_id, slot_ids in self.slots_in_window.items():
                for slot_id in slot_ids:
                    self.slot_to_covering_shifts[slot_id] = []

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
        total = 0
        for _, shift_row in self.shifts.iterrows():
            shift_id = shift_row["shift_id"]
            required_staff = int(shift_row["required_staff"])
            duration_min = self.duration_minutes.get(shift_id)
            if duration_min is None:
                raise ValueError(f"Durata mancante per il turno {shift_id}")
            total += duration_min * required_staff
        return total

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
        """Gestisce ore contrattuali e straordinari per dipendente."""
        if not self.duration_minutes:
            raise ValueError("Le durate dei turni devono essere calcolate prima di applicare il vincolo sulle ore massime.")

        self.overtime_vars = {}
        self.overtime_cost_weights = {}
        total_possible_ot = 0

        for _, emp_row in self.employees.iterrows():
            emp_id = emp_row["employee_id"]
            max_minutes = max(0, int(round(float(emp_row["max_week_hours"]) * 60)))
            max_ot_minutes = max(0, int(round(float(emp_row.get("max_overtime_hours", 0)) * 60)))

            pairs = self._vars_by_emp.get(emp_id, [])
            terms = [self.duration_minutes[shift_id] * var for shift_id, var in pairs]
            assigned_expr = sum(terms) if terms else 0

            overtime_var = self.model.NewIntVar(0, max_ot_minutes, f"overtime_min__{emp_id}")
            self.overtime_vars[emp_id] = overtime_var

            self.model.Add(assigned_expr <= max_minutes + overtime_var)

            total_possible_ot += max_ot_minutes
            self.overtime_cost_weights[emp_id] = self._resolve_overtime_cost_weight(emp_row)

        if self.config.global_overtime_cap_minutes is not None and self.overtime_vars:
            cap_minutes = min(self.config.global_overtime_cap_minutes, total_possible_ot) if total_possible_ot > 0 else 0
            self.global_overtime_cap_minutes = cap_minutes
            self.model.Add(sum(self.overtime_vars.values()) <= cap_minutes)
        else:
            self.global_overtime_cap_minutes = None

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

    def _compute_shift_soft_shortfall_expr(self):
        if not self.shift_soft_shortfall_vars:
            return 0, False

        terms = []
        for shift_id, var in self.shift_soft_shortfall_vars.items():
            duration = self.duration_minutes.get(shift_id)
            if duration is None:
                continue
            terms.append(duration * var)

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
        if not self.assignment_vars:
            return 0, False

        if not self.duration_minutes:
            raise ValueError("Le durate dei turni devono essere disponibili per impostare la funzione obiettivo.")

        active_emp_ids = [emp_id for emp_id in self.employees["employee_id"] if emp_id in self._vars_by_emp]
        if not active_emp_ids:
            return 0, False

        total_required_minutes = self.total_required_minutes
        if total_required_minutes <= 0:
            return 0, False

        shortfall_terms = []
        if self.shortfall_vars:
            for shift_id, var in self.shortfall_vars.items():
                duration = self.duration_minutes.get(shift_id)
                if duration is not None:
                    shortfall_terms.append(duration * var)
        shortfall_expr = sum(shortfall_terms) if shortfall_terms else 0

        num_active = len(active_emp_ids)
        deviation_bound = total_required_minutes

        self.workload_dev_vars = []
        for emp_id in active_emp_ids:
            terms = [self.duration_minutes[shift_id] * var for shift_id, var in self._vars_by_emp.get(emp_id, [])]
            assigned_expr = sum(terms) if terms else 0
            over = self.model.NewIntVar(0, deviation_bound, f"workload_over__{emp_id}")
            under = self.model.NewIntVar(0, deviation_bound, f"workload_under__{emp_id}")
            lhs = num_active * assigned_expr - total_required_minutes
            if shortfall_terms:
                lhs = lhs + shortfall_expr
            rhs = num_active * over - num_active * under
            self.model.Add(lhs == rhs)
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
                # MODALITÀ HEADCOUNT: shortfall in persone, moltiplicare per durata segmento
                # per ottenere persona-minuti per coerenza con funzione obiettivo
                segment_duration = self._get_segment_duration_minutes(segment_id)
                terms.append(segment_duration * var)
            else:
                # MODALITÀ PERSON_MINUTES: shortfall già in persona-minuti
                terms.append(var)

        if not terms:
            return 0, False
        return sum(terms), True

    def _set_objective(self) -> None:
        window_expr, has_window = self._compute_window_shortfall_expr()
        shortfall_expr, has_shortfall = self._compute_shortfall_cost_expr()
        skill_expr, has_skill = self._compute_skill_shortfall_expr()
        shift_soft_expr, has_shift_soft = self._compute_shift_soft_shortfall_expr()
        overtime_expr, has_overtime = self._compute_overtime_cost_expr()
        pref_expr, has_pref = self._compute_preference_cost_expr()
        fairness_expr, has_fairness = self._compute_fair_workload_expr()
        
        # NUOVO: Termini per segmenti con turni interi (se preserve_shift_integrity=True)
        segment_expr, has_segment = self._compute_segment_shortfall_expr()

        priority_map = {
            "unmet_window": (window_expr, has_window),
            "unmet_demand": (shortfall_expr, has_shortfall),
            "unmet_skill": (skill_expr, has_skill),
            "unmet_shift": (shift_soft_expr, has_shift_soft),
            "overtime": (overtime_expr, has_overtime),
            "preferences": (pref_expr, has_pref),
            "fairness": (fairness_expr, has_fairness),
        }
        
        # Se preserve_shift_integrity=True, usa i segmenti invece delle finestre per unmet_window
        if self.preserve_shift_integrity and has_segment:
            priority_map["unmet_window"] = (segment_expr, has_segment)

        terms = []
        for key in self.objective_priority:
            expr, available = priority_map.get(key, (0, False))
            weight = self.objective_weights.get(key, 0)
            if available and weight:
                terms.append(weight * expr)

        if not terms:
            self.model.Minimize(0)
            return

        self.model.Minimize(sum(terms))

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
        if not self.shortfall_vars:
            return pd.DataFrame(columns=["shift_id", "shortfall_units", "shortfall_staff_minutes"])

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
            return pd.DataFrame(columns=["shift_id", "shortfall_units", "shortfall_staff_minutes"])

        return pd.DataFrame(rows, columns=["shift_id", "shortfall_units", "shortfall_staff_minutes"])

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
        """STEP 4A: Inizializza pesi obiettivo in persona-minuti da config (persona-ora)."""
        self.objective_weights_minutes = {}
        
        # Legge i pesi dalla configurazione (interpretati come persona-ora) e converte
        penalties_config = {
            "unmet_window": BASE_WINDOW_WEIGHT_H,
            "unmet_demand": BASE_SHIFT_WEIGHT_H,
            "unmet_skill": BASE_SKILL_WEIGHT_H,
            "unmet_shift": BASE_SHIFT_SOFT_WEIGHT_H,
            "overtime": BASE_OVERTIME_WEIGHT_H,
            "fairness": BASE_FAIRNESS_WEIGHT_H,
            "preferences": BASE_PREFERENCES_WEIGHT_H,
        }
        
        # Converte da persona-ora a persona-minuto
        for key, weight_per_hour in penalties_config.items():
            weight_per_minute = _weight_per_hour_to_minutes(weight_per_hour)
            if weight_per_minute > 0:
                self.objective_weights_minutes[key] = weight_per_minute
        
        logger.info(
            "Pesi obiettivo (persona-minuto): %s",
            {k: f"{v:.4f}" for k, v in self.objective_weights_minutes.items()}
        )

    def _compute_slot_minutes(self) -> None:
        """STEP 4A: Calcola durate slot in minuti per termini finestra."""
        self.slot_minutes = {}
        
        if not self.adaptive_slot_data:
            return
            
        try:
            slot_bounds = getattr(self.adaptive_slot_data, 'slot_bounds', {})
            
            for window_id, slot_ids in self.slots_in_window.items():
                for slot_id in slot_ids:
                    if slot_id in slot_bounds:
                        start_min, end_min = slot_bounds[slot_id]
                        duration = max(1, int(end_min - start_min))
                        self.slot_minutes[slot_id] = duration
                    else:
                        # Fallback: usa durata media turni
                        self.slot_minutes[slot_id] = self.avg_shift_minutes
                        
        except AttributeError as e:
            logger.warning("Errore nel calcolo durate slot: %s", e)
            # Fallback: tutti gli slot hanno durata media turni
            for window_id, slot_ids in self.slots_in_window.items():
                for slot_id in slot_ids:
                    self.slot_minutes[slot_id] = self.avg_shift_minutes
        
        if self.slot_minutes:
            total_slots = len(self.slot_minutes)
            avg_slot_duration = sum(self.slot_minutes.values()) / total_slots
            logger.info(
                "Durate slot: %d slot, media %.1f min (range: %d-%d min)",
                total_slots,
                avg_slot_duration,
                min(self.slot_minutes.values()) if self.slot_minutes else 0,
                max(self.slot_minutes.values()) if self.slot_minutes else 0
            )

    def extract_objective_breakdown(self, solver: cp_model.CpSolver) -> dict[str, dict[str, float]]:
        """STEP 4B: Calcola breakdown dettagliato dell'obiettivo per componente."""
        breakdown = {}
        
        # 1. Finestre (slot adattivi)
        window_minutes = 0
        window_cost = 0.0
        if self.coverage_mode == "adaptive_slots" and self.slot_shortfall_vars:
            weight_per_min = self.objective_weights_minutes.get("unmet_window", 0.0)
            for (window_id, slot_id), var in self.slot_shortfall_vars.items():
                shortfall_units = solver.Value(var)
                if shortfall_units > 0:
                    slot_duration = self.slot_minutes.get(slot_id, self.avg_shift_minutes)
                    minutes = shortfall_units * slot_duration
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
        
        # 4. Turni soft (demand minima)
        shift_soft_minutes = 0
        shift_soft_cost = 0.0
        weight_per_min = self.objective_weights_minutes.get("unmet_shift", 0.0)
        for shift_id, var in self.shift_soft_shortfall_vars.items():
            shortfall_units = solver.Value(var)
            if shortfall_units > 0:
                shift_duration = self.duration_minutes.get(shift_id, self.avg_shift_minutes)
                minutes = shortfall_units * shift_duration
                shift_soft_minutes += minutes
                shift_soft_cost += minutes * weight_per_min
        
        breakdown["unmet_shift"] = {
            "minutes": shift_soft_minutes,
            "cost": shift_soft_cost,
            "weight_per_min": weight_per_min
        }
        
        # 5. Straordinari
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
        
        # 6. Preferenze (violazioni pesate)
        pref_violations = 0
        pref_cost = 0.0
        weight_per_min = self.objective_weights_minutes.get("preferences", 0.0)
        for (emp_id, shift_id), var in self.assignment_vars.items():
            if solver.Value(var):
                score = self.preference_score_by_pair.get((emp_id, shift_id), 0)
                if score < 0:  # Solo violazioni (preferenze negative)
                    pref_violations += abs(score)
                    # Costo = |score| * mean_shift_minutes * weight_per_min
                    pref_cost += abs(score) * self.mean_shift_minutes * weight_per_min
        
        breakdown["preferences"] = {
            "violations": pref_violations,
            "cost": pref_cost,
            "weight_per_min": weight_per_min,
            "mean_shift_minutes": self.mean_shift_minutes
        }
        
        # 7. Fairness (deviazioni workload)
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
        """STEP 4B: Log compatto del breakdown obiettivo post-solve."""
        breakdown = self.extract_objective_breakdown(solver)
        
        print("\n=== Breakdown Obiettivo (persona-minuti) ===")
        total_cost = 0.0
        
        for component, data in breakdown.items():
            cost = data.get("cost", 0.0)
            total_cost += cost
            
            if component == "preferences":
                violations = data.get("violations", 0)
                mean_min = data.get("mean_shift_minutes", 0)
                print(f"- {component:12}: {violations:3d} violazioni × {mean_min:3d}min = {cost:8.4f}")
            elif component == "fairness":
                dev_min = data.get("deviations_minutes", 0)
                print(f"- {component:12}: {dev_min:6.0f} dev-min = {cost:8.4f}")
            else:
                minutes = data.get("minutes", 0)
                print(f"- {component:12}: {minutes:6.0f} min = {cost:8.4f}")
        
        print(f"- {'TOTALE':12}: {total_cost:8.4f}")
        
        # Top-5 componenti più costosi
        sorted_components = sorted(
            [(k, v.get("cost", 0.0)) for k, v in breakdown.items()],
            key=lambda x: x[1],
            reverse=True
        )
        top_5 = [f"{comp}({cost:.3f})" for comp, cost in sorted_components[:5] if cost > 0]
        if top_5:
            print(f"Top-5 costi: {', '.join(top_5)}")

    def export_objective_breakdown_csv(self, solver: cp_model.CpSolver, output_path: Path) -> None:
        """STEP 4B: Export breakdown obiettivo in CSV."""
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
    dict[str, list[str]],  # window_shifts
    dict[str, int],  # window_duration_map
    dict[str, int],  # shift_soft_demand
]:
    employees = loader.load_employees(data_dir / "employees.csv")
    shifts = loader.load_shifts(data_dir / "shifts.csv")
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
    windows_df = loader.load_windows(data_dir / "windows.csv", shifts_norm)
    window_demand_map: dict[str, int] = {}
    window_duration_map: dict[str, int] = {}
    
    if not windows_df.empty:
        window_demand_map = {
            str(row["window_id"]): int(row["window_demand"]) 
            for _, row in windows_df.iterrows()
        }
        window_duration_map = {
            str(row["window_id"]): int(row["window_minutes"]) 
            for _, row in windows_df.iterrows()
        }

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

    shift_soft_demand: dict[str, int] = {}
    window_shifts: dict[str, list[str]] = {}
    if "demand" in shifts_norm.columns:
        for _, row in shifts_norm.iterrows():
            shift_id = str(row["shift_id"])
            demand_val = int(row.get("demand", 0))
            if demand_val > 0:
                shift_soft_demand[shift_id] = demand_val
            demand_id = str(row.get("demand_id", "")).strip()
            if demand_id:
                if demand_id not in window_demand_map:
                    raise ValueError(f"Turno {shift_id}: demand_id '{demand_id}' non presente in demand_windows.csv")
                window_shifts.setdefault(demand_id, []).append(shift_id)
                expected_role = window_roles.get(demand_id)
                role = str(row.get("role", ""))
                if expected_role is not None and expected_role != role:
                    warnings.warn(
                        f"Turno {shift_id}: ruolo {role} non coincide con quello della finestra {demand_id} ({expected_role})",
                        RuntimeWarning,
                    )
    else:
        shift_soft_demand = {}

    for demand_id, demand_value in window_demand_map.items():
        shift_ids = window_shifts.get(demand_id, [])
        if not shift_ids:
            warnings.warn(
                f"Finestra {demand_id}: nessun turno associato alla domanda",
                RuntimeWarning,
            )
            continue
        capacity = int(shifts_norm[shifts_norm["shift_id"].isin(shift_ids)]["required_staff"].astype(int).sum())
        if demand_value > capacity:
            warnings.warn(
                f"Finestra {demand_id}: domanda {demand_value} supera la capacita teorica {capacity}",
                RuntimeWarning,
            )

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
        window_shifts,
        window_duration_map,
        shift_soft_demand,
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
        "unmet_shift": cfg.penalties.unmet_shift,
        "overtime": args.overtime_priority if args.overtime_priority is not None else cfg.penalties.overtime,
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
        shift_shortfall_priority=objective_weights.get("unmet_shift", 0),
        preferences_weight=objective_weights.get("preferences", 0),
        fairness_weight=objective_weights.get("fairness", 0),
        default_overtime_cost_weight=default_ot_weight,
        global_overtime_cap_minutes=global_ot_cap_minutes,
        random_seed=cfg.random.seed,
        mip_gap=cfg.solver.mip_gap,
        skills_slack_enabled=cfg.skills.enable_slack,
        objective_priority=tuple(objective_priority),
    )

    summary = {
        "priority": objective_priority,
        "penalties": penalties,
        "rest_min_hours": global_rest_hours,
        "solver_time_limit": max_seconds,
        "solver_mip_gap": cfg.solver.mip_gap,
        "random_seed": cfg.random.seed,
        "skills_slack_enabled": cfg.skills.enable_slack,
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
        window_shifts,
        window_duration_map,
        shift_soft_demand,
    ) = _load_data(args.data_dir, solver_cfg.global_min_rest_hours)

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
        mapped_shifts = sum(len(window_shifts.get(wid, [])) for wid in window_demand_map)
        logger.info("Domande aggregate: %d finestre (%d turni associati)", window_count, mapped_shifts)
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
        window_demands=window_demand_map,
        window_shifts=window_shifts,
        window_duration_minutes=window_duration_map,
        shift_soft_demands=shift_soft_demand,
        config=solver_cfg,
        objective_priority=objective_priority,
        objective_weights=objective_weights,
        preserve_shift_integrity=cfg.shifts.preserve_shift_integrity,
    )
    
    # NUOVO: Imposta demand_mode dal config
    solver.demand_mode = cfg.shifts.demand_mode
    solver.build()
    cp_solver = solver.solve()

    print("Stato solver:", cp_solver.StatusName())

    # Generazione report diagnostici tramite ScheduleReporter
    from reporting import ScheduleReporter
    
    report_dir = Path(solver_cfg.report.output_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    
    reporter = ScheduleReporter(solver, cp_solver)

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

    # Generazione report CSV
    reporter.generate_coverage_report(report_dir / "coverage_report.csv")
    reporter.generate_constraint_report(report_dir / "constraint_report.csv")
    reporter.generate_objective_report(report_dir / "objective_report.csv")
    
    # Output a console dei principali indicatori
    print("\n=== Riepilogo Diagnostico ===")
    
    # Copertura
    coverage_stats = reporter.get_coverage_summary()
    print("\nStatistiche Copertura:")
    print(f"- Media copertura: {coverage_stats['avg_coverage']:.1%}")
    print(f"- Segmenti sottodimensionati: {coverage_stats['understaffed_segments']}")
    print(f"- Segmenti sovradimensionati: {coverage_stats['overstaffed_segments']}")

    # Vincoli
    constraint_stats = reporter.get_constraint_summary() 
    print("\nStato Vincoli:")
    print(f"- Vincoli violati: {constraint_stats['violated']}")
    print(f"- Vincoli attivi: {constraint_stats['binding']}")
    
    # Obiettivo
    obj_stats = reporter.get_objective_summary()
    print("\nBreakdown Obiettivo:")
    for term in obj_stats:
        print(f"- {term.name}: {term.value:.2f} (peso: {term.weight}, contributo: {term.contribution:.1%})")

    # Report dettagliati
    if solver_cfg.report.enabled:
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
        print("✓ Tutte le variabili aggregate sono corrette: y[s] = sum_e x[e,s]")
    else:
        print("✗ Errore nelle variabili aggregate!")

    return 0



if __name__ == "__main__":
    raise SystemExit(main())
