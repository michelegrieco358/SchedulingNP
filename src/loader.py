import argparse
import sys
from pathlib import Path
from datetime import datetime, time, timedelta
from dateutil import parser as dtparser

import pandas as pd
import warnings


REQUIRED_EMP_COLS = ["employee_id", "name", "roles", "max_week_hours", "min_rest_hours", "max_overtime_hours"]
REQUIRED_SHIFT_COLS = ["shift_id", "day", "start", "end", "role", "required_staff"]
REQUIRED_AVAIL_COLS = ["employee_id", "shift_id", "is_available"]


def _ensure_columns(df: pd.DataFrame, required_cols: list, name: str):
    """ check che siano presenti le colonne necessarie nei file csv """
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"{name}: colonne mancanti: {missing}")


def _parse_time_hhmm(s: str) -> time:
    """Parsa 'HH:MM' in datetime.time. Raise error se formato non valido."""
    try:
        return dtparser.parse(s).time()
    except Exception as e:
        raise ValueError(f"Orario non valido '{s}' (atteso HH:MM)") from e


def _parse_date_iso(s: str) -> datetime.date:
    """Parsa stringa della data in datetime.time. Raise error se formato non valido."""
    try:
        return dtparser.isoparse(s).date()
    except Exception as e:
        raise ValueError(f"Data non valida '{s}' (atteso YYYY-MM-DD)") from e


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

    # unicità  id
    if df["employee_id"].duplicated().any():
        dups = df[df["employee_id"].duplicated()]["employee_id"].tolist()
        raise ValueError(f"employees.csv: employee_id duplicati: {dups}")

    # crea colonna di variabili tipo set che indicano i ruoli di ogni dipendente
    df["roles_set"] = df["roles"].apply(lambda s: set([p.strip() for p in s.split("|") if p.strip() != ""]))
    df["primary_role"] = df["roles"].apply(lambda s: s.split("|")[0].strip() if s else "")

    # controlli rapidi
    if (df["max_week_hours"] <= 0).any():
        raise ValueError("employees.csv: max_week_hours deve essere > 0")
    if (df["min_rest_hours"] < 0).any():
        raise ValueError("employees.csv: min_rest_hours non puÃƒÂ² essere negativo")
    if (df["max_overtime_hours"] < 0).any():
        raise ValueError("employees.csv: max_overtime_hours non puÃƒÂ² essere negativo")

    return df


def load_shifts(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    _ensure_columns(df, REQUIRED_SHIFT_COLS, "shifts.csv")

    # tipi/parse
    df["shift_id"] = df["shift_id"].astype(str)
    df["day"] = df["day"].apply(_parse_date_iso)
    df["start"] = df["start"].apply(_parse_time_hhmm)
    df["end"] = df["end"].apply(_parse_time_hhmm)
    df["role"] = df["role"].astype(str)
    df["required_staff"] = pd.to_numeric(df["required_staff"], errors="raise").astype(int)

    if df["shift_id"].duplicated().any():
        dups = df[df["shift_id"].duplicated()]["shift_id"].tolist()
        raise ValueError(f"shifts.csv: shift_id duplicati: {dups}")

    if (df["required_staff"] <= 0).any():
        raise ValueError("shifts.csv: required_staff deve essere >= 1")

    # controllo orari (accettiamo end=00:00 come 'fine a mezzanotte')
    # Non calcoliamo ancora la durata: sarà fatto in precompute (step successivo).
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
