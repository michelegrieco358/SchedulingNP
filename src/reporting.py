"""Modulo per la generazione di report diagnostici delle soluzioni di scheduling.

Questo modulo fornisce strumenti per analizzare i risultati dell'ottimizzazione
e generare report dettagliati sulla copertura, i vincoli e l'obiettivo.
"""
from pathlib import Path
import logging
import math
from functools import reduce
from typing import Dict, Any, Mapping, Sequence, Optional
from dataclasses import dataclass
import pandas as pd
from ortools.sat.python import cp_model

logger = logging.getLogger(__name__)


@dataclass
class SegmentCoverage:
    """Dati di copertura per un segmento temporale."""
    segment_id: str
    day: str
    role: str
    start_minute: int
    end_minute: int
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
        """Genera report di copertura per slot temporali aggregati.

        Returns:
            DataFrame con colonne: segment_start, segment_end, demand, assigned, shortfall, overstaffing
        """
        columns = [
            "segment_id",
            "day",
            "role",
            "start_minute",
            "end_minute",
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

        slot_windows = getattr(data, "slot_windows", {}) or getattr(self.solver, "slot_windows", {}) or {}
        cover_map = getattr(data, "cover_segment", {}) or {}

        window_demands = getattr(self.solver, "window_demands", {}) or {}
        segment_demands = getattr(self.solver, "segment_demands", {}) or {}

        segment_shortfalls: dict[str, float] = {}
        if hasattr(self.solver, "segment_shortfall_vars") and self.solver.segment_shortfall_vars:
            segment_shortfalls = {
                seg_id: float(self.cp_solver.Value(var))
                for seg_id, var in self.solver.segment_shortfall_vars.items()
            }

        segment_overstaffs: dict[str, float] = {}
        if hasattr(self.solver, "segment_overstaff_vars") and self.solver.segment_overstaff_vars:
            segment_overstaffs = {
                seg_id: float(self.cp_solver.Value(var))
                for seg_id, var in self.solver.segment_overstaff_vars.items()
            }

        slot_minutes: dict[str, int] = {}
        for slot_id, bounds in slot_bounds.items():
            try:
                start_min, end_min = int(bounds[0]), int(bounds[1])
            except (TypeError, ValueError, IndexError):
                continue
            duration = max(0, end_min - start_min)
            if duration <= 0:
                continue
            slot_minutes[slot_id] = duration

        if not slot_minutes:
            df = pd.DataFrame(columns=columns)
            output_path = self.output_dir / "segment_coverage.csv"
            df.to_csv(output_path, index=False)
            logger.info("Report copertura segmenti salvato in %s", output_path)
            self._print_coverage_summary(df)
            return df

        slots_by_segment: dict[str, list[str]] = {}
        segments_by_slot: dict[str, list[str]] = {}
        for (segment_id, slot_id), covers in cover_map.items():
            if not covers or slot_id not in slot_minutes:
                continue
            slots_by_segment.setdefault(segment_id, []).append(slot_id)
            segments_by_slot.setdefault(slot_id, []).append(segment_id)

        segment_total_minutes: dict[str, int] = {}
        for segment_id, slots in slots_by_segment.items():
            total = sum(slot_minutes.get(slot_id, 0) for slot_id in slots)
            if total > 0:
                segment_total_minutes[segment_id] = total

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
            if slot_id not in slot_minutes:
                continue
            start_min, end_min = bounds
            duration = slot_minutes[slot_id]
            start_time = _format_time(start_min)
            end_time = _format_time(end_min)

            day_label = ""
            role_label = ""
            parts = str(slot_id).split("__")
            if len(parts) >= 3:
                day_label = parts[0]
                role_label = parts[1]

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
                demand_from_windows += demand * overlap_minutes

            assigned_sum = 0.0
            shortfall_sum = 0.0
            overstaff_sum = 0.0
            demand_from_segments = 0.0

            covering_segments = segments_by_slot.get(slot_id, [])

            for segment_id in covering_segments:
                total_minutes = segment_total_minutes.get(segment_id, 0)
                if total_minutes <= 0:
                    continue
                seg_demand = float(segment_demands.get(segment_id, 0))
                seg_shortfall = float(segment_shortfalls.get(segment_id, 0))
                seg_overstaff = float(segment_overstaffs.get(segment_id, 0))
                if seg_demand <= 0 and seg_shortfall <= 0:
                    continue
                share = duration / total_minutes
                demand_contrib = seg_demand * share
                shortfall_contrib = seg_shortfall * share
                overstaff_contrib = seg_overstaff * share
                assigned_contrib = max(0.0, demand_contrib - shortfall_contrib + overstaff_contrib)

                demand_from_segments += demand_contrib
                shortfall_sum += shortfall_contrib
                overstaff_sum += overstaff_contrib
                assigned_sum += assigned_contrib

            if demand_from_segments > 0:
                demand_value = demand_from_segments
            else:
                demand_value = demand_from_windows

            demand_int = int(round(demand_value)) if demand_value > 0 else 0
            assigned_int = int(round(assigned_sum)) if assigned_sum > 0 else 0
            shortfall_int = int(round(shortfall_sum)) if shortfall_sum > 0 else 0
            overstaff_int = int(round(overstaff_sum)) if overstaff_sum > 0 else 0
            overstaffing = max(overstaff_int, assigned_int - demand_int, 0)

            coverages.append(
                SegmentCoverage(
                    segment_id=slot_id,
                    day=day_label,
                    role=role_label,
                    start_minute=int(start_min),
                    end_minute=int(end_min),
                    start_time=start_time,
                    end_time=end_time,
                    demand=demand_int,
                    assigned=assigned_int,
                    shortfall=shortfall_int,
                    overstaffing=overstaffing,
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
        slot_shortfall_vars = getattr(self.solver, "slot_shortfall_vars", None) or {}
        legacy_shortfall_vars = getattr(self.solver, "shortfall_vars", None) or {}

        shortfall_source = slot_shortfall_vars if slot_shortfall_vars else legacy_shortfall_vars

        if shortfall_source:
            total_shortfall = sum(self.cp_solver.Value(var) for var in shortfall_source.values())
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
        slot_skill_shortfall_vars = getattr(self.solver, "slot_skill_shortfall_vars", None) or {}
        legacy_skill_shortfall_vars = getattr(self.solver, "skill_shortfall_vars", None) or {}

        skill_shortfall_source = (
            slot_skill_shortfall_vars if slot_skill_shortfall_vars else legacy_skill_shortfall_vars
        )

        if skill_shortfall_source:
            total_skill_shortfall = sum(self.cp_solver.Value(var) for var in skill_shortfall_source.values())
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
        """Genera una heatmap domanda/copertura basata sui microslot del solver."""

        if coverage_df.empty:
            logger.info("Coverage_df vuoto: heatmap di copertura non generata")
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

        df = coverage_df.copy()

        if "day" not in df.columns:
            df["day"] = ""
        if "role" not in df.columns:
            df["role"] = ""

        df["day"] = df["day"].fillna("").astype(str)
        df["role"] = df["role"].fillna("").astype(str)

        unique_keys = sorted({(row.day, row.role) for row in df.itertuples()})
        if not unique_keys:
            logger.info("Nessuna combinazione (giorno, ruolo) trovata: heatmap non generata")
            return

        key_to_col = {key: idx for idx, key in enumerate(unique_keys)}
        labels = [f"{day}\n{role}" if role else day for day, role in unique_keys]

        if "start_minute" not in df.columns or "end_minute" not in df.columns:
            logger.info("Copertura segmenti priva di start/end minuti: heatmap non generata")
            return

        durations = [
            max(0, int(end) - int(start))
            for start, end in zip(df["start_minute"], df["end_minute"])
            if pd.notna(start) and pd.notna(end)
        ]

        durations = [d for d in durations if d > 0]
        if not durations:
            logger.info("Durate segmenti non positive: heatmap non generata")
            return

        base_minutes = reduce(math.gcd, durations)
        base_minutes = math.gcd(base_minutes, 1440)
        if base_minutes <= 0:
            base_minutes = 60

        n_rows = 1440 // base_minutes
        n_cols = len(unique_keys)

        demand_matrix = np.zeros((n_rows, n_cols), dtype=float)
        assigned_matrix = np.zeros((n_rows, n_cols), dtype=float)

        for row in df.itertuples():
            key = (row.day, row.role)
            col = key_to_col.get(key)
            if col is None:
                continue

            try:
                start_min = int(row.start_minute)
                end_min = int(row.end_minute)
            except (TypeError, ValueError):
                continue

            duration = max(0, end_min - start_min)
            if duration <= 0:
                continue

            demand_val = float(row.demand)
            assigned_val = float(row.assigned)

            start_idx = max(0, min(n_rows, start_min // base_minutes))
            end_idx = max(start_idx + 1, min(n_rows, math.ceil(end_min / base_minutes)))

            for idx in range(start_idx, end_idx):
                bucket_start = idx * base_minutes
                bucket_end = bucket_start + base_minutes
                overlap = min(end_min, bucket_end) - max(start_min, bucket_start)
                if overlap <= 0:
                    continue
                weight = overlap / duration
                demand_matrix[idx, col] += demand_val * weight
                assigned_matrix[idx, col] += assigned_val * weight

        shortfall_matrix = np.maximum(demand_matrix - assigned_matrix, 0.0)

        fig = plt.figure(figsize=(max(6, n_cols * 1.5), 8))
        ax = fig.add_subplot(111)
        heatmap = ax.imshow(shortfall_matrix, aspect="auto", cmap="Reds", origin="lower")

        for col_idx in range(1, n_cols):
            ax.axvline(x=col_idx - 0.5, color="black", linestyle="-", linewidth=2.5, alpha=1.0)

        ax.set_xlabel("Giorno / Ruolo")
        ax.set_ylabel("Orario")
        ax.set_xticks(range(n_cols))
        ax.set_xticklabels(labels, rotation=0)

        tick_interval = max(1, int(round(60 / base_minutes)))
        tick_positions = list(range(0, n_rows, tick_interval))
        tick_labels = [
            f"{(idx * base_minutes) // 60:02d}:{(idx * base_minutes) % 60:02d}"
            for idx in tick_positions
        ]
        ax.set_yticks(tick_positions)
        ax.set_yticklabels(tick_labels)

        ax.set_title("Shortfall di copertura (rosso = scopertura)")
        fig.colorbar(heatmap, ax=ax, label="Shortfall (persona-minuti)")
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
