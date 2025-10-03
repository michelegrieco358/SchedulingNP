"""Modulo per la generazione di report diagnostici delle soluzioni di scheduling.

Questo modulo fornisce strumenti per analizzare i risultati dell'ottimizzazione
e generare report dettagliati sulla copertura, i vincoli e l'obiettivo.
"""
from pathlib import Path
import logging
import datetime as dt
from typing import Dict, Any, Mapping, Sequence, Optional
from dataclasses import dataclass
import pandas as pd
from ortools.sat.python import cp_model

logger = logging.getLogger(__name__)


@dataclass
class SegmentCoverage:
    """Dati di copertura per un segmento temporale."""
    segment_id: str
    start_time: str
    end_time: str
    demand: int
    assigned: int
    shortfall: int
    overstaffing: int


@dataclass
class ConstraintStatus:
    """Stato di un vincolo nel modello."""
    name: str
    satisfied: bool
    binding: bool
    violation: float


@dataclass
class ObjectiveTerm:
    """Termine nella funzione obiettivo."""
    name: str
    weight: float
    value: float
    contribution: float


class ScheduleReporter:
    """Generatore di report diagnostici per soluzioni di scheduling."""

    def __init__(
        self,
        solver: Any,
        cp_solver: cp_model.CpSolver,
        *,
        assignments_df: Optional[pd.DataFrame] = None,
        windows_df: Optional[pd.DataFrame] = None,
    ):
        """Inizializza il reporter.

        Args:
            solver: Istanza di ShiftSchedulingCpSolver
            cp_solver: Solver CP-SAT con soluzione
            assignments_df: Assegnazioni già estratte dal solver (opzionale)
            windows_df: DataFrame con la domanda aggregata per finestra (opzionale)
        """
        self.solver = solver
        self.cp_solver = cp_solver
        self.output_dir = Path("reports")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.assignments_df = assignments_df
        self.windows_df = windows_df

    def update_data(
        self,
        *,
        assignments_df: Optional[pd.DataFrame] = None,
        windows_df: Optional[pd.DataFrame] = None,
    ) -> None:
        """Aggiorna i riferimenti a assignments e windows se disponibili."""

        if assignments_df is not None:
            self.assignments_df = assignments_df
        if windows_df is not None:
            self.windows_df = windows_df

    def generate_segment_coverage_report(self) -> pd.DataFrame:
        """Genera report di copertura per slot temporali aggregati."""
        columns = [
            "segment_id",
            "start_time",
            "end_time",
            "demand",
            "assigned",
            "shortfall",
            "overstaffing",
        ]

        data = getattr(self.solver, "adaptive_slot_data", None)
        if not data:
            df = pd.DataFrame(columns=columns)
            output_path = self.output_dir / "segment_coverage.csv"
            df.to_csv(output_path, index=False)
            logger.info("Report copertura segmenti salvato in %s", output_path)
            self._print_coverage_summary(df)
            return df

        slot_bounds = getattr(data, "slot_bounds", {}) or {}
        if not slot_bounds:
            df = pd.DataFrame(columns=columns)
            output_path = self.output_dir / "segment_coverage.csv"
            df.to_csv(output_path, index=False)
            logger.info("Report copertura segmenti salvato in %s", output_path)
            self._print_coverage_summary(df)
            return df

        slot_demands = getattr(self.solver, "slot_demands", {}) or {}
        demand_mode = getattr(self.solver, "demand_mode", "headcount")
        slot_windows = getattr(data, "slot_windows", {}) or getattr(self.solver, "slot_windows", {}) or {}
        window_demands = getattr(self.solver, "window_demands", {}) or {}

        slot_shortfalls: dict[str, float] = {}
        if getattr(self.solver, "slot_shortfall_vars", None):
            slot_shortfalls = {
                slot_id: float(self.cp_solver.Value(var))
                for slot_id, var in self.solver.slot_shortfall_vars.items()
            }

        slot_overstaff: dict[str, float] = {}
        if getattr(self.solver, "slot_overstaff_vars", None):
            slot_overstaff = {
                slot_id: float(self.cp_solver.Value(var))
                for slot_id, var in self.solver.slot_overstaff_vars.items()
            }

        def _format_time(minute: int) -> str:
            minute = int(minute)
            if minute < 0:
                minute = 0
            minute = minute % (24 * 60)
            return f"{minute // 60:02d}:{minute % 60:02d}"

        def _slot_sort_key(item: tuple[str, tuple[int, int] | Sequence[int] | Any]) -> tuple[int, int, str]:
            bounds = item[1]
            start = 0
            end = 0
            try:
                start = int(bounds[0])
            except (TypeError, ValueError, IndexError):
                start = 0
            try:
                end = int(bounds[1])
            except (TypeError, ValueError, IndexError):
                end = start
            return (start, end, str(item[0]))

        coverages: list[SegmentCoverage] = []

        for slot_id, bounds in sorted(slot_bounds.items(), key=_slot_sort_key):
            if not isinstance(bounds, (list, tuple)) or len(bounds) < 2:
                continue
            try:
                start_min = int(bounds[0])
                end_min = int(bounds[1])
            except (TypeError, ValueError):
                continue
            duration = max(0, end_min - start_min)
            if duration <= 0:
                continue

            start_time = _format_time(start_min)
            end_time = _format_time(end_min)

            demand_value = int(slot_demands.get(slot_id, 0))
            if demand_value <= 0 and slot_windows:
                demand_from_windows = 0.0
                for window_info in slot_windows.get(slot_id, []):
                    if isinstance(window_info, tuple):
                        window_id, overlap = window_info
                        overlap_minutes = max(0, int(overlap))
                    else:
                        window_id = window_info
                        overlap_minutes = duration
                    demand = window_demands.get(str(window_id), 0)
                    if demand <= 0 or overlap_minutes <= 0:
                        continue
                    if demand_mode == "headcount":
                        demand_from_windows += demand * overlap_minutes
                    else:
                        demand_from_windows += demand * overlap_minutes
                demand_value = int(round(demand_from_windows)) if demand_from_windows > 0 else 0

            shortfall_val = int(round(slot_shortfalls.get(slot_id, 0.0)))
            overstaff_val = int(round(slot_overstaff.get(slot_id, 0.0)))
            assigned_val = max(0, demand_value + overstaff_val - shortfall_val)

            coverages.append(
                SegmentCoverage(
                    segment_id=slot_id,
                    start_time=start_time,
                    end_time=end_time,
                    demand=demand_value,
                    assigned=assigned_val,
                    shortfall=shortfall_val,
                    overstaffing=overstaff_val,
                )
            )

        df = pd.DataFrame([vars(c) for c in coverages]) if coverages else pd.DataFrame(columns=columns)
        if not df.empty:
            df = df[columns]

        output_path = self.output_dir / "segment_coverage.csv"
        df.to_csv(output_path, index=False)
        logger.info("Report copertura segmenti salvato in %s", output_path)
        self._print_coverage_summary(df)
        return df


    def generate_constraint_report(self) -> pd.DataFrame:
        """Genera report sullo stato dei vincoli principali.
        
        Returns:
            DataFrame con colonne: constraint_name, satisfied, binding, violation
        """
        statuses = []
        
        # Verifica vincoli di assegnazione
        if self.solver.shortfall_vars:
            total_shortfall = sum(self.cp_solver.Value(var) for var in self.solver.shortfall_vars.values())
            statuses.append(ConstraintStatus(
                name="coverage_constraints",
                satisfied=total_shortfall == 0,
                binding=total_shortfall == 0,
                violation=total_shortfall
            ))
        
        # Verifica vincoli di riposo
        if hasattr(self.solver, "rest_violations"):
            total_rest_violations = sum(self.cp_solver.Value(var) for var in self.solver.rest_violations.values())
            statuses.append(ConstraintStatus(
                name="rest_constraints",
                satisfied=total_rest_violations == 0,
                binding=total_rest_violations == 0,
                violation=total_rest_violations
            ))
        
        # Verifica vincoli di skill
        if self.solver.skill_shortfall_vars:
            total_skill_shortfall = sum(self.cp_solver.Value(var) for var in self.solver.skill_shortfall_vars.values())
            statuses.append(ConstraintStatus(
                name="skill_constraints",
                satisfied=total_skill_shortfall == 0,
                binding=total_skill_shortfall == 0,
                violation=total_skill_shortfall
            ))
        
        # Converti in DataFrame
        df = pd.DataFrame([vars(s) for s in statuses])
        
        # Salva su file
        output_path = self.output_dir / "constraint_status.csv"
        df.to_csv(output_path, index=False)
        logger.info(f"Report stato vincoli salvato in {output_path}")
        
        return df

    def generate_objective_breakdown(self) -> pd.DataFrame:
        """Genera breakdown dettagliato della funzione obiettivo.
        
        Returns:
            DataFrame con colonne: term, weight, value, contribution
        """
        breakdown = []
        
        # Usa il metodo esistente per ottenere il breakdown
        objective_data = self.solver.extract_objective_breakdown(self.cp_solver)
        
        total_contribution = 0
        for term_name, term_data in objective_data.items():
            weight = term_data.get("weight_per_min", 0.0)
            value = term_data.get("minutes", 0)
            contribution = term_data.get("cost", 0.0)
            
            breakdown.append(ObjectiveTerm(
                name=term_name,
                weight=weight,
                value=value,
                contribution=contribution
            ))
            total_contribution += contribution
        
        # Aggiungi riga totale
        breakdown.append(ObjectiveTerm(
            name="TOTAL",
            weight=0.0,
            value=0.0,
            contribution=total_contribution
        ))
        
        # Converti in DataFrame
        df = pd.DataFrame([vars(t) for t in breakdown])
        
        # Salva su file
        output_path = self.output_dir / "objective_breakdown.csv"
        df.to_csv(output_path, index=False, float_format="%.6f")
        logger.info(f"Report breakdown obiettivo salvato in {output_path}")
        
        return df

    def _print_coverage_summary(self, coverage_df: pd.DataFrame) -> None:
        """Stampa un sommario compatto della copertura su console."""
        if coverage_df.empty:
            print("\nNessun dato di copertura disponibile")
            return

        print("\n=== Copertura Segmenti ===")

        total_segments = len(coverage_df)
        covered = len(coverage_df[coverage_df["shortfall"] == 0])
        partial = len(coverage_df[(coverage_df["shortfall"] > 0) & (coverage_df["assigned"] > 0)])
        uncovered = len(coverage_df[coverage_df["assigned"] == 0])

        header = f"{'Intervallo':<17} {'Stato':<6} {'Rich':>5} {'Ass':>5} {'Delta':>6} | {'Copertura':<20}"
        print(header)
        print('-' * len(header))

        bar_width = 20
        for _, row in coverage_df.iterrows():
            demand = int(row["demand"])
            assigned = int(row["assigned"])
            shortfall = int(row["shortfall"])
            overstaff = int(row["overstaffing"])

            if demand <= 0:
                coverage_pct = 100.0
            else:
                coverage_pct = max(0.0, min(100.0, (assigned / demand) * 100.0))

            filled = int(round((coverage_pct / 100.0) * bar_width))
            filled = min(bar_width, max(0, filled))
            bar = '#' * filled + '.' * (bar_width - filled)

            if coverage_pct >= 99.5:
                status = 'OK'
            elif coverage_pct <= 0.5:
                status = 'MISS'
            else:
                status = 'PART'

            delta = overstaff - shortfall
            print(
                f"{row['start_time']}-{row['end_time']:<7} "
                f"{status:<6} "
                f"{demand:5d} "
                f"{assigned:5d} "
                f"{delta:+6d} | "
                f"{bar}"
            )

        print('-' * len(header))
        print(
            f"Total segments: {total_segments:3d}  "
            f"Covered: {covered:3d} OK  "
            f"Partial: {partial:3d} PART  "
            f"Uncovered: {uncovered:3d} MISS"
        )

        if not coverage_df.empty:
            self._plot_coverage(coverage_df)

    def _plot_coverage(self, coverage_df: pd.DataFrame) -> None:
        """Genera una heatmap domanda/copertura utilizzando assignments e windows."""

        if self.windows_df is None or getattr(self.windows_df, "empty", True):
            logger.info("Nessuna windows_df disponibile: salto la generazione della heatmap di copertura")
            return

        try:
            import numpy as np
            import matplotlib.pyplot as plt
        except ImportError:
            logger.warning("matplotlib o numpy non installati - impossibile generare la heatmap")
            return
        except Exception as exc:  # pragma: no cover - import dinamico
            logger.error(f"Errore nell'import dei pacchetti di plotting: {exc}")
            return

        assignments_df = self.assignments_df
        if assignments_df is None:
            try:
                assignments_df = self.solver.extract_assignments(self.cp_solver)
                self.assignments_df = assignments_df
            except Exception as exc:  # pragma: no cover - fallback diagnostico
                logger.error(f"Impossibile estrarre le assegnazioni dal solver: {exc}")
                return

        if assignments_df is None or assignments_df.empty:
            logger.info("Nessuna assegnazione attiva: heatmap di copertura non generata")
            return

        windows_df = self.windows_df
        if windows_df is None or windows_df.empty:
            logger.info("DataFrame windows_df vuoto: heatmap di copertura non generata")
            return

        slot_minutes = 60

        day_series = windows_df["day"].astype(str)
        days = sorted(day_series.unique())
        if not days:
            logger.info("Nessun giorno presente in windows_df: heatmap di copertura non generata")
            return

        day_to_col = {day: idx for idx, day in enumerate(days)}
        n_days = len(days)
        n_slots = 24 * 60 // slot_minutes

        demand_matrix = np.zeros((n_slots, n_days), dtype=float)
        coverage_matrix = np.zeros((n_slots, n_days), dtype=float)

        def _coerce_minute_value(value: Any) -> Optional[int]:
            """Return an integer minute offset when possible."""

            if value is None:
                return None
            try:
                if pd.isna(value):
                    return None
            except TypeError:
                # Non-scalar values fall through to conversion attempt.
                pass

            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        def _parse_time_like(value: Any) -> Optional[int]:
            """Parse datetime/time or HH:MM strings into minutes from midnight."""

            if value is None:
                return None

            if isinstance(value, dt.datetime):
                return value.hour * 60 + value.minute

            if isinstance(value, dt.time):
                return value.hour * 60 + value.minute

            if isinstance(value, pd.Timestamp):
                if pd.isna(value):
                    return None
                return value.hour * 60 + value.minute

            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return None
                try:
                    hour_str, minute_str = text.split(":", 1)
                    return int(hour_str) * 60 + int(minute_str)
                except (ValueError, AttributeError):
                    return None

            return None

        for _, win in windows_df.iterrows():
            day_label = str(win.get("day", ""))
            col = day_to_col.get(day_label)
            if col is None:
                continue

            start_minute = _coerce_minute_value(win.get("window_start_min"))
            end_minute = _coerce_minute_value(win.get("window_end_min"))

            if start_minute is None or end_minute is None:
                start_minute = _parse_time_like(win.get("window_start"))
                end_minute = _parse_time_like(win.get("window_end"))

            if start_minute is None or end_minute is None:
                start_repr = win.get("window_start")
                end_repr = win.get("window_end")
                logger.debug(
                    "Window %s ha orari non validi (%s-%s)",
                    win.get("window_id"),
                    start_repr,
                    end_repr,
                )
                continue

            start_idx = max(0, min(n_slots, start_minute // slot_minutes))
            end_idx = max(start_idx + 1, min(n_slots, end_minute // slot_minutes))

            demand = win.get("window_demand", 0)
            try:
                demand_val = float(demand)
            except (TypeError, ValueError):
                demand_val = 0.0

            demand_matrix[start_idx:end_idx, col] += demand_val

        assignments_df_local = assignments_df.copy()
        if "day" not in assignments_df_local.columns:
            if "start_dt" in assignments_df_local.columns:
                assignments_df_local["day"] = (
                    pd.to_datetime(assignments_df_local["start_dt"], errors="coerce").dt.date.astype(str)
                )
            else:
                assignments_df_local["day"] = ""
        else:
            assignments_df_local["day"] = assignments_df_local["day"].astype(str)

        for _, row in assignments_df_local.iterrows():
            col = day_to_col.get(str(row.get("day", "")))
            if col is None:
                continue

            start_dt = pd.to_datetime(row.get("start_dt"), errors="coerce")
            end_dt = pd.to_datetime(row.get("end_dt"), errors="coerce")

            if pd.isna(start_dt) or pd.isna(end_dt):
                day_label = row.get("day")
                start_str = row.get("start") or row.get("start_time")
                end_str = row.get("end") or row.get("end_time")
                if day_label and start_str:
                    start_dt = pd.to_datetime(f"{day_label} {start_str}", errors="coerce")
                if day_label and end_str:
                    end_dt = pd.to_datetime(f"{day_label} {end_str}", errors="coerce")

            if pd.isna(start_dt) or pd.isna(end_dt):
                continue

            start_idx = max(0, min(n_slots, (start_dt.hour * 60 + start_dt.minute) // slot_minutes))
            end_idx_raw = (end_dt.hour * 60 + end_dt.minute) // slot_minutes
            end_idx = max(start_idx + 1, min(n_slots, end_idx_raw))

            coverage_matrix[start_idx:end_idx, col] += 1.0

        shortfall_matrix = np.maximum(demand_matrix - coverage_matrix, 0.0)

        fig = plt.figure(figsize=(n_days * 1.5 if n_days else 6, 8))
        ax = fig.add_subplot(111)
        heatmap = ax.imshow(shortfall_matrix, aspect="auto", cmap="Reds", origin="lower")

        for col_idx in range(1, n_days):
            ax.axvline(x=col_idx - 0.5, color="black", linestyle="-", linewidth=2.5, alpha=1.0)

        ax.set_xlabel("Giorno")
        ax.set_ylabel("Orario")
        ax.set_xticks(range(n_days))
        ax.set_xticklabels(days)

        hour_ticks = [i for i in range(n_slots) if (i * slot_minutes) % 60 == 0]
        hour_labels = [f"{hour:02d}:00" for hour in range(24)]
        ax.set_yticks(hour_ticks)
        ax.set_yticklabels(hour_labels)

        ax.set_title("Shortfall di copertura (rosso = scopertura)")
        fig.colorbar(heatmap, ax=ax, label="Shortfall (persone mancanti)")
        fig.tight_layout()

        plot_path = self.output_dir / "coverage_plot.png"
        fig.savefig(plot_path)
        plt.close(fig)

        logger.info(f"Heatmap copertura salvata in {plot_path}")

    def generate_all_reports(self) -> None:
        """Genera tutti i report disponibili."""
        try:
            self.generate_segment_coverage_report()
            self.generate_constraint_report()
            self.generate_objective_breakdown()
            
            logger.info(f"Report generati nella directory {self.output_dir}")
            
        except Exception as e:
            logger.error(f"Errore nella generazione dei report: {e}")
            raise
