import argparse
import sys
import json
import logging
from types import SimpleNamespace
from pathlib import Path
from datetime import datetime, time, timedelta
from dateutil import parser as dtparser

import pandas as pd
import warnings

try:
    from .time_utils import normalize_2400, parse_hhmm_to_min
except ImportError:  # fallback when running as a script
    from time_utils import normalize_2400, parse_hhmm_to_min  # type: ignore


logger = logging.getLogger(__name__)

REQUIRED_EMP_COLS = ["employee_id", "name", "roles", "max_week_hours", "min_rest_hours", "max_overtime_hours"]
SHIFT_BASE_COLS = ["shift_id", "day", "start", "end", "role"]
WINDOW_BASE_COLS = ["window_id", "day", "window_start", "window_end", "role", "window_demand"]
REQUIRED_AVAIL_COLS = ["employee_id", "shift_id", "is_available"]


def _ensure_columns(df: pd.DataFrame, required_cols: list, name: str):
    """ check che siano presenti le colonne necessarie nei file csv """
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"{name}: colonne mancanti: {missing}")


def _parse_time_hhmm(value: str | time) -> time:
    """Parsa un orario in formato HH:MM in ``datetime.time``."""
    if isinstance(value, time):
        return value
    try:
        minutes = parse_hhmm_to_min(str(value))
    except ValueError as exc:
        raise ValueError(f"Orario non valido '{value}' (atteso HH:MM)") from exc
    return _minutes_to_time(minutes)


def _minutes_to_time(minutes: int) -> time:
    minutes = normalize_2400(int(minutes))
    if minutes == 1440:
        return time(0, 0)
    return time(minutes // 60, minutes % 60)


def _parse_date_iso(s: str) -> datetime.date:
    """Parsa stringa della data in datetime.time. Raise error se formato non valido."""
    try:
        return dtparser.isoparse(s).date()
    except Exception as e:
        raise ValueError(f"Data non valida '{s}' (atteso YYYY-MM-DD)") from e


def _compute_duration_minutes(start_min: int, end_min: int, shift_id: str) -> int:
    start_min = normalize_2400(int(start_min))
    end_min = normalize_2400(int(end_min))
    if end_min == start_min:
        if end_min in (0, 1440):
            return 1440
        raise ValueError(f"shifts.csv: turno {shift_id} ha stesso orario di inizio/fine ({start_min}) non ammesso")
    duration = end_min - start_min
    if duration <= 0:
        duration += 1440
    if duration <= 0:
        raise ValueError(f"shifts.csv: durata calcolata non valida per {shift_id}")
    return duration


def _parse_skill_list(value: str) -> set[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return set()
    text = str(value).strip()
    if not text:
        return set()
    tokens = [item.strip() for item in text.split(',') if item and item.strip()]
    return set(tokens)


def _parse_window_skills(raw_value, window_id: str) -> dict[str, int]:
    """Parse skills requirements for windows from format 'skill1:qty1,skill2:qty2'."""
    if raw_value is None or (isinstance(raw_value, float) and pd.isna(raw_value)):
        return {}
    text_value = str(raw_value).strip()
    if not text_value:
        return {}

    normalized: dict[str, int] = {}
    for chunk in text_value.split(','):
        part = chunk.strip()
        if not part:
            continue
        if ':' not in part:
            raise ValueError(f"windows.csv: skills per {window_id} deve usare formato skill:quantity")
        skill_name, value_part = part.split(':', 1)
        skill_name = skill_name.strip()
        if not skill_name:
            raise ValueError(f"windows.csv: skill vuota nella finestra {window_id}")
        try:
            qty_int = int(str(value_part).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"windows.csv: valore non intero per skill {skill_name} della finestra {window_id}: {value_part}") from exc
        if qty_int < 0:
            raise ValueError(f"windows.csv: valore negativo per skill {skill_name} della finestra {window_id}")
        if qty_int > 0:
            normalized[skill_name] = qty_int

    return normalized


def _normalize_contracted_hours(df: pd.DataFrame) -> None:
    """
    Normalizza la colonna contracted_hours implementando la logica di rifattorizzazione:
    
    1. Se contracted_hours è valorizzata e min_hours != max_week_hours, mostra WARNING per incoerenza
    2. Se contracted_hours è vuota e min_hours == max_week_hours, imposta contracted_hours = min_hours
    3. Se contracted_hours è valorizzata ma min_hours e max_week_hours non sono presenti, imposta entrambi uguali a contracted_hours
    4. Assicura che contracted_hours sia numerica (float) e NaN se mancante
    """
    # Assicura che la colonna contracted_hours esista
    if "contracted_hours" not in df.columns:
        df["contracted_hours"] = pd.NA
    
    # Assicura che min_week_hours esista (può essere mancante per alcuni dipendenti)
    if "min_week_hours" not in df.columns:
        df["min_week_hours"] = pd.NA
    
    # Converte a numeric, mantenendo NaN per valori mancanti
    df["contracted_hours"] = pd.to_numeric(df["contracted_hours"], errors="coerce")
    df["min_week_hours"] = pd.to_numeric(df["min_week_hours"], errors="coerce")
    
    for idx, row in df.iterrows():
        emp_id = row["employee_id"]
        contracted_h = row["contracted_hours"]
        min_h = row["min_week_hours"]
        max_h = row["max_week_hours"]
        
        # Caso 1: contracted_hours valorizzata
        if pd.notna(contracted_h):
            # Caso 1a: Controlla incoerenza con min_week_hours e max_week_hours
            if pd.notna(min_h) and min_h != max_h:
                warnings.warn(
                    f"employees.csv: Dipendente {emp_id} ha contracted_hours={contracted_h} "
                    f"ma min_week_hours={min_h} != max_week_hours={max_h}. "
                    f"Dati incoerenti: per lavoratori contrattualizzati min_week_hours dovrebbe essere uguale a max_week_hours.",
                    RuntimeWarning
                )
            
            # Caso 1b: Se min_week_hours e max_week_hours non sono presenti, impostali uguali a contracted_hours
            if pd.isna(min_h):
                df.at[idx, "min_week_hours"] = contracted_h
                logger.debug(f"Dipendente {emp_id}: impostato min_week_hours = {contracted_h} (da contracted_hours)")
            
            # Per coerenza, se contracted_hours è presente, max_week_hours dovrebbe essere >= contracted_hours
            if max_h < contracted_h:
                warnings.warn(
                    f"employees.csv: Dipendente {emp_id} ha max_week_hours={max_h} < contracted_hours={contracted_h}. "
                    f"Questo potrebbe causare problemi nel modello.",
                    RuntimeWarning
                )
        
        # Caso 2: contracted_hours vuota ma min_week_hours == max_week_hours
        elif pd.isna(contracted_h) and pd.notna(min_h) and min_h == max_h:
            df.at[idx, "contracted_hours"] = min_h
            logger.debug(f"Dipendente {emp_id}: impostato contracted_hours = {min_h} (min_week_hours == max_week_hours)")
        
        # Caso 3: contracted_hours vuota e min_week_hours != max_week_hours (o min_week_hours mancante)
        # → Lavoratore non contrattualizzato, lascia contracted_hours come NaN
    
    # Assicura che contracted_hours sia float (con NaN per valori mancanti)
    df["contracted_hours"] = df["contracted_hours"].astype("float64")
    df["min_week_hours"] = df["min_week_hours"].astype("float64")
    
    # Log statistiche finali
    contracted_count = df["contracted_hours"].notna().sum()
    total_count = len(df)
    logger.info(
        f"Normalizzazione contracted_hours completata: {contracted_count}/{total_count} dipendenti contrattualizzati"
    )


def _parse_skill_requirements(raw_value, shift_id: str, capacity_hint: int) -> dict[str, int]:
    if raw_value is None or (isinstance(raw_value, float) and pd.isna(raw_value)):
        return {}
    text_value = str(raw_value).strip()
    if not text_value:
        return {}

    if text_value.startswith('{'):
        try:
            parsed_obj = json.loads(text_value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"shifts.csv: skill_requirements JSON non valido per {shift_id}: {text_value}") from exc
        if not isinstance(parsed_obj, dict):
            raise ValueError(f"shifts.csv: skill_requirements per {shift_id} deve essere un oggetto")
        items = list(parsed_obj.items())
    else:
        items = []
        for chunk in text_value.split(','):
            part = chunk.strip()
            if not part:
                continue
            if '=' not in part:
                raise ValueError(f"shifts.csv: skill_requirements per {shift_id} deve usare key=value")
            key, value_part = part.split('=', 1)
            items.append((key, value_part))

    normalized: dict[str, int] = {}
    for key, value in items:
        skill_name = str(key).strip()
        if not skill_name:
            raise ValueError(f"shifts.csv: skill vuota nel turno {shift_id}")
        try:
            qty_int = int(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"shifts.csv: valore non intero per skill {skill_name} del turno {shift_id}: {value}") from exc
        if qty_int < 0:
            raise ValueError(f"shifts.csv: valore negativo per skill {skill_name} del turno {shift_id}")
        if qty_int > 0:
            normalized[skill_name] = qty_int

    total_req = sum(normalized.values())
    if capacity_hint is not None:
        limit = max(1, int(capacity_hint))
        if total_req > limit:
            warnings.warn(
                f"shifts.csv: skill_requirements per {shift_id} sommano {total_req} > capacità di riferimento {limit}",
                RuntimeWarning,
            )
    return normalized





def load_employees(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    _ensure_columns(df, REQUIRED_EMP_COLS, "employees.csv")

    # casting coerenza tipi base
    df["employee_id"] = df["employee_id"].astype(str)
    df["name"] = df["name"].astype(str)
    df["roles"] = df["roles"].astype(str)
    df["max_week_hours"] = pd.to_numeric(df["max_week_hours"], errors="raise").astype(int)
    df["min_rest_hours"] = pd.to_numeric(df["min_rest_hours"], errors="raise").astype(int)
    df["max_overtime_hours"] = pd.to_numeric(df["max_overtime_hours"], errors="raise").astype(int)

    # unicità  id
    if df["employee_id"].duplicated().any():
        dups = df[df["employee_id"].duplicated()]["employee_id"].tolist()
        raise ValueError(f"employees.csv: employee_id duplicati: {dups}")

    # NUOVA LOGICA: Gestione esplicita della colonna contracted_hours
    _normalize_contracted_hours(df)

    # crea colonna di variabili tipo set che indicano i ruoli di ogni dipendente
    if "skills" not in df.columns:
        df["skills"] = ""
    df["skills"] = df["skills"].fillna("").astype(str)
    df["skills_set"] = df["skills"].apply(_parse_skill_list)

    df["roles_set"] = df["roles"].apply(lambda s: set([p.strip() for p in s.split("|") if p.strip() != ""]))
    df["primary_role"] = df["roles"].apply(lambda s: s.split("|")[0].strip() if s else "")

    # controlli rapidi
    if (df["max_week_hours"] <= 0).any():
        raise ValueError("employees.csv: max_week_hours deve essere > 0")
    if (df["min_rest_hours"] < 0).any():
        raise ValueError("employees.csv: min_rest_hours non può essere negativo")
    if (df["max_overtime_hours"] < 0).any():
        raise ValueError("employees.csv: max_overtime_hours non può essere negativo")

    return df


def load_shifts(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    _ensure_columns(df, SHIFT_BASE_COLS, "shifts.csv")

    original_cols = set(df.columns)

    df["shift_id"] = df["shift_id"].astype(str).str.strip()
    if df["shift_id"].duplicated().any():
        dups = df[df["shift_id"].duplicated()]["shift_id"].tolist()
        raise ValueError(f"shifts.csv: shift_id duplicati: {dups}")

    df["day"] = df["day"].apply(_parse_date_iso)
    df["start_min"] = df["start"].apply(parse_hhmm_to_min)
    df["end_min"] = df["end"].apply(parse_hhmm_to_min)
    df["start"] = df["start_min"].apply(_minutes_to_time)
    df["end"] = df["end_min"].apply(_minutes_to_time)
    df["role"] = df["role"].astype(str).str.strip()

    # Only 'demand' field is supported in v2.0+
    if "demand" not in original_cols:
        raise ValueError("shifts.csv: 'demand' column is required. Legacy 'required_staff' is no longer supported.")
    
    if "required_staff" in original_cols:
        raise ValueError("shifts.csv: 'required_staff' field is no longer supported. Use 'demand' instead.")
    
    df["demand"] = pd.to_numeric(df["demand"], errors="coerce").fillna(0).astype(int)
    if (df["demand"] < 0).any():
        raise ValueError("shifts.csv: demand deve essere >= 0")
    
    # Set required_staff = demand for internal consistency
    df["required_staff"] = df["demand"]

    if "skill_requirements" not in original_cols:
        df["skill_requirements"] = ""
    df["skill_requirements"] = df["skill_requirements"].fillna("")
    df["skill_requirements"] = df.apply(
        lambda row: _parse_skill_requirements(
            row["skill_requirements"],
            row["shift_id"],
            int(max(row["demand"], row["required_staff"], 0)),
        ),
        axis=1,
    )

    df["crosses_midnight"] = df["end_min"] <= df["start_min"]
    df["duration_minutes"] = df.apply(
        lambda row: _compute_duration_minutes(row["start_min"], row["end_min"], row["shift_id"]),
        axis=1,
    )
    
    # Add duration_h for solver compatibility
    df["duration_h"] = df["duration_minutes"] / 60.0
    
    # Add start_dt and end_dt for solver compatibility
    df["start_dt"] = pd.to_datetime(df["day"].astype(str) + " " + df["start"].astype(str))
    df["end_dt"] = pd.to_datetime(df["day"].astype(str) + " " + df["end"].astype(str))
    
    # Handle midnight crossing for end_dt
    midnight_mask = df["crosses_midnight"]
    df.loc[midnight_mask, "end_dt"] = df.loc[midnight_mask, "end_dt"] + pd.Timedelta(days=1)

    if "demand_id" not in df.columns:
        df["demand_id"] = ""
    df["demand_id"] = df["demand_id"].fillna("").astype(str).str.strip()

    summary = (
        f"shifts.csv: caricati {len(df)} turni, ruoli: {sorted(df['role'].unique())}, "
        f"domanda media: {df['demand'].mean():.2f}"
    )
    logger.info(summary)
    return df


def load_availability(path: Path, employees: pd.DataFrame, shifts: pd.DataFrame) -> pd.DataFrame:
    df = pd.read_csv(path)
    _ensure_columns(df, REQUIRED_AVAIL_COLS, "availability.csv")

    df["employee_id"] = df["employee_id"].astype(str)
    df["shift_id"] = df["shift_id"].astype(str)
    df["is_available"] = pd.to_numeric(df["is_available"], errors="raise").astype(int)

    # valori ammessi 0/1
    bad = df[~df["is_available"].isin([0, 1])]
    if not bad.empty:
        raise ValueError("availability.csv: is_available deve essere 0 o 1")

    # chiavi devono esistere
    emp_set = set(employees["employee_id"])
    shift_set = set(shifts["shift_id"])
    bad_emp = df[~df["employee_id"].isin(emp_set)]
    bad_shift = df[~df["shift_id"].isin(shift_set)]
    if not bad_emp.empty:
        raise ValueError(f"availability.csv: employee_id non presenti: {sorted(set(bad_emp['employee_id']))}")
    if not bad_shift.empty:
        raise ValueError(f"availability.csv: shift_id non presenti: {sorted(set(bad_shift['shift_id']))}")

    # dedup
    if df.duplicated(subset=["employee_id", "shift_id"]).any():
        raise ValueError("availability.csv: coppie (employee_id, shift_id) duplicate")

    return df


def build_quali_mask(employees: pd.DataFrame, shifts: pd.DataFrame) -> pd.DataFrame:
    """Restituisce solo le coppie dipendente-turno qualificate (qual_ok=1)."""
    roles_exploded = employees[["employee_id", "roles"]].copy()
    roles_exploded["role"] = roles_exploded["roles"].str.split("|")
    roles_exploded = roles_exploded.explode("role")
    roles_exploded["role"] = roles_exploded["role"].fillna("").astype(str).str.strip()
    roles_exploded = roles_exploded[roles_exploded["role"] != ""]

    merged = roles_exploded.merge(shifts[["shift_id", "role"]], on="role", how="inner")
    quali = merged[["employee_id", "shift_id"]].drop_duplicates().copy()
    quali["qual_ok"] = 1
    return quali




def load_overtime_costs(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = ["role", "overtime_cost_per_hour"]
    _ensure_columns(df, required, path.name)

    df["role"] = df["role"].astype(str)
    df["overtime_cost_per_hour"] = pd.to_numeric(df["overtime_cost_per_hour"], errors="raise").astype(float)

    if (df["overtime_cost_per_hour"] < 0).any():
        raise ValueError(f"{path.name}: overtime_cost_per_hour deve essere >= 0")

    if df["role"].duplicated().any():
        dups = df[df["role"].duplicated()]["role"].tolist()
        raise ValueError(f"{path.name}: role duplicati: {dups}")

    return df


def load_preferences(path: Path, employees: pd.DataFrame, shifts: pd.DataFrame) -> pd.DataFrame:
    columns = ["employee_id", "shift_id", "score"]
    if not path.exists():
        return pd.DataFrame(columns=columns)

    df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame(columns=columns)

    _ensure_columns(df, columns, path.name)

    df = df[columns].copy()
    df["employee_id"] = df["employee_id"].astype(str)
    df["shift_id"] = df["shift_id"].astype(str)
    df["score"] = pd.to_numeric(df["score"], errors="coerce")

    if df["score"].isna().any():
        rows = df[df["score"].isna()]
        warnings.warn(
            f"{path.name}: score non numerici scartati per {len(rows)} righe",
            RuntimeWarning,
        )
        df = df[df["score"].notna()]

    if df.empty:
        return pd.DataFrame(columns=columns)

    df.loc[:, "score"] = df["score"].astype(int)
    df.loc[:, "score"] = df["score"].clip(-2, 2)

    valid_employees = set(employees["employee_id"].astype(str))
    valid_shifts = set(shifts["shift_id"].astype(str))

    mask_valid = df["employee_id"].isin(valid_employees) & df["shift_id"].isin(valid_shifts)
    if not mask_valid.all():
        invalid_count = (~mask_valid).sum()
        warnings.warn(
            f"{path.name}: scartate {invalid_count} righe con employee_id/shift_id non validi",
            RuntimeWarning,
        )
        df = df[mask_valid]

    if df.empty:
        return pd.DataFrame(columns=columns)

    df = df.drop_duplicates(subset=["employee_id", "shift_id"], keep="last").reset_index(drop=True)
    return df





def load_windows(
    path: Path,
    shifts: pd.DataFrame | None = None,
    *,
    config: object | None = None,
) -> pd.DataFrame:
    "Carica windows.csv (nuovo schema) restituendo colonne normalizzate in minuti."
    extended_cols = WINDOW_BASE_COLS + ["window_start_min", "window_end_min", "window_minutes"]
    if not path.exists():
        logger.warning("%s non trovato: copertura finestre disabilitata (modalita legacy)", path.name)
        if config is not None and hasattr(config, "windows"):
            try:
                if getattr(config.windows, "coverage_mode", "disabled") != "disabled":
                    config.windows.coverage_mode = "disabled"
            except AttributeError:
                pass
        return pd.DataFrame(columns=extended_cols)

    df = pd.read_csv(path)
    if df.empty:
        logger.warning("%s vuoto: nessuna finestra caricata", path.name)
        return pd.DataFrame(columns=extended_cols)

    _ensure_columns(df, WINDOW_BASE_COLS, path.name)

    df = df.copy()
    df["window_id"] = df["window_id"].astype(str).str.strip()
    df = df[df["window_id"] != ""]
    if df.empty:
        logger.warning("%s: tutte le finestre prive di identificativo valido sono state scartate", path.name)
        return pd.DataFrame(columns=extended_cols)

    if df["window_id"].duplicated().any():
        warnings.warn("windows.csv: window_id duplicati, mantengo l'ultima occorrenza", RuntimeWarning)
        df = df.drop_duplicates(subset="window_id", keep="last")

    df["day"] = df["day"].apply(_parse_date_iso)
    df["window_start_min"] = df["window_start"].apply(parse_hhmm_to_min)
    df["window_end_min"] = df["window_end"].apply(parse_hhmm_to_min)
    df["window_start"] = df["window_start_min"].apply(_minutes_to_time)
    df["window_end"] = df["window_end_min"].apply(_minutes_to_time)
    df["role"] = df["role"].astype(str).str.strip()
    df["window_demand"] = pd.to_numeric(df["window_demand"], errors="coerce").fillna(0).astype(int)

    if (df["window_demand"] < 0).any():
        raise ValueError(f"{path.name}: window_demand deve essere >= 0")

    invalid_bounds = df["window_end_min"] <= df["window_start_min"]
    if invalid_bounds.any():
        bad_ids = df.loc[invalid_bounds, "window_id"].tolist()
        raise ValueError(f"{path.name}: window_end deve essere maggiore di window_start per {bad_ids}")

    df["window_minutes"] = df["window_end_min"] - df["window_start_min"]

    # Parse skills column if present
    if "skills" in df.columns:
        df["skills"] = df["skills"].fillna("")
        df["skill_requirements"] = df.apply(
            lambda row: _parse_window_skills(row["skills"], row["window_id"]),
            axis=1,
        )
        extended_cols = extended_cols + ["skill_requirements"]
    else:
        df["skill_requirements"] = [{}] * len(df)
        extended_cols = extended_cols + ["skill_requirements"]

    if shifts is not None and not shifts.empty:
        known_roles = set(shifts["role"].astype(str).str.strip())
        unknown_roles = sorted(set(df["role"]) - known_roles)
        if unknown_roles:
            raise ValueError(f"{path.name}: ruoli sconosciuti {unknown_roles}")

    df = df.sort_values(["day", "window_start_min", "window_id"]).reset_index(drop=True)
    logger.info("%s: caricate %d finestre (ruoli: %s)", path.name, len(df), sorted(df["role"].unique()))
    return df[extended_cols]


def load_time_off(path: Path, employees: pd.DataFrame) -> pd.DataFrame:
    columns = ["employee_id", "off_start_dt", "off_end_dt", "reason"]
    if not path.exists():
        return pd.DataFrame(columns=columns)

    df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame(columns=columns)

    required = ["employee_id", "day"]
    _ensure_columns(df, required, path.name)

    df = df.copy()
    df["employee_id"] = df["employee_id"].astype(str)
    df["day"] = df["day"].astype(str)

    if "reason" not in df.columns:
        df["reason"] = ""

    def _parse_optional_time(value):
        if pd.isna(value) or str(value).strip() == "":
            return None
        return _parse_time_hhmm(str(value))

    records = []
    for _, row in df.iterrows():
        try:
            day = _parse_date_iso(str(row["day"]))
        except ValueError as exc:
            warnings.warn(f"{path.name}: riga ignorata per data non valida ({row})", RuntimeWarning)
            continue

        start_time = _parse_optional_time(row.get("start_time"))
        end_time = _parse_optional_time(row.get("end_time"))

        if start_time is None:
            start_time = time(0, 0)
        off_start_dt = datetime.combine(day, start_time)

        if end_time is None:
            off_end_dt = datetime.combine(day + timedelta(days=1), time(0, 0))
        else:
            off_end_dt = datetime.combine(day, end_time)
            if off_end_dt <= off_start_dt:
                off_end_dt += timedelta(days=1)

        records.append(
            {
                "employee_id": row["employee_id"],
                "off_start_dt": off_start_dt,
                "off_end_dt": off_end_dt,
                "reason": str(row.get("reason", "")),
            }
        )

    if not records:
        return pd.DataFrame(columns=columns)

    result = pd.DataFrame(records, columns=columns)

    valid_employees = set(employees["employee_id"].astype(str))
    mask_valid = result["employee_id"].isin(valid_employees)
    if not mask_valid.all():
        invalid_count = (~mask_valid).sum()
        warnings.warn(
            f"{path.name}: scartate {invalid_count} righe con employee_id non valido",
            RuntimeWarning,
        )
        result = result[mask_valid]

    if result.empty:
        return pd.DataFrame(columns=columns)

    result = result.drop_duplicates().reset_index(drop=True)
    return result


def apply_time_off(assign_mask: pd.DataFrame, time_off: pd.DataFrame, shifts_norm: pd.DataFrame) -> pd.DataFrame:
    if assign_mask.empty:
        result = assign_mask.copy()
        result["timeoff_block"] = 0
        return result

    result = assign_mask.copy()
    result["timeoff_block"] = 0

    if time_off is None or time_off.empty:
        return result

    required_cols = {"shift_id", "start_dt", "end_dt"}
    if not required_cols.issubset(shifts_norm.columns):
        raise ValueError("shifts_norm deve includere start_dt ed end_dt per applicare i time-off")

    shift_times = shifts_norm[["shift_id", "start_dt", "end_dt"]].drop_duplicates()
    merged = result.merge(shift_times, on="shift_id", how="left")
    merged = merged.merge(time_off, on="employee_id", how="left")

    if merged["off_start_dt"].isna().all():
        return result

    overlap = (
        merged["off_start_dt"].notna()
        & (merged["off_start_dt"] < merged["end_dt"])
        & (merged["off_end_dt"] > merged["start_dt"])
    )
    merged["overlap"] = overlap.astype(int)

    flags = merged.groupby(["employee_id", "shift_id"])["overlap"].max().reset_index()
    if flags.empty:
        return result

    result = result.drop(columns=["timeoff_block"]).merge(flags, on=["employee_id", "shift_id"], how="left")
    result.rename(columns={"overlap": "timeoff_block"}, inplace=True)
    result["timeoff_block"] = result["timeoff_block"].fillna(0).astype(int)
    result["can_assign"] = (result["can_assign"].astype(int) * (1 - result["timeoff_block"])).astype(int)

    blocked = result[result["timeoff_block"] == 1]
    if not blocked.empty:
        total = int(blocked["timeoff_block"].sum())
        per_emp = (
            blocked.groupby("employee_id")["timeoff_block"].sum().sort_values(ascending=False)
        )
        summary = ", ".join(f"{emp}: {int(cnt)}" for emp, cnt in per_emp.items())
        print(f"Time-off: {total} coppie escluse ({summary})")

    return result

def merge_availability(quali_mask: pd.DataFrame, availability: pd.DataFrame) -> pd.DataFrame:
    """
    Combina qualifica + availability: se non c'è riga in availability assume disponibile (1).
    Includiamo anche le coppie presenti solo in availability (qual_ok=0) per diagnosi.
    """
    qualified = quali_mask[["employee_id", "shift_id"]].drop_duplicates().copy()
    qualified["qual_ok"] = 1

    av = availability.copy()
    if not av.empty:
        av["is_available"] = av["is_available"].astype(int)

    merged = qualified.merge(av, on=["employee_id", "shift_id"], how="left")
    merged["is_available"] = merged["is_available"].fillna(1).astype(int)

    if not av.empty:
        extra = av.merge(qualified[["employee_id", "shift_id"]], on=["employee_id", "shift_id"], how="left", indicator=True)
        extra = extra[extra["_merge"] == "left_only"].drop(columns="_merge")
        if not extra.empty:
            extra = extra.assign(qual_ok=0)
            merged = pd.concat([merged, extra[["employee_id", "shift_id", "qual_ok", "is_available"]]], ignore_index=True)

    merged["can_assign"] = (merged["qual_ok"] & merged["is_available"]).astype(int)
    return merged[["employee_id", "shift_id", "can_assign", "qual_ok", "is_available"]]



def load_data_bundle(data_dir: Path, *, config: object | None = None, shifts_only_mode: bool = False) -> SimpleNamespace:
    """Carica i dataset con la nuova architettura (v2.0+)."""
    data_dir = Path(data_dir)

    # windows.csv is required unless in shifts-only mode
    windows_path = data_dir / "windows.csv"
    if not windows_path.exists() and not shifts_only_mode:
        raise FileNotFoundError(
            f"windows.csv is required in {data_dir}. "
            f"Use shifts_only_mode=True if you only need shift-based scheduling without window coverage."
        )

    employees = load_employees(data_dir / "employees.csv")
    shifts = load_shifts(data_dir / "shifts.csv")
    availability = load_availability(data_dir / "availability.csv", employees, shifts)

    quali_mask = build_quali_mask(employees, shifts)
    assign_mask = merge_availability(quali_mask, availability)

    time_off = load_time_off(data_dir / "time_off.csv", employees)
    preferences = load_preferences(data_dir / "preferences.csv", employees, shifts)
    windows_df = load_windows(data_dir / "windows.csv", shifts, config=config)

    shift_duration_minutes = {
        str(row.shift_id): int(row.duration_minutes)
        for row in shifts.itertuples()
    }
    shift_records = {
        str(row.shift_id): {
            "day": row.day,
            "role": row.role,
            "start_min": int(row.start_min),
            "end_min": int(row.end_min),
            "crosses_midnight": bool(row.crosses_midnight),
            "demand": int(row.demand),
            "required_staff": int(row.required_staff),
            "skill_req": dict(row.skill_requirements) if isinstance(row.skill_requirements, dict) else {},
        }
        for row in shifts.itertuples()
    }

    window_records = {
        str(row.window_id): {
            "day": row.day,
            "role": row.role,
            "start_min": int(row.window_start_min),
            "end_min": int(row.window_end_min),
            "window_minutes": int(row.window_minutes),
            "window_demand": int(row.window_demand),
            "skill_req": dict(row.skill_requirements) if hasattr(row, 'skill_requirements') and isinstance(row.skill_requirements, dict) else {},
        }
        for row in windows_df.itertuples()
    }

    emp_skills = {
        str(row.employee_id): set(row.skills_set)
        for row in employees.itertuples()
    }
    eligible = {
        (str(row.employee_id), str(row.shift_id)): bool(row.can_assign)
        for row in assign_mask.itertuples()
    }

    bundle = SimpleNamespace(
        employees_df=employees,
        shifts_df=shifts,
        availability_df=availability,
        quali_mask_df=quali_mask,
        assign_mask_df=assign_mask,
        time_off_df=time_off,
        preferences_df=preferences,
        windows_df=windows_df,
        shifts=shift_records,
        windows=window_records,
        shift_duration_minutes=shift_duration_minutes,
        emp_skills=emp_skills,
        eligible=eligible,
    )
    return bundle


def summarize(employees: pd.DataFrame, shifts: pd.DataFrame, av_mask: pd.DataFrame):
    n_emp = len(employees)
    n_shifts = len(shifts)
    n_assignable = av_mask["can_assign"].sum()
    n_pairs = len(av_mask)

    days = sorted(shifts["day"].unique())
    roles = sorted(shifts["role"].unique())

    print("=== Riepilogo dati ===")
    print(f"Dipendenti: {n_emp}")
    print(f"Turni: {n_shifts} ({len(days)} giorni, ruoli: {roles})")
    print(f"Coppie possibili employeeÃƒâ€”shift: {n_pairs}")
    print(f"Coppie assegnabili (qualifica & disponibilitÃƒÂ ): {n_assignable} ({n_assignable/n_pairs:.1%})")
    print()
    print("Esempi di blocchi non assegnabili (prime 10):")
    print(av_mask[av_mask["can_assign"] == 0].head(10).to_string(index=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Loader & check CSV per scheduling MVP")
    parser.add_argument("--data-dir", type=str, default="data", help="Cartella con employees.csv, shifts.csv, availability.csv")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    emp_path = data_dir / "employees.csv"
    sh_path = data_dir / "shifts.csv"
    av_path = data_dir / "availability.csv"

    if not emp_path.exists() or not sh_path.exists() or not av_path.exists():
        raise FileNotFoundError("Assicura che employees.csv, shifts.csv e availability.csv siano nella cartella indicata.")

    employees = load_employees(emp_path)
    shifts = load_shifts(sh_path)
    availability = load_availability(av_path, employees, shifts)

    quali_mask = build_quali_mask(employees, shifts)
    av_mask = merge_availability(quali_mask, availability)

    summarize(employees, shifts, av_mask)


if __name__ == "__main__":
    sys.exit(main())
