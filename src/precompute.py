from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from pathlib import Path
from typing import Dict, List, Tuple
from types import SimpleNamespace

from dateutil import tz
from dateutil import parser as dtparser
import pandas as pd


logger = logging.getLogger(__name__)


@dataclass
class AdaptiveSlotData:
    slots_by_day_role: Dict[tuple[date, str], List[str]]
    slot_minutes: Dict[str, int]
    slot_bounds: Dict[str, tuple[int, int]]
    segments_of_s: Dict[str, List[str]]
    segment_owner: Dict[str, str]
    segment_bounds: Dict[str, tuple[date, str, int, int]]
    cover_segment: Dict[tuple[str, str], int]

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
        return datetime.combine(day_obj, hhmm)

    start_dt = df.apply(lambda r: _mk_dt(r["day"], r["start"]), axis=1)
    end_dt_raw = df.apply(lambda r: _mk_dt(r["day"], r["end"]), axis=1)

    # se l'end non è strettamente dopo lo start, gestisci i casi
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
    Restituisce un DataFrame con colonne:
      - shift_id_from, shift_id_to, gap_h
    dove gap_h = ore tra fine di 'from' e inizio di 'to', può essere negativo se si sovrappongono).
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

def build_adaptive_slots(data, config) -> AdaptiveSlotData:
    """Generate adaptive time slots per (day, role) and segment coverage."""

    if hasattr(data, "shifts_df"):
        shifts_df = data.shifts_df
    else:
        shifts_df = data

    if shifts_df is None or shifts_df.empty:
        return AdaptiveSlotData({}, {}, {}, {}, {}, {}, {})

    windows_cfg = getattr(config, "windows", None)
    if windows_cfg is None:
        windows_cfg = SimpleNamespace(
            midnight_policy="split",
            warn_slots_threshold=500,
            hard_slots_threshold=2000,
        )

    midnight_policy = getattr(windows_cfg, "midnight_policy", "split").lower()
    warn_threshold = getattr(windows_cfg, "warn_slots_threshold", None)
    hard_threshold = getattr(windows_cfg, "hard_slots_threshold", None)

    segments_of_s: Dict[str, List[str]] = {}
    segment_owner: Dict[str, str] = {}
    segment_bounds: Dict[str, tuple[date, str, int, int]] = {}
    segments_by_day_role: Dict[tuple[date, str], List[str]] = {}

    def add_segment(s_id: str, seg_day: date, role: str, start: int, end: int) -> None:
        if end <= start:
            return
        seg_list = segments_of_s.setdefault(s_id, [])
        seg_id = f"{s_id}__seg{len(seg_list)}"
        seg_list.append(seg_id)
        segment_owner[seg_id] = s_id
        segment_bounds[seg_id] = (seg_day, role, start, end)
        segments_by_day_role.setdefault((seg_day, role), []).append(seg_id)

    for row in shifts_df.itertuples():
        shift_id = str(row.shift_id)
        role = str(row.role)
        base_day = row.day
        start_min = int(row.start_min)
        end_min = int(row.end_min)
        crosses_midnight = bool(getattr(row, "crosses_midnight", end_min <= start_min))

        if not crosses_midnight:
            add_segment(shift_id, base_day, role, start_min, end_min)
            continue

        if midnight_policy == "exclude":
            add_segment(shift_id, base_day, role, start_min, 1440)
        elif midnight_policy == "split":
            if start_min < 1440:
                add_segment(shift_id, base_day, role, start_min, 1440)
            next_day = base_day + timedelta(days=1)
            if end_min > 0:
                add_segment(shift_id, next_day, role, 0, end_min)
        else:
            raise ValueError(f"Midnight policy sconosciuta: {midnight_policy}")

    slots_by_day_role: Dict[tuple[date, str], List[str]] = {}
    slot_minutes: Dict[str, int] = {}
    slot_bounds: Dict[str, tuple[int, int]] = {}
    cover_segment: Dict[tuple[str, str], int] = {}

    for key in sorted(segments_by_day_role.keys()):
        seg_ids = segments_by_day_role[key]
        breakpoints: List[int] = []
        for seg_id in seg_ids:
            _, _, start, end = segment_bounds[seg_id]
            breakpoints.extend([start, end])
        breakpoints = sorted(set(breakpoints))

        slots: List[str] = []
        for start, end in zip(breakpoints, breakpoints[1:]):
            if end <= start:
                continue
            slot_id = f"{key[0].isoformat()}__{key[1]}__{start:04d}_{end:04d}"
            slots.append(slot_id)
            slot_minutes[slot_id] = end - start
            slot_bounds[slot_id] = (start, end)
        slots_by_day_role[key] = slots

        count = len(slots)
        if count == 0:
            continue
        if hard_threshold is not None and count > hard_threshold:
            msg = (
                f"Numero slot {count} per day={key[0]} role={key[1]} supera hard_slots_threshold "
                f"{hard_threshold}"
            )
            logger.error(msg)
            raise RuntimeError(msg)
        if warn_threshold is not None and count > warn_threshold:
            logger.warning(
                "Numero slot %s per day=%s role=%s supera warn_slots_threshold %s",
                count,
                key[0],
                key[1],
                warn_threshold,
            )

        for seg_id in seg_ids:
            _, _, seg_start, seg_end = segment_bounds[seg_id]
            for slot_id in slots:
                slot_start, slot_end = slot_bounds[slot_id]
                cover_segment[(seg_id, slot_id)] = int(seg_start <= slot_start and seg_end >= slot_end)

    return AdaptiveSlotData(
        slots_by_day_role=slots_by_day_role,
        slot_minutes=slot_minutes,
        slot_bounds=slot_bounds,
        segments_of_s=segments_of_s,
        segment_owner=segment_owner,
        segment_bounds=segment_bounds,
        cover_segment=cover_segment,
    )


def _compute_segments_by_day_role(segment_bounds: Dict[str, tuple[date, str, int, int]]) -> Dict[tuple[date, str], List[str]]:
    result: Dict[tuple[date, str], List[str]] = {}
    for seg_id, (seg_day, role, start, _end) in segment_bounds.items():
        result.setdefault((seg_day, role), []).append((start, seg_id))
    for key, items in result.items():
        items.sort(key=lambda item: (item[0], item[1]))
        result[key] = [seg_id for _, seg_id in items]
    return result


def _compute_slot_signatures(
    data: AdaptiveSlotData,
    segments_by_day_role: Dict[tuple[date, str], List[str]] | None = None,
) -> Dict[str, frozenset[str]]:
    if segments_by_day_role is None:
        segments_by_day_role = _compute_segments_by_day_role(data.segment_bounds)

    signatures: Dict[str, frozenset[str]] = {}
    for key, slots in data.slots_by_day_role.items():
        segments = segments_by_day_role.get(key, [])
        for slot in slots:
            signature = frozenset(
                seg for seg in segments if data.cover_segment.get((seg, slot), 0) == 1
            )
            signatures[slot] = signature
    return signatures


def _merge_slots_by_signature(
    data: AdaptiveSlotData,
    segments_by_day_role: Dict[tuple[date, str], List[str]],
) -> tuple[AdaptiveSlotData, Dict[str, frozenset[str]]]:
    new_slots_by_day_role: Dict[tuple[date, str], List[str]] = {}
    new_slot_bounds: Dict[str, tuple[int, int]] = {}
    new_slot_minutes: Dict[str, int] = {}
    new_cover_segment: Dict[tuple[str, str], int] = {}
    slot_signature: Dict[str, frozenset[str]] = {}

    total_before = sum(len(slots) for slots in data.slots_by_day_role.values())
    total_after = 0

    for key, slots in data.slots_by_day_role.items():
        if not slots:
            new_slots_by_day_role[key] = []
            continue

        sorted_slots = sorted(slots, key=lambda slot: data.slot_bounds[slot][0])
        segments = segments_by_day_role.get(key, [])
        run_start = None
        run_end = None
        run_signature: frozenset[str] | None = None
        run_slots: List[str] = []
        new_slot_list: List[str] = []

        def finalize_run():
            nonlocal run_start, run_end, run_signature, run_slots, new_slot_list
            if not run_slots or run_signature is None:
                return
            new_id = f"{key[0].isoformat()}__{key[1]}__{run_start:04d}_{run_end:04d}"
            new_slot_list.append(new_id)
            new_slot_bounds[new_id] = (run_start, run_end)
            new_slot_minutes[new_id] = run_end - run_start
            slot_signature[new_id] = run_signature
            for seg in segments:
                new_cover_segment[(seg, new_id)] = int(seg in run_signature)
            run_start = None
            run_end = None
            run_signature = None
            run_slots = []

        for slot in sorted_slots:
            slot_start, slot_end = data.slot_bounds[slot]
            signature = frozenset(
                seg for seg in segments if data.cover_segment.get((seg, slot), 0) == 1
            )
            if run_signature is None:
                run_start = slot_start
                run_end = slot_end
                run_signature = signature
                run_slots = [slot]
                continue
            if signature == run_signature and slot_start == run_end:
                run_end = slot_end
                run_slots.append(slot)
            else:
                finalize_run()
                run_start = slot_start
                run_end = slot_end
                run_signature = signature
                run_slots = [slot]
        finalize_run()

        new_slots_by_day_role[key] = new_slot_list
        total_after += len(new_slot_list)

    if total_after < total_before:
        logger.info("Merge slot per firma: %s -> %s slot", total_before, total_after)

    merged_data = AdaptiveSlotData(
        slots_by_day_role=new_slots_by_day_role,
        slot_minutes=new_slot_minutes,
        slot_bounds=new_slot_bounds,
        segments_of_s=data.segments_of_s,
        segment_owner=data.segment_owner,
        segment_bounds=data.segment_bounds,
        cover_segment=new_cover_segment,
    )
    return merged_data, slot_signature


def map_windows_to_slots(
    adaptive_data: AdaptiveSlotData,
    windows_df: pd.DataFrame | None,
    *,
    strict: bool = True,
    merge_signatures: bool = True,
) -> tuple[AdaptiveSlotData, Dict[str, List[str]], Dict[str, frozenset[str]]]:
    """Map windows to slots and validate potential coverage."""

    if adaptive_data is None:
        raise ValueError("adaptive_data non può essere None")

    data = adaptive_data
    segments_by_day_role = _compute_segments_by_day_role(data.segment_bounds)

    if merge_signatures:
        data, slot_signature = _merge_slots_by_signature(data, segments_by_day_role)
        segments_by_day_role = _compute_segments_by_day_role(data.segment_bounds)
    else:
        slot_signature = _compute_slot_signatures(data, segments_by_day_role)

    if windows_df is None or windows_df.empty:
        logger.info("Nessuna finestra da mappare (0)")
        return data, {}, slot_signature

    slots_in_window: Dict[str, List[str]] = {}
    total_refs = 0

    for row in windows_df.itertuples():
        window_id = str(row.window_id)
        key = (row.day, row.role)
        if key not in data.slots_by_day_role:
            raise RuntimeError(
                f"Finestra {window_id}: nessuno slot generato per day={row.day} role={row.role}"
            )
        window_start = int(row.window_start_min)
        window_end = int(row.window_end_min)
        if window_end <= window_start:
            raise ValueError(f"Finestra {window_id}: intervallo non valido")

        available_slots = data.slots_by_day_role[key]
        selected: List[str] = []
        for slot_id in available_slots:
            slot_start, slot_end = data.slot_bounds[slot_id]
            if slot_start >= window_start and slot_end <= window_end:
                selected.append(slot_id)

        if not selected:
            raise RuntimeError(
                f"Finestra {window_id}: nessuno slot compatibile dentro l'intervallo ({window_start}-{window_end})"
            )

        if strict:
            for slot_id in selected:
                signature = slot_signature.get(slot_id, frozenset())
                if not signature:
                    slot_start, slot_end = data.slot_bounds[slot_id]
                    raise RuntimeError(
                        "Finestra %s: slot %s (%s-%s) senza copertura potenziale per day=%s role=%s"
                        % (
                            window_id,
                            slot_id,
                            slot_start,
                            slot_end,
                            row.day,
                            row.role,
                        )
                    )

        slots_in_window[window_id] = selected
        total_refs += len(selected)

    logger.info(
        "Mappate %s finestre su %s slot (riferimenti=%s)",
        len(slots_in_window),
        len(data.slot_bounds),
        total_refs,
    )
    return data, slots_in_window, slot_signature
