"""CP-SAT shift scheduling model (MVP).

Questo modulo definisce lo scheletro del solver basato su OR-Tools.
Il modello prende in ingresso i DataFrame risultanti da loader.precompute.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd
from ortools.sat.python import cp_model

try:
    from . import loader, precompute
except ImportError:  # fallback when running as a script
    import loader  # type: ignore
    import precompute  # type: ignore

DEFAULT_GLOBAL_MIN_REST_HOURS = 8.0
OVERTIME_COST_SCALE = 100
DEFAULT_OVERTIME_PRIORITY = 1000
DEFAULT_FAIRNESS_WEIGHT = 1
DEFAULT_OVERTIME_COST_WEIGHT = 1000
DEFAULT_SHORTFALL_PRIORITY = 2_000_000
DEFAULT_GLOBAL_OVERTIME_CAP_MINUTES = None

@dataclass
class SolverConfig:
    """Parametri base per il solver CP-SAT."""
    max_seconds: float = 30.0
    log_search_progress: bool = False
    global_min_rest_hours: float = DEFAULT_GLOBAL_MIN_REST_HOURS
    overtime_priority: int = DEFAULT_OVERTIME_PRIORITY
    shortfall_priority: int = DEFAULT_SHORTFALL_PRIORITY
    fairness_weight: int = DEFAULT_FAIRNESS_WEIGHT
    default_overtime_cost_weight: int = DEFAULT_OVERTIME_COST_WEIGHT
    global_overtime_cap_minutes: int | None = None

class ShiftSchedulingCpSolver:
    """Costruisce e risolve il modello CP-SAT per l'MVP."""
    def __init__(
        self,
        employees: pd.DataFrame,
        shifts: pd.DataFrame,
        assign_mask: pd.DataFrame,
        rest_conflicts: pd.DataFrame | None = None,
        overtime_costs: pd.DataFrame | None = None,
        config: SolverConfig | None = None,
    ) -> None:
        self.employees = employees
        self.shifts = shifts
        self.assign_mask = assign_mask
        self.rest_conflicts = rest_conflicts
        self.overtime_costs = overtime_costs
        self.config = config or SolverConfig()

        self.model = cp_model.CpModel()
        self.assignment_vars: Dict[Tuple[str, str], cp_model.IntVar] = {}
        self.duration_minutes: Dict[str, int] = {}
        self.total_required_minutes: int = 0
        self.workload_dev_vars: list[cp_model.IntVar] = []
        self._vars_by_shift: dict[str, list[cp_model.BoolVar]] = {}
        self._vars_by_emp: dict[str, list[tuple[str, cp_model.BoolVar]]] = {}
        self.shortfall_vars: dict[str, cp_model.IntVar] = {}
        self.overtime_vars: dict[str, cp_model.IntVar] = {}
        self.overtime_cost_weights: dict[str, int] = {}
        self.global_overtime_cap_minutes: int | None = config.global_overtime_cap_minutes
        self.role_overtime_costs: dict[str, float] = {}
        self._night_shift_ids: set[str] = set()
        if overtime_costs is not None:
            self.role_overtime_costs = dict(zip(overtime_costs["role"], overtime_costs["overtime_cost_per_hour"]))

    def build(self) -> None:
        """Costruisce variabili e vincoli base (placeholder)."""
        self._build_assignment_variables()
        self._add_shift_coverage_constraints()
        self.duration_minutes = self._compute_shift_duration_minutes()
        self.total_required_minutes = self._compute_total_required_minutes()
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
        self._vars_by_emp = {}
        for (emp_id, shift_id), var in self.assignment_vars.items():
            self._vars_by_shift.setdefault(shift_id, []).append(var)
            self._vars_by_emp.setdefault(emp_id, []).append((shift_id, var))

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
            return self.config.default_overtime_cost_weight

        cost_per_hour = min(candidates)
        cost_per_minute = int(round(cost_per_hour * OVERTIME_COST_SCALE / 60))
        return max(1, cost_per_minute)

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

    def _compute_overtime_cost_expr(self):
        if not self.overtime_vars:
            return 0, False

        terms = [
            self.overtime_cost_weights.get(emp_id, DEFAULT_OVERTIME_COST_WEIGHT) * var
            for emp_id, var in self.overtime_vars.items()
        ]
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

        num_active = len(active_emp_ids)
        deviation_bound = total_required_minutes

        self.workload_dev_vars = []
        for emp_id in active_emp_ids:
            terms = [self.duration_minutes[shift_id] * var for shift_id, var in self._vars_by_emp.get(emp_id, [])]
            assigned_expr = sum(terms) if terms else 0
            over = self.model.NewIntVar(0, deviation_bound, f"workload_over__{emp_id}")
            under = self.model.NewIntVar(0, deviation_bound, f"workload_under__{emp_id}")
            lhs = num_active * assigned_expr - total_required_minutes
            rhs = num_active * over - num_active * under
            self.model.Add(lhs == rhs)
            self.workload_dev_vars.extend([over, under])

        if not self.workload_dev_vars:
            return 0, False
        return sum(self.workload_dev_vars), True

    def _set_objective(self) -> None:
        shortfall_expr, has_shortfall = self._compute_shortfall_cost_expr()
        overtime_expr, has_overtime = self._compute_overtime_cost_expr()
        fairness_expr, has_fairness = self._compute_fair_workload_expr()

        if not (has_shortfall or has_overtime or has_fairness):
            self.model.Minimize(0)
            return

        terms = []
        if has_shortfall:
            terms.append(self.config.shortfall_priority * shortfall_expr)
        if has_overtime:
            terms.append(self.config.overtime_priority * overtime_expr)
        if has_fairness:
            terms.append(self.config.fairness_weight * fairness_expr)

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
        """Esegue la risoluzione CP-SAT (placeholder)."""
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.config.max_seconds
        solver.parameters.log_search_progress = self.config.log_search_progress

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


def _load_data(
    data_dir: Path,
    global_min_rest_hours: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    employees = loader.load_employees(data_dir / "employees.csv")
    shifts = loader.load_shifts(data_dir / "shifts.csv")
    availability = loader.load_availability(data_dir / "availability.csv", employees, shifts)

    shifts_norm = precompute.normalize_shift_times(shifts)
    quali_mask = loader.build_quali_mask(employees, shifts)
    assign_mask = loader.merge_availability(quali_mask, availability)
    rest_conflicts = precompute.conflict_pairs_for_rest(shifts_norm, global_min_rest_hours)
    overtime_costs = loader.load_overtime_costs(data_dir / "overtime_costs.csv")

    return employees, shifts_norm, availability, assign_mask, rest_conflicts, overtime_costs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Solver CP-SAT per shift scheduling")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Cartella con i CSV di input")
    parser.add_argument("--max-seconds", type=float, default=30.0, help="Tempo massimo di risoluzione")
    parser.add_argument("--log-search", action="store_true", help="Mostra i log del solver CP-SAT")
    parser.add_argument("--output", type=Path, default=None, help="Percorso di salvataggio per le assegnazioni in CSV")
    parser.add_argument("--global-rest-hours", type=float, default=None, help="Soglia globale di riposo minimo (ore)")
    parser.add_argument("--overtime-priority", type=int, default=None, help="Peso per penalizzare lo straordinario")
    parser.add_argument("--fairness-weight", type=int, default=None, help="Peso per la fairness nel carico di lavoro")
    parser.add_argument("--default-ot-weight", type=int, default=None, help="Peso predefinito per costo straordinario se il ruolo manca")
    parser.add_argument("--global-ot-cap-hours", type=float, default=None, help="Tetto settimanale globale di straordinari (ore)")
    args = parser.parse_args(argv)

    global_ot_cap_minutes = None
    if args.global_ot_cap_hours is not None:
        global_ot_cap_minutes = max(0, int(round(args.global_ot_cap_hours * 60)))

    config = SolverConfig(
        max_seconds=args.max_seconds,
        log_search_progress=args.log_search,
        global_min_rest_hours=args.global_rest_hours if args.global_rest_hours is not None else DEFAULT_GLOBAL_MIN_REST_HOURS,
        overtime_priority=args.overtime_priority if args.overtime_priority is not None else DEFAULT_OVERTIME_PRIORITY,
        fairness_weight=args.fairness_weight if args.fairness_weight is not None else DEFAULT_FAIRNESS_WEIGHT,
        default_overtime_cost_weight=args.default_ot_weight if args.default_ot_weight is not None else DEFAULT_OVERTIME_COST_WEIGHT,
        global_overtime_cap_minutes=global_ot_cap_minutes,
    )

    (
        employees,
        shifts_norm,
        availability,
        assign_mask,
        rest_conflicts,
        overtime_costs,
    ) = _load_data(args.data_dir, config.global_min_rest_hours)

    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts_norm,
        assign_mask=assign_mask,
        rest_conflicts=rest_conflicts,
        overtime_costs=overtime_costs,
        config=config,
    )
    solver.build()
    cp_solver = solver.solve()

    print("Stato solver:", cp_solver.StatusName())

    assignments_df = solver.extract_assignments(cp_solver)
    if args.output is not None:
        output_path = args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        assignments_df.to_csv(output_path, index=False)
        print(f"Assegnazioni salvate in {output_path}")
    elif assignments_df.empty:
        print("Nessuna assegnazione attiva da mostrare.")
    else:
        print("Assegnazioni attive (prime 10 righe):")
        print(assignments_df.head(10).to_string(index=False))

    overtime_df = solver.extract_overtime_summary(cp_solver)
    if not overtime_df.empty:
        print("\nStraordinari (minuti per dipendente):")
        print(overtime_df.to_string(index=False, formatters={"overtime_hours": lambda v: f"{v:.2f}"}))

    shortfall_df = solver.extract_shortfall_summary(cp_solver)
    if not shortfall_df.empty:
        print("\nShortfall turni scoperti:")
        print(shortfall_df.to_string(index=False))

    solver.log_employee_summary(cp_solver)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
