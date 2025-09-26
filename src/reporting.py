"""Modulo per la generazione di report diagnostici delle soluzioni di scheduling.

Questo modulo fornisce strumenti per analizzare i risultati dell'ottimizzazione
e generare report dettagliati sulla copertura, i vincoli e l'obiettivo.
"""
from pathlib import Path
import logging
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
    
    def __init__(self, solver: Any, cp_solver: cp_model.CpSolver):
        """Inizializza il reporter.
        
        Args:
            solver: Istanza di ShiftSchedulingCpSolver
            cp_solver: Solver CP-SAT con soluzione
        """
        self.solver = solver
        self.cp_solver = cp_solver
        self.output_dir = Path("reports")
        self.output_dir.mkdir(exist_ok=True)

    def generate_segment_coverage_report(self) -> pd.DataFrame:
        """Genera report di copertura per segmenti temporali.
        
        Returns:
            DataFrame con colonne: segment_start, segment_end, demand, assigned, shortfall, overstaffing
        """
        coverages = []
        
        # Modalità unica turni integri: usa sempre segment_demands e segment_shortfall_vars
        if hasattr(self.solver, 'segment_demands') and self.solver.segment_demands:
            for segment_id, demand in self.solver.segment_demands.items():
                shortfall = self.cp_solver.Value(self.solver.segment_shortfall_vars[segment_id])
                assigned = demand - shortfall
                overstaffing = max(0, assigned - demand)
                
                try:
                    start_min, end_min = self.solver.adaptive_slot_data.segment_bounds[segment_id]
                    start_time = f"{start_min // 60:02d}:{start_min % 60:02d}"
                    end_time = f"{end_min // 60:02d}:{end_min % 60:02d}"
                except (AttributeError, KeyError):
                    start_time = "??:??"
                    end_time = "??:??"
                
                coverages.append(SegmentCoverage(
                    segment_id=segment_id,
                    start_time=start_time,
                    end_time=end_time,
                    demand=demand,
                    assigned=assigned,
                    shortfall=shortfall,
                    overstaffing=overstaffing
                ))
        # Modalità slot adattivi rimossa - ora usa solo segment_shortfall_vars
        
        # Converti in DataFrame
        columns = ["segment_id", "start_time", "end_time", "demand", "assigned", "shortfall", "overstaffing"]
        if coverages:
            df = pd.DataFrame([vars(c) for c in coverages])
            df = df[columns]
        else:
            # Se non ci sono dati, crea un DataFrame vuoto con le colonne corrette
            df = pd.DataFrame(columns=columns)
        
        # Salva su file
        output_path = self.output_dir / "segment_coverage.csv"
        df.to_csv(output_path, index=False)
        logger.info(f"Report copertura segmenti salvato in {output_path}")
        
        # Stampa sommario su console
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
        
        # Calcola statistiche globali
        total_segments = len(coverage_df)
        covered = len(coverage_df[coverage_df["shortfall"] == 0])
        partial = len(coverage_df[(coverage_df["shortfall"] > 0) & (coverage_df["assigned"] > 0)])
        uncovered = len(coverage_df[coverage_df["assigned"] == 0])
        
        # Stampa intestazione con statistiche globali
        print(f"{'Intervallo':<15} {'Stato':^6} {'Rich':>5} {'Ass':>5} {'Delta':>6} | {'Copertura':<20}")
        print("-" * 60)
        
        # Stampa dettagli per ogni segmento con barra di copertura
        for _, row in coverage_df.iterrows():
            if row["demand"] == 0:
                coverage_pct = 100
            else:
                coverage_pct = (row["assigned"] / row["demand"]) * 100
            
            # Calcola barra di copertura
            bar_width = 20
            filled = int((coverage_pct / 100) * bar_width)
            if coverage_pct == 100:
                bar = "█" * filled
                status = "✓"
            elif coverage_pct == 0:
                bar = "░" * bar_width
                status = "✗"
            else:
                bar = "█" * filled + "░" * (bar_width - filled)
                status = "~"
            
            print(f"{row['start_time']}-{row['end_time']:<6} "
                  f"[{status:^4}] "
                  f"{row['demand']:4d} "
                  f"{row['assigned']:4d} "
                  f"{row['overstaffing'] - row['shortfall']:+5d} | "
                  f"{bar}")
        
        # Stampa sommario finale
        print("-" * 60)
        print(f"Totale segmenti: {total_segments:3d}  "
              f"Coperti: {covered:3d} ✓  "
              f"Parziali: {partial:3d} ~  "
              f"Scoperti: {uncovered:3d} ✗")
        
        # Genera e salva plot se ci sono dati
        if not coverage_df.empty:
            self._plot_coverage(coverage_df)

    def _plot_coverage(self, coverage_df: pd.DataFrame) -> None:
        """Genera un grafico della domanda e copertura per intervallo temporale."""
        try:
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
            from datetime import datetime, timedelta
            
            # Converti orari in datetime per il plot
            base_date = datetime.now().date()
            x_times = []
            for start, end in zip(coverage_df["start_time"], coverage_df["end_time"]):
                h_start, m_start = map(int, start.split(":"))
                h_end, m_end = map(int, end.split(":"))
                x_times.append([
                    datetime.combine(base_date, datetime.min.time()) + timedelta(hours=h_start, minutes=m_start),
                    datetime.combine(base_date, datetime.min.time()) + timedelta(hours=h_end, minutes=m_end)
                ])
            
            # Crea figura
            plt.figure(figsize=(12, 6))
            
            # Plot domanda
            for i in range(len(coverage_df)):
                plt.bar(x_times[i][0], coverage_df.iloc[i]["demand"], 
                       width=(x_times[i][1] - x_times[i][0]).total_seconds()/3600,
                       alpha=0.3, color='blue', label='Domanda' if i == 0 else None)
            
            # Plot copertura
            for i in range(len(coverage_df)):
                plt.bar(x_times[i][0], coverage_df.iloc[i]["assigned"],
                       width=(x_times[i][1] - x_times[i][0]).total_seconds()/3600,
                       alpha=0.6, color='green', label='Assegnati' if i == 0 else None)
            
            # Formattazione
            plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
            plt.gca().xaxis.set_major_locator(mdates.HourLocator(interval=2))
            plt.gcf().autofmt_xdate()
            
            plt.title('Domanda vs Copertura per Intervallo')
            plt.xlabel('Ora')
            plt.ylabel('Personale')
            plt.legend()
            plt.grid(True, alpha=0.3)
            
            # Salva plot
            plot_path = self.output_dir / "coverage_plot.png"
            plt.savefig(plot_path)
            plt.close()
            
            logger.info(f"Grafico copertura salvato in {plot_path}")
            
        except ImportError:
            logger.warning("matplotlib non installato - impossibile generare il grafico")
        except Exception as e:
            logger.error(f"Errore nella generazione del grafico: {e}")

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
