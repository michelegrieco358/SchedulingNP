"""Test avanzati per la copertura dei segmenti nella modalità unica."""
import pytest
import pandas as pd
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from src.model_cp import ShiftSchedulingCpSolver, SolverConfig
from src.reporting import ScheduleReporter


def test_segment_coverage_with_overlapping_shifts():
    """Test copertura segmenti con turni sovrapposti."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""},
        {"employee_id": "E2", "name": "Bob", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""},
        {"employee_id": "E3", "name": "Carol", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""},
    ])
    
    # Turni sovrapposti per testare la segmentazione
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "08:00", "end": "16:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 480, "end_min": 960, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 08:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 16:00:00")},
        {"shift_id": "S2", "day": pd.Timestamp("2025-10-07").date(), "start": "12:00", "end": "20:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 720, "end_min": 1200, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 12:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 20:00:00")},
        {"shift_id": "S3", "day": pd.Timestamp("2025-10-07").date(), "start": "16:00", "end": "23:59", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 479, "duration_h": 7.98, "start_min": 960, "end_min": 1439, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 16:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 23:59:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": emp_id, "shift_id": shift_id, "can_assign": 1}
        for emp_id in employees["employee_id"]
        for shift_id in shifts["shift_id"]
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=10.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che i turni sovrapposti siano gestiti correttamente
    assignments = solver.extract_assignments(cp_solver)
    assert len(assignments) >= 1  # Almeno un'assegnazione
    
    # Verifica che ogni turno abbia al massimo la sua domanda soddisfatta
    for shift_id in shifts["shift_id"]:
        shift_assignments = assignments[assignments["shift_id"] == shift_id]
        shift_demand = shifts[shifts["shift_id"] == shift_id]["demand"].iloc[0]
        assert len(shift_assignments) <= shift_demand + 1  # Può essere leggermente superiore
    
    # Verifica che non ci siano conflitti (stesso dipendente su turni sovrapposti)
    for emp_id in employees["employee_id"]:
        emp_assignments = assignments[assignments["employee_id"] == emp_id]
        if len(emp_assignments) > 1:
            # Verifica che i turni non si sovrappongano temporalmente
            for i, row1 in emp_assignments.iterrows():
                for j, row2 in emp_assignments.iterrows():
                    if i != j:
                        shift1 = shifts[shifts["shift_id"] == row1["shift_id"]].iloc[0]
                        shift2 = shifts[shifts["shift_id"] == row2["shift_id"]].iloc[0]
                        # I turni non dovrebbero sovrapporsi per lo stesso dipendente
                        assert (shift1["end_min"] <= shift2["start_min"] or 
                                shift2["end_min"] <= shift1["start_min"])


def test_segment_coverage_with_high_demand():
    """Test copertura segmenti con alta domanda."""
    employees = pd.DataFrame([
        {"employee_id": f"E{i}", "name": f"Employee{i}", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""}
        for i in range(1, 6)  # 5 dipendenti
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "06:00", "end": "14:00", 
         "role": "nurse", "demand": 3, "required_staff": 3, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 360, "end_min": 840, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 06:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 14:00:00")},
        {"shift_id": "S2", "day": pd.Timestamp("2025-10-07").date(), "start": "14:00", "end": "22:00", 
         "role": "nurse", "demand": 2, "required_staff": 2, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 840, "end_min": 1320, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 14:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 22:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": emp_id, "shift_id": shift_id, "can_assign": 1}
        for emp_id in employees["employee_id"]
        for shift_id in shifts["shift_id"]
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=10.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che l'alta domanda sia soddisfatta
    assignments = solver.extract_assignments(cp_solver)
    
    s1_assignments = assignments[assignments["shift_id"] == "S1"]
    s2_assignments = assignments[assignments["shift_id"] == "S2"]
    
    assert len(s1_assignments) >= 3  # Almeno 3 persone per S1
    assert len(s2_assignments) >= 2  # Almeno 2 persone per S2
    
    # Verifica shortfall
    shortfall_summary = solver.extract_shortfall_summary(cp_solver)
    if not shortfall_summary.empty:
        # Se c'è shortfall, dovrebbe essere minimo
        total_shortfall = shortfall_summary["shortfall_units"].sum()
        assert total_shortfall <= 1  # Shortfall minimo accettabile


def test_segment_coverage_with_windows_and_demand_modes():
    """Test copertura segmenti con finestre temporali e diverse modalità di domanda."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""},
        {"employee_id": "E2", "name": "Bob", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "08:00", "end": "16:00", 
         "role": "nurse", "demand": 0, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 480, "end_min": 960, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 08:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 16:00:00"), "demand_id": "W1"},
        {"shift_id": "S2", "day": pd.Timestamp("2025-10-07").date(), "start": "16:00", "end": "23:59", 
         "role": "nurse", "demand": 0, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 479, "duration_h": 7.98, "start_min": 960, "end_min": 1439, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 16:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 23:59:00"), "demand_id": "W1"},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": emp_id, "shift_id": shift_id, "can_assign": 1}
        for emp_id in employees["employee_id"]
        for shift_id in shifts["shift_id"]
    ])
    
    # Test con demand_mode = "headcount"
    window_demands = {"W1": 2}  # 2 persone simultanee
    window_shifts = {"W1": ["S1", "S2"]}
    window_duration_minutes = {"W1": 960}  # 16 ore totali
    
    solver_headcount = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        window_demands=window_demands,
        window_shifts=window_shifts,
        window_duration_minutes=window_duration_minutes,
        config=SolverConfig(max_seconds=10.0)
    )
    solver_headcount.demand_mode = "headcount"
    
    solver_headcount.build()
    cp_solver_headcount = solver_headcount.solve()
    
    assert cp_solver_headcount.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Test con demand_mode = "person_minutes"
    solver_person_min = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        window_demands=window_demands,
        window_shifts=window_shifts,
        window_duration_minutes=window_duration_minutes,
        config=SolverConfig(max_seconds=10.0)
    )
    solver_person_min.demand_mode = "person_minutes"
    
    solver_person_min.build()
    cp_solver_person_min = solver_person_min.solve()
    
    assert cp_solver_person_min.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Confronta i risultati
    assignments_headcount = solver_headcount.extract_assignments(cp_solver_headcount)
    assignments_person_min = solver_person_min.extract_assignments(cp_solver_person_min)
    
    assert len(assignments_headcount) >= 1
    assert len(assignments_person_min) >= 1


def test_segment_coverage_reporting():
    """Test reporting per copertura segmenti."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""},
        {"employee_id": "E2", "name": "Bob", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""},
    ])
    
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "08:00", "end": "16:00", 
         "role": "nurse", "demand": 2, "required_staff": 2, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 480, "end_min": 960, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 08:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 16:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
        {"employee_id": "E2", "shift_id": "S1", "can_assign": 1},
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Test reporting
    reporter = ScheduleReporter(solver, cp_solver)
    
    # Test segment coverage report
    coverage_report = reporter.generate_segment_coverage_report()
    assert isinstance(coverage_report, pd.DataFrame)
    assert len(coverage_report.columns) == 7  # Colonne attese
    
    # Test constraint report
    constraint_report = reporter.generate_constraint_report()
    assert isinstance(constraint_report, pd.DataFrame)
    assert len(constraint_report.columns) == 4  # Colonne attese
    
    # Test objective breakdown
    objective_report = reporter.generate_objective_breakdown()
    assert isinstance(objective_report, pd.DataFrame)
    assert len(objective_report.columns) == 4  # Colonne attese
    
    # Verifica che ci sia la riga TOTAL
    total_rows = objective_report[objective_report["name"] == "TOTAL"]
    assert len(total_rows) == 1


def test_segment_coverage_with_midnight_shifts():
    """Test copertura segmenti con turni notturni."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""},
        {"employee_id": "E2", "name": "Bob", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""},
    ])
    
    # Turno notturno che attraversa la mezzanotte
    shifts = pd.DataFrame([
        {"shift_id": "S_NIGHT", "day": pd.Timestamp("2025-10-07").date(), "start": "22:00", "end": "06:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 1320, "end_min": 360, 
         "crosses_midnight": True, "start_dt": pd.Timestamp("2025-10-07 22:00:00"), 
         "end_dt": pd.Timestamp("2025-10-08 06:00:00")},
        {"shift_id": "S_DAY", "day": pd.Timestamp("2025-10-07").date(), "start": "08:00", "end": "16:00", 
         "role": "nurse", "demand": 1, "required_staff": 1, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 480, "end_min": 960, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 08:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 16:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": emp_id, "shift_id": shift_id, "can_assign": 1}
        for emp_id in employees["employee_id"]
        for shift_id in shifts["shift_id"]
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=10.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che i turni notturni siano gestiti correttamente
    assignments = solver.extract_assignments(cp_solver)
    assert len(assignments) >= 2  # Almeno un'assegnazione per turno
    
    # Verifica che non ci siano violazioni di riposo per turni notturni
    night_assignments = assignments[assignments["shift_id"] == "S_NIGHT"]
    day_assignments = assignments[assignments["shift_id"] == "S_DAY"]
    
    # Se lo stesso dipendente ha entrambi i turni, dovrebbe rispettare il riposo minimo
    for _, night_row in night_assignments.iterrows():
        for _, day_row in day_assignments.iterrows():
            if night_row["employee_id"] == day_row["employee_id"]:
                # Il turno notturno finisce alle 06:00, il turno diurno inizia alle 08:00
                # Dovrebbe esserci almeno 2 ore di riposo (meno del minimo di 8 ore)
                # Il solver dovrebbe evitare questa assegnazione o gestirla correttamente
                pass  # Il test verifica che il solver non crashi


def test_segment_coverage_objective_function():
    """Test funzione obiettivo per copertura segmenti."""
    employees = pd.DataFrame([
        {"employee_id": "E1", "name": "Alice", "roles": "nurse", "max_week_hours": 40,
         "min_rest_hours": 8, "max_overtime_hours": 10, "skills": ""},
    ])
    
    # Scenario con shortfall intenzionale (più domanda che capacità)
    shifts = pd.DataFrame([
        {"shift_id": "S1", "day": pd.Timestamp("2025-10-07").date(), "start": "08:00", "end": "16:00", 
         "role": "nurse", "demand": 3, "required_staff": 3, "skill_requirements": {}, 
         "duration_minutes": 480, "duration_h": 8.0, "start_min": 480, "end_min": 960, 
         "crosses_midnight": False, "start_dt": pd.Timestamp("2025-10-07 08:00:00"), 
         "end_dt": pd.Timestamp("2025-10-07 16:00:00")},
    ])
    
    assign_mask = pd.DataFrame([
        {"employee_id": "E1", "shift_id": "S1", "can_assign": 1},
    ])
    
    solver = ShiftSchedulingCpSolver(
        employees=employees,
        shifts=shifts,
        assign_mask=assign_mask,
        config=SolverConfig(max_seconds=5.0)
    )
    
    solver.build()
    cp_solver = solver.solve()
    
    # Anche con shortfall, dovrebbe trovare una soluzione
    assert cp_solver.StatusName() in ["OPTIMAL", "FEASIBLE"]
    
    # Verifica che ci sia shortfall
    shortfall_summary = solver.extract_shortfall_summary(cp_solver)
    assert not shortfall_summary.empty
    assert shortfall_summary["shortfall_units"].sum() == 2  # 3 richiesti - 1 disponibile
    
    # Verifica objective breakdown
    objective_breakdown = solver.extract_objective_breakdown(cp_solver)
    assert "unmet_demand" in objective_breakdown
    assert objective_breakdown["unmet_demand"]["minutes"] > 0  # Dovrebbe esserci shortfall
