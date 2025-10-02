# tests/test_e2e_repo_data.py
import inspect
from types import SimpleNamespace
from pathlib import Path

import pandas as pd
import pytest
import yaml

precompute = pytest.importorskip("src.precompute", reason="src.precompute non disponibile")
model_cp = pytest.importorskip("src.model_cp", reason="src.model_cp non disponibile")

DATA_DIR = Path("data")


def _read_inputs():
    needed = ["shifts.csv", "windows.csv", "preferences.csv", "employees.csv", "config.yaml"]
    missing = [n for n in needed if not (DATA_DIR / n).exists()]
    assert not missing, f"Mancano file dati: {missing}"

    shifts_df = pd.read_csv(DATA_DIR / "shifts.csv")
    windows_df = pd.read_csv(DATA_DIR / "windows.csv")
    preferences_df = pd.read_csv(DATA_DIR / "preferences.csv")
    employees_df = pd.read_csv(DATA_DIR / "employees.csv")
    with open(DATA_DIR / "config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    # guard-rail header
    cols = set(shifts_df.columns)
    assert "shift_id" in cols and "dtshift_id" not in cols, f"Header shifts.csv non valido: {cols}"

    return shifts_df, windows_df, preferences_df, employees_df, config


def _call_with_supported_kwargs(fn, pool: dict):
    """Chiama fn passando solo i kwargs supportati dalla sua signature."""
    sig = inspect.signature(fn)
    allowed = {k: v for k, v in pool.items() if k in sig.parameters and v is not None}
    return fn(**allowed), sig


def _build_adaptive_slots(windows_df, config):
    assert hasattr(precompute, "build_adaptive_slots"), "Manca precompute.build_adaptive_slots"
    win_cfg = (config.get("windows") or {})

    # proviamo vari alias per il parametro windows
    windows_aliases = {
        "windows_df": windows_df,
        "windows": windows_df,
        "df": windows_df,
        "windows_data": windows_df,
    }
    # possibili extra param (se esistono nella signature li passiamo)
    extra_pool = {
        "midnight_policy": win_cfg.get("midnight_policy", "split"),
        "warn_slots_threshold": win_cfg.get("warn_slots_threshold"),
        "hard_slots_threshold": win_cfg.get("hard_slots_threshold"),
    }

    errors = []
    for k, v in windows_aliases.items():
        pool = {k: v, **extra_pool}
        try:
            adaptive, sig = _call_with_supported_kwargs(precompute.build_adaptive_slots, pool)
            # conta slot
            if isinstance(adaptive, dict) and "slots" in adaptive:
                num_slots = len(adaptive["slots"])
            elif hasattr(adaptive, "slots"):
                num_slots = len(adaptive.slots)
            else:
                num_slots = len(adaptive)
            assert num_slots > 0, "build_adaptive_slots ha prodotto 0 slot"
            return adaptive
        except TypeError as e:
            errors.append(f"tentativo con '{k}': {e}")
        except AssertionError as e:
            errors.append(f"tentativo con '{k}': {e}")

    # ultimo tentativo: senza kwargs (magari legge da stato globale)
    try:
        adaptive = precompute.build_adaptive_slots()
        num_slots = len(adaptive["slots"]) if isinstance(adaptive, dict) and "slots" in adaptive \
            else (len(adaptive.slots) if hasattr(adaptive, "slots") else len(adaptive))
        assert num_slots > 0, "build_adaptive_slots ha prodotto 0 slot"
        return adaptive
    except Exception as e:
        sig = inspect.signature(precompute.build_adaptive_slots)
        pytest.fail(
            "Impossibile chiamare precompute.build_adaptive_slots con i parametri noti.\n"
            f"Signature: {sig}\n"
            f"Tentativi falliti: \n- " + "\n- ".join(errors) + f"\nUltimo errore: {repr(e)}"
        )


def _construct_solver(adaptive, config, *, shifts_df, windows_df, preferences_df, employees_df):
    assert hasattr(model_cp, "ShiftSchedulingCpSolver"), "Manca ShiftSchedulingCpSolver in src.model_cp"
    Solver = model_cp.ShiftSchedulingCpSolver
    sig = inspect.signature(Solver)

    data = SimpleNamespace(
        shifts_df=shifts_df,
        windows_df=windows_df,
        preferences_df=preferences_df,
        employees_df=employees_df,
        config=config,
    )

    # proviamo combinazioni decrescenti
    combos = [
        {"data": data, "adaptive_slot_data": adaptive, "config": config},
        {"data": data, "adaptive_slot_data": adaptive},
        {"adaptive_slot_data": adaptive, "config": config},
        {"data": data, "config": config},
        # in caso il costruttore accetti direttamente i df:
        {"shifts_df": shifts_df, "windows_df": windows_df, "preferences_df": preferences_df,
         "employees_df": employees_df, "adaptive_slot_data": adaptive, "config": config},
    ]

    errors = []
    for kwargs in combos:
        try:
            # filtra solo i param accettati dalla signature
            allowed = {k: v for k, v in kwargs.items() if k in sig.parameters}
            if not allowed:
                continue
            return Solver(**allowed)
        except TypeError as e:
            errors.append(f"{kwargs.keys()} -> {e}")

    pytest.fail(
        "Impossibile costruire ShiftSchedulingCpSolver con le combinazioni provate.\n"
        f"Signature del costruttore: {sig}\n"
        "Errori:\n- " + "\n- ".join(errors)
    )


def _set_time_limit_if_supported(solver, seconds=30.0):
    if hasattr(solver, "params"):
        try:
            solver.params.max_time_in_seconds = float(seconds)
        except Exception:
            pass
    if hasattr(solver, "set_time_limit_seconds"):
        try:
            solver.set_time_limit_seconds(int(seconds))
        except Exception:
            pass


def test_full_pipeline_shift_scheduling():
    shifts_df, windows_df, preferences_df, employees_df, config = _read_inputs()
    adaptive = _build_adaptive_slots(windows_df, config)
    solver = _construct_solver(
        adaptive, config,
        shifts_df=shifts_df, windows_df=windows_df,
        preferences_df=preferences_df, employees_df=employees_df,
    )
    _set_time_limit_if_supported(solver, 30)

    status = solver.solve()
    status_str = str(status).upper()
    assert ("OPTIMAL" in status_str) or ("FEASIBLE" in status_str), f"Status inatteso: {status}"

    # checks hard solo se disponibili
    for check in ("check_hours_constraints", "check_rest_constraints", "check_demand_coverage"):
        if hasattr(solver, check):
            assert getattr(solver, check)(), f"Violazione vincoli hard rilevata da {check}()."


def test_shifts_header_guard():
    cols = set(pd.read_csv(DATA_DIR / "shifts.csv").columns)
    assert "shift_id" in cols
    assert "dtshift_id" not in cols
