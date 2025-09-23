from pathlib import Path
from datetime import datetime, timedelta, time
from dateutil import tz
from dateutil import parser as dtparser
import pandas as pd

# --- 1) Normalizza start/end in datetime coerenti (gestisce end a mezzanotte o passaggi giorno) ---
def normalize_shift_times(shifts: pd.DataFrame) -> pd.DataFrame:
    """
    Aggiunge colonne:
      - start_dt, end_dt: datetime (naive) calcolati da day+start/end
      - duration_h: durata in ore (float)
    Regola: se end <= start, l'end si intende al giorno successivo (turno che "attraversa" le 24:00).
    """
    df = shifts.copy()

    def _mk_dt(day_obj, hhmm):
        # day_obj ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¨ un datetime.date; hhmm ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¨ un datetime.time (giÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â  parsati dal loader)
        return datetime.combine(day_obj, hhmm)

    start_dt = df.apply(lambda r: _mk_dt(r["day"], r["start"]), axis=1)
    end_dt_raw = df.apply(lambda r: _mk_dt(r["day"], r["end"]), axis=1)

    # se l'end non ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¨ strettamente dopo lo start, gestisci i casi
    end_dt = []
    for s, e in zip(start_dt, end_dt_raw):
        if e < s:
            end_dt.append(e + timedelta(days=1))
        elif e == s:
            if e.time() == time(0, 0):
                end_dt.append(e + timedelta(days=1))
            else:
                raise ValueError("Fine turno non puÃƒÆ’Ã‚Â² coincidere con l'inizio a meno che non sia mezzanotte.")
        else:
            end_dt.append(e)

    df["start_dt"] = start_dt
    df["end_dt"] = end_dt

    # durata in ore
    df["duration_h"] = (df["end_dt"] - df["start_dt"]).dt.total_seconds() / 3600.0

    return df


# --- 2) Tabella gap tra TUTTE le coppie di turni (s, s') ---
def compute_gap_table(shifts_norm: pd.DataFrame) -> pd.DataFrame:
    """
    Restituisce un DataFrame lungo con colonne:
      - shift_id_from, shift_id_to, gap_h
    dove gap_h = ore tra fine di 'from' e inizio di 'to' (puÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â² essere negativo se si sovrappongono).
    """
    a = shifts_norm[["shift_id", "start_dt", "end_dt"]].copy()
    b = shifts_norm[["shift_id", "start_dt"]].copy()
    a.columns = ["shift_id_from", "start_dt_from", "end_dt_from"]
    b.columns = ["shift_id_to", "start_dt_to"]

    a["key"] = 1
    b["key"] = 1
    cart = a.merge(b, on="key").drop(columns="key")

    cart = cart[cart["start_dt_from"] < cart["start_dt_to"]]

    cart["gap_h"] = (cart["start_dt_to"] - cart["end_dt_from"]).dt.total_seconds() / 3600.0
    return cart[["shift_id_from", "shift_id_to", "gap_h"]]


# --- 3) A partire dalla gap table, estrae solo le coppie di turni che violano un riposo minimo globale.
def conflict_pairs_for_rest(shifts_norm: pd.DataFrame, min_rest_hours: float) -> pd.DataFrame:
    """
    Restituisce le coppie (from,to) che violano un riposo minimo GLOBALE passato in input:
      gap_h < min_rest_hours
    Nota: se vuoi usare min_rest_hours *per dipendente*, non filtrare qui; usa la gap_table nel modello.
    """
    gap = compute_gap_table(shifts_norm)
    conf = gap[gap["gap_h"] < float(min_rest_hours)].copy()

    if conf.empty:
        return conf.reset_index(drop=True)

    conf = conf.drop_duplicates(subset=["shift_id_from", "shift_id_to"])
    return conf.reset_index(drop=True)


# --- 4) Utility di riepilogo per debug ---
def summarize_shifts(shifts_norm: pd.DataFrame, gap_table: pd.DataFrame, sample: int = 10):
    print("=== Shifts normalizzati ===")
    cols = ["shift_id", "day", "start_dt", "end_dt", "duration_h", "role", "required_staff"]
    print(shifts_norm[cols].to_string(index=False, max_colwidth=24))
    print()
    print("=== Esempi di gap (prime righe) ===")
    print(gap_table.head(sample).to_string(index=False))
