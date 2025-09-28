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

REQUIRED_EMP_COLS = ["employee_id", "name", "roles", "max_week_hours", "min_rest_hours", "max_overtime_hours", "contracted_hours", "min_week_hours"]
SHIFT_BASE_COLS = ["shift_id", "day", "start", "end", "role"]
WINDOW_BASE_COLS = ["window_id", "day", "window_start", "window_end", "role", "window_demand"]
REQUIRED_AVAIL_COLS = ["employee_id", "shift_id", "is_available"]


def _ensure_columns(df: pd.DataFrame, required_cols: list, name: str):
    """ check che siano presenti le colonne necessarie nei file csv """
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"{name}: colonne mancanti: {missing}")


def _coerce_integer_series(
    series: pd.Series,
    *,
    column: str,
    origin: str,
    ids: pd.Series | None = None,
) -> pd.Series:
    """Converte colonne numeriche in Int64 segnalando e arrotondando i valori non interi."""
    numeric = pd.to_numeric(series, errors="coerce")
    rounded = numeric.round()
    decimals_mask = numeric.notna() & (rounded != numeric)
    if decimals_mask.any():
        sample_preview = ""
        if ids is not None:
            offenders = ids.loc[decimals_mask].astype(str)
            sample = offenders.head(5).tolist()
            suffix = ", ..." if len(offenders) > len(sample) else ""
            if sample:
                sample_preview = f" (employee_id: {', '.join(sample)}{suffix})"
        warnings.warn(
            f'{origin}: valori non interi trovati in "{column}"{sample_preview}; arrotondo al piu vicino intero.',
            RuntimeWarning,
        )
    return rounded.astype("Int64")

def _parse_time_hhmm(value: str | time) -> time:
    """Parsa un orario in formato HH:MM in ``datetime.time``."""
    if isinstance(value, time):
        return value
    try:
        minutes = parse_hhmm_to_min(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"Orario non valido '{value}' (atteso HH:MM)") from exc
    return _minutes_to_time(minutes)


def _minutes_to_time(minutes: int) -> time:
    """Converte minuti (0-1440) in datetime.time (00:00-24:00)."""
    minutes = normalize_2400(int(minutes))
    if minutes == 1440:
        return time(0, 0)
    return time(minutes // 60, minutes % 60)


def _parse_date_iso(s: str) -> datetime.date:
    """Parsa stringa della data in datetime.date. Raise error se formato non valido."""
    try:
        return dtparser.isoparse(s).date()
    except Exception as e:
        raise ValueError(f"Data non valida '{s}' (atteso YYYY-MM-DD)") from e


def _compute_duration_minutes(start_min: int, end_min: int, shift_id: str) -> int:
    """Calcola la durata in minuti di un turno, gestendo il caso overnight."""
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


def _parse_skill_list(value) -> set[str]:
    """Parsa una stringa di skills separate da virgola in un set di stringhe."""   
    # 1) qualunque mancante → insieme vuoto
    if pd.isna(value):
        return set()
    # 2) normalizza a stringa e rimuovi spazi
    text = str(value).strip()
    if not text:
        return set()
    # difesa extra se arrivano letterali tipo "nan", "<NA>", "None"
    if text.lower() in {"nan", "<na>", "none"}:
        return set()
    # 3) split sulla virgola, trim e scarto vuoti/placeholder
    tokens = []
    for part in text.split(","):
        t = part.strip()
        if not t:
            continue
        if t.lower() in {"nan", "<na>", "none"}:  # opzionale
            continue
        tokens.append(t)

    return set(tokens)


def _parse_window_skills(raw_value, window_id: str) -> dict[str, int]:
    """Parsa la colonna skills di windows.csv in un dizionario {skill: quantity}."""
    if pd.isna(raw_value):
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
    1. Se contracted_hours è valorizzata e min_week_hours != max_week_hours, mostra WARNING per incoerenza
    2. Se contracted_hours è valorizzata ma min_week_hours e/o max_week_hours non sono presenti,
       imposta entrambi uguali a contracted_hours
    3. Assicura che contracted_hours, min_week_hours e max_week_hours siano interi nullable (Int64)
       e NaN se mancanti
    """
    # --- 1) Assicura che le colonne esistano ---------------------------------
    for col in ("contracted_hours", "min_week_hours", "max_week_hours"):
        if col not in df.columns:
            df[col] = pd.NA

    # --- 2) Converte e normalizza a interi Int64 (coercizione anticipata) ----
    for column in ("contracted_hours", "min_week_hours", "max_week_hours"):
        df[column] = _coerce_integer_series(
            df[column],
            column=column,
            origin="employees.csv",
            ids=df.get("employee_id"),
        )
        # _coerce_integer_series:
        #   - fa to_numeric + round
        #   - emette warning se trova decimali
        #   - restituisce Int64 (nullable)

    # --- 3) Controlli di coerenza e riempimenti ------------------------------
    for idx, row in df.iterrows():
        emp_id = row["employee_id"]
        contracted_h = row["contracted_hours"]
        min_h = row["min_week_hours"]
        max_h = row["max_week_hours"]

        if pd.notna(contracted_h):
            # 3a) Warning se min e max differiscono tra loro
            if pd.notna(min_h) and pd.notna(max_h) and min_h != max_h:
                warnings.warn(
                    f"employees.csv: Dipendente {emp_id} ha min_week_hours={min_h} diverso da max_week_hours={max_h}",
                    RuntimeWarning,
                )
            # 3b) Warning se min o max differiscono da contracted_hours
            for hours, name in [(min_h, "min_week_hours"), (max_h, "max_week_hours")]:
                if pd.notna(hours) and hours != contracted_h:
                    warnings.warn(
                        f"employees.csv: Dipendente {emp_id} ha {name}={hours} diverso da contracted_hours={contracted_h}",
                        RuntimeWarning,
                    )
            # 3c) Se mancano min o max, imposta entrambi uguali a contracted_hours
            if pd.isna(min_h) or pd.isna(max_h):
                df.at[idx, "min_week_hours"] = contracted_h
                df.at[idx, "max_week_hours"] = contracted_h
                logger.debug(
                    f"Dipendente {emp_id}: impostato min_week_hours e max_week_hours = {contracted_h} (da contracted_hours)"
                )

        # Caso 2: contracted_hours mancante e min/max diversi
        # → lavoratore non contrattualizzato, lasciamo contracted_hours NaN

    # --- 4) Statistiche finali ------------------------------------------------
    contracted_count = df["contracted_hours"].notna().sum()
    total_count = len(df)
    logger.info(
        f"Normalizzazione contracted_hours completata: {contracted_count}/{total_count} dipendenti contrattualizzati"
    )


def load_employees(path: Path) -> pd.DataFrame:
    """Carica e normalizza employees.csv."""
    df = pd.read_csv(path, dtype={"employee_id": "string", "name": "string", "roles": "string"})
    _ensure_columns(df, REQUIRED_EMP_COLS, "employees.csv")

    # 1) blocca subito NaN/vuoti su testi richiesti
    for col in ["employee_id", "name", "roles"]:
        s = df[col].str.strip()
        missing = s.isna() | (s == "")
        if missing.any():
            righe = df.index[missing].tolist()
            raise ValueError(f"employees.csv: valori mancanti o vuoti nella colonna '{col}' alle righe: {righe}")

    # 2) normalizza testi (mantieni dtype 'string')
    df["employee_id"] = df["employee_id"].str.strip()
    df["name"] = df["name"].str.strip()
    df["roles"] = df["roles"].str.strip()

    # 3) skills
    if "skills" in df.columns:
        df["skills"] = df["skills"].astype("string").str.strip()
    else:
        df["skills"] = pd.Series("", index=df.index, dtype="string")

    # 4) assicurati che le colonne numeriche esistano
    all_numeric_cols = ["max_week_hours", "min_rest_hours", "min_week_hours", "contracted_hours", "max_overtime_hours"]
    for col in all_numeric_cols:
        if col not in df.columns:
            df[col] = pd.NA  # verrà poi coercizzata dove opportuno

    # 5) coercizza solo le numeriche NON gestite da _normalize_contracted_hours
    for col in ("min_rest_hours", "max_overtime_hours"):
        df[col] = _coerce_integer_series(
            df[col],
            column=col,
            origin="employees.csv",
            ids=df["employee_id"],
        )

    # 6) unicità id
    if df["employee_id"].duplicated().any():
        dups = df.loc[df["employee_id"].duplicated(), "employee_id"].tolist()
        raise ValueError(f"employees.csv: employee_id duplicati: {dups}")

    # 7) normalizza contracted/min/max (coercizione e warning inclusi)
    _normalize_contracted_hours(df)

    # 8) set di skills/ruoli
    df["skills_set"] = df["skills"].apply(_parse_skill_list)
    df["roles_set"] = df["roles"].apply(lambda s: {p.strip() for p in s.split("|") if p.strip()})
    df["primary_role"] = df["roles"].apply(lambda s: s.split("|")[0].strip() if s else "")

    # 9) controlli di dominio
    if (df["max_week_hours"].notna() & (df["max_week_hours"] <= 0)).any():
        raise ValueError("employees.csv: max_week_hours deve essere > 0")

    if (df["min_rest_hours"].notna() & (df["min_rest_hours"] < 0)).any():
        raise ValueError("employees.csv: min_rest_hours non può essere negativo")

    # ok che manchi per i non contrattualizzati: controlla solo se valorizzata
    if (df["max_overtime_hours"].notna() & (df["max_overtime_hours"] < 0)).any():
        raise ValueError("employees.csv: max_overtime_hours non può essere negativo")

    return df


def load_shifts(path: Path) -> pd.DataFrame:
    """Carica e normalizza shifts.csv."""
    df = pd.read_csv(path)
    _ensure_columns(df, SHIFT_BASE_COLS, "shifts.csv")

    # role non deve essere vuoto né NaN
    s = df["role"].astype("string").str.strip()
    missing = s.isna() | (s == "")
    if missing.any():
        righe = (df.index[missing] + 2).tolist()
        raise ValueError(
            f"shifts.csv: valori mancanti o vuoti nella colonna 'role' alle righe: {righe[:10]}"
        )
    df["role"] = s

    # shift_id non vuoto + unicità
    sid = df["shift_id"].astype("string").str.strip()
    missing_sid = sid.isna() | (sid == "")
    if missing_sid.any():
        righe = (df.index[missing_sid] + 2).tolist()
        raise ValueError(f"shifts.csv: 'shift_id' mancante/vuoto alle righe: {righe[:10]}")
    df["shift_id"] = sid

    if df["shift_id"].duplicated().any():
        dups = df.loc[df["shift_id"].duplicated(), "shift_id"].unique().tolist()
        raise ValueError(f"shifts.csv: shift_id duplicati: {dups}")

    # parsing date/orari
    df["day"]       = df["day"].astype(str).str.strip().apply(_parse_date_iso)
    df["start_min"] = df["start"].astype(str).str.strip().apply(parse_hhmm_to_min)
    df["end_min"]   = df["end"].astype(str).str.strip().apply(parse_hhmm_to_min)
    df["start"] = df["start_min"].apply(_minutes_to_time)
    df["end"] = df["end_min"].apply(_minutes_to_time)

    # flag overnight (fine <= inizio)
    df["crosses_midnight"] = df["end_min"] <= df["start_min"]

    # durata in minuti/ore
    df["duration_minutes"] = df.apply(
        lambda row: _compute_duration_minutes(row["start_min"], row["end_min"], row["shift_id"]),
        axis=1,
    )
    df["duration_h"] = df["duration_minutes"] / 60.0

    # datetime completi
    df["start_dt"] = pd.to_datetime(df["day"].astype(str) + " " + df["start"].astype(str))
    df["end_dt"]   = pd.to_datetime(df["day"].astype(str) + " " + df["end"].astype(str))

    # se passa al giorno dopo OPPURE finisce a 24:00, somma 1 giorno all'end_dt
    midnight_mask = df["crosses_midnight"] | (df["end_min"] == 1440)
    df.loc[midnight_mask, "end_dt"] = df.loc[midnight_mask, "end_dt"] + pd.Timedelta(days=1)

    # demand_id opzionale
    if "demand_id" not in df.columns:
        df["demand_id"] = ""
    df["demand_id"] = df["demand_id"].fillna("").astype(str).str.strip()

    mean_demand = df["demand"].mean() if "demand" in df.columns else float("nan")
    summary = (
        f"shifts.csv: caricati {len(df)} turni, ruoli: {sorted(df['role'].unique())}, "
        f"domanda media: {mean_demand:.2f}"
    )
    logger.info(summary)
    return df



def load_availability(path: Path, employees: pd.DataFrame, shifts: pd.DataFrame) -> pd.DataFrame:
    """Carica e normalizza availability.csv."""
    df = pd.read_csv(path, dtype={"employee_id": "string", "shift_id": "string"})
    _ensure_columns(df, REQUIRED_AVAIL_COLS, "availability.csv")

    # ID non vuoti/NaN + strip
    for col in ["employee_id", "shift_id"]:
        s = df[col].str.strip()
        missing = s.isna() | (s == "")
        if missing.any():
            righe = (df.index[missing] + 2).tolist()
            raise ValueError(f"availability.csv: '{col}' mancante/vuoto alle righe: {righe[:10]}")
        df[col] = s  # normalizzato

    # is_available numerico e intero
    df["is_available"] = pd.to_numeric(df["is_available"], errors="raise").astype(int)

    # valori ammessi 0/1
    bad = df.loc[~df["is_available"].isin([0, 1])]
    if not bad.empty:
        righe = (bad.index + 2).tolist()
        raise ValueError(f"availability.csv: is_available deve essere 0 o 1 (righe: {righe[:10]})")

    # chiavi devono esistere
    emp_set = set(employees["employee_id"])
    shift_set = set(shifts["shift_id"])

    bad_emp = df.loc[~df["employee_id"].isin(emp_set), "employee_id"].unique().tolist()
    bad_shift = df.loc[~df["shift_id"].isin(shift_set), "shift_id"].unique().tolist()
    if bad_emp:
        raise ValueError(f"availability.csv: employee_id non presenti: {bad_emp[:10]}")
    if bad_shift:
        raise ValueError(f"availability.csv: shift_id non presenti: {bad_shift[:10]}")

    # dedup sulla coppia
    dup_mask = df.duplicated(subset=["employee_id", "shift_id"], keep=False)
    if dup_mask.any():
        pairs = df.loc[dup_mask, ["employee_id", "shift_id"]].drop_duplicates().to_records(index=False).tolist()
        raise ValueError(f"availability.csv: coppie duplicate (prime): {pairs[:10]}")

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
    """Carica e normalizza overtime_costs.csv."""
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
    """Carica e normalizza preferences.csv."""
    columns = ["employee_id", "shift_id", "score"]
    if not path.exists():
        return pd.DataFrame(columns=columns)

    df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame(columns=columns)

    _ensure_columns(df, columns, path.name)

    df = df[columns].copy()
    df["employee_id"] = df["employee_id"].astype(str).str.strip()
    df["shift_id"] = df["shift_id"].astype(str).str.strip()
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

    df["score"] = df["score"].astype(int)
    df["score"] = df["score"].clip(-2, 2)

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
) -> pd.DataFrame:
    """Carica windows.csv (schema attuale) restituendo colonne normalizzate in minuti."""
    extended_cols = WINDOW_BASE_COLS + ["window_start_min", "window_end_min", "window_minutes"]
    if not path.exists():
        raise FileNotFoundError(f"{path.name} non trovato: il file windows.csv è obbligatorio.")

    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"{path.name} vuoto: nessuna finestra caricata")

    _ensure_columns(df, WINDOW_BASE_COLS, path.name)

    df = df.copy()

    # window_id: evita "nan" stringa e scarta vuoti/NaN
    s = df["window_id"].astype("string").str.strip()
    valid = s.notna() & (s != "")
    df = df[valid].copy()
    if df.empty:
        raise ValueError(f"{path.name}: tutte le finestre prive di identificativo valido sono state scartate")
    df.loc[:, "window_id"] = s[valid]

    # duplicati window_id: warning e tieni l'ultima occorrenza
    if df["window_id"].duplicated().any():
        warnings.warn("windows.csv: window_id duplicati, mantengo l'ultima occorrenza", RuntimeWarning)
        df = df.drop_duplicates(subset="window_id", keep="last")

    df["day"] = df["day"].apply(_parse_date_iso)
    df["window_start_min"] = df["window_start"].apply(parse_hhmm_to_min)
    df["window_end_min"] = df["window_end"].apply(parse_hhmm_to_min)
    df["window_start"] = df["window_start_min"].apply(_minutes_to_time)
    df["window_end"] = df["window_end_min"].apply(_minutes_to_time)

    # role come string senza trasformare NaN in "nan"
    df["role"] = df["role"].astype("string").str.strip()

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
        # dizionari non condivisi tra le righe
        df["skill_requirements"] = [{} for _ in range(len(df))]
        extended_cols = extended_cols + ["skill_requirements"]

    if shifts is not None and not shifts.empty:
        known_roles = set(shifts["role"].astype(str).str.strip())
        unknown_roles = sorted(set(df["role"].astype(str).str.strip()) - known_roles)
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
    df["employee_id"] = df["employee_id"].astype("string").str.strip()
    df["day"] = df["day"].astype("string").str.strip()
    df["reason"] = df.get("reason", "").fillna("").astype("string").str.strip()


    def _parse_optional_time(value):
        if pd.isna(value):
            return None
        s = str(value).strip()
        if s == "":
            return None
        return _parse_time_hhmm(value)  # passa il valore “com’è”


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
                "reason": row["reason"],
            }
        )

    if not records:
        return pd.DataFrame(columns=columns)

    result = pd.DataFrame(records, columns=columns)

    valid_employees = set(employees["employee_id"].astype("string").str.strip())
    result["employee_id"] = result["employee_id"].astype("string").str.strip()
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

    result = result.drop_duplicates(subset=["employee_id", "off_start_dt", "off_end_dt"], keep="last")
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



from types import SimpleNamespace
from pathlib import Path
import pandas as pd
import numpy as np

def load_data_bundle(data_dir: Path, *, config: object | None = None) -> SimpleNamespace:
    """Carica tutti i dataset (v2.0+), inclusi windows.csv (obbligatorio)."""
    data_dir = Path(data_dir)

    # windows.csv è obbligatorio
    windows_path = data_dir / "windows.csv"
    if not windows_path.exists():
        raise FileNotFoundError(
            f"windows.csv is required in {data_dir}. "
            f"If you need a shifts-only mode, remove this check or provide a stub windows.csv."
        )

    # --- Caricamento dataset base ---
    employees    = load_employees(data_dir / "employees.csv")
    shifts       = load_shifts(data_dir / "shifts.csv")
    availability = load_availability(data_dir / "availability.csv", employees, shifts)

    quali_mask   = build_quali_mask(employees, shifts)
    assign_mask  = merge_availability(quali_mask, availability)

    time_off     = load_time_off(data_dir / "time_off.csv", employees)
    preferences  = load_preferences(data_dir / "preferences.csv", employees, shifts)
    # load_windows signature: (path, shifts)
    windows_df   = load_windows(windows_path, shifts)

    # --- Utilità per cast sicuri ---
    def _to_int_safe(x, default=0):
        try:
            if pd.isna(x):
                return default
            return int(x)
        except Exception:
            return default

    # --- Dizionari dai turni ---
    shift_duration_minutes = {
        str(row.shift_id): _to_int_safe(getattr(row, "duration_minutes", 0), 0)
        for row in shifts.itertuples()
    }

    shift_records = {
        str(row.shift_id): {
            "day": getattr(row, "day", None),
            "role": getattr(row, "role", None),
            "start_min": _to_int_safe(getattr(row, "start_min", None)),
            "end_min": _to_int_safe(getattr(row, "end_min", None)),
            "crosses_midnight": bool(getattr(row, "crosses_midnight", False)),
            # Campi opzionali in shifts (fallback a 0/{} se assenti o NaN)
            "demand": _to_int_safe(getattr(row, "demand", 0), 0),
            "required_staff": _to_int_safe(getattr(row, "required_staff", 0), 0),
            "skill_req": (
                dict(getattr(row, "skill_requirements", {}))
                if isinstance(getattr(row, "skill_requirements", {}), dict) else {}
            ),
        }
        for row in shifts.itertuples()
    }

    # --- Dizionari dalle windows ---
    window_records = {
        str(row.window_id): {
            "day": getattr(row, "day", None),
            "role": getattr(row, "role", None),
            "start_min": _to_int_safe(getattr(row, "window_start_min", None)),
            "end_min": _to_int_safe(getattr(row, "window_end_min", None)),
            "window_minutes": _to_int_safe(getattr(row, "window_minutes", None)),
            "window_demand": _to_int_safe(getattr(row, "window_demand", 0), 0),
            "skill_req": (
                dict(getattr(row, "skill_requirements", {}))
                if isinstance(getattr(row, "skill_requirements", {}), dict) else {}
            ),
        }
        for row in windows_df.itertuples()
    }

    # --- Skills dipendenti e mask di eleggibilità ---
    emp_skills = {
        str(row.employee_id): set(row.skills_set)
        for row in employees.itertuples()
    }
    eligible = {
        (str(row.employee_id), str(row.shift_id)): bool(row.can_assign)
        for row in assign_mask.itertuples()
    }

    # --- Bundle finale ---
    return SimpleNamespace(
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


def summarize(employees: pd.DataFrame, shifts: pd.DataFrame, av_mask: pd.DataFrame):
    n_emp = len(employees)
    n_shifts = len(shifts)
    n_pairs = len(av_mask)
    n_assignable = int(pd.to_numeric(av_mask.get("can_assign", 0), errors="coerce").fillna(0).sum())

    days = sorted(shifts["day"].unique())
    roles = sorted(shifts["role"].unique())

    print("=== Riepilogo dati ===")
    print(f"Dipendenti: {n_emp}")
    print(f"Turni: {n_shifts} ({len(days)} giorni, ruoli: {roles})")
    print(f"Coppie possibili employee×shift: {n_pairs}")
    if n_pairs > 0:
        print(f"Coppie assegnabili (qualifica & disponibilità): {n_assignable} ({n_assignable/n_pairs:.1%})")
    else:
        print("Coppie assegnabili (qualifica & disponibilità): 0 (n/a)")
    print()

    blocked = av_mask[av_mask.get("can_assign", 0) == 0]
    print("Esempi di blocchi non assegnabili (prime 10):")
    if blocked.empty:
        print("(nessuno)")
    else:
        print(blocked.head(10).to_string(index=False))


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
