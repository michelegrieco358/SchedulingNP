# Shift Scheduling CP-SAT

Pianificatore turni basato su **OR-Tools CP-SAT**.  
Input: dipendenti, turni, disponibilità, costi di straordinario.  
Output: assegnazioni turno→dipendente che rispettano vincoli operativi, **minimizzando prima gli straordinari** e poi bilanciando il carico (fairness).

> **Stato attuale:** orizzonte di **una settimana**. Estensioni multi-periodo nella roadmap.

---

## Indice

- [Caratteristiche](#caratteristiche)
- [Funzione obiettivo (lessicografica con pesi)](#funzione-obiettivo-lessicografica-con-pesi)
- [Installazione](#installazione)
- [Dati di input (CSV)](#dati-di-input-csv)
- [Esecuzione](#esecuzione)
- [Come funziona](#come-funziona)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [Struttura del progetto](#struttura-del-progetto)
- [Licenza](#licenza)

---

## Caratteristiche

- **Copertura turni:** ogni turno copre esattamente `required_staff`.
- **Riposo minimo:** blocco di coppie di turni troppo vicini (**deduplicate**, nessun self-pair, direzione temporale corretta).  
  Le coppie in conflitto sono pre-calcolate e pulite in `precompute.py`.
- **Un turno al giorno:** max 1 turno per dipendente per `start_dt.date()`.
- **Turni notte:** nessuna notte consecutiva; max **3** notti per settimana ISO (calcolata da `start_dt`).
- **Ore massime & straordinario:** minuti assegnati ≤ `max_week_hours` + overtime (limitato per dipendente).
- **Fairness (L1):** riduce lo scostamento in minuti rispetto alla **quota media** tra i dipendenti attivi.
- **Pre-processing robusto:** normalizzazione orari (cross‑midnight), conflitti di riposo deduplicati, eleggibilità costruita **via join per ruolo** (no cartesiano).
- **Nota:** lo **shortfall di copertura** non è ancora attivo (sarà aggiunto successivamente).

---

## Funzione obiettivo (lessicografica con pesi)

La priorità è **lessicografica** e si realizza con **pesi gerarchici** (grandi ma finiti). In pratica il modello si comporta come se avesse più obiettivi ordinati per importanza.

1. **Straordinari (priorità 1):** minimizzare il **costo di overtime**.
2. **Fairness (priorità 2):** a parità (o quasi) di overtime, minimizzare la **deviazione L1** rispetto alla quota media di minuti assegnati per dipendente attivo.

Obiettivo implementato come **somma pesata**:

```
min   λ_overtime * C_overtime  +  λ_fairness * Σ_e (over_e + under_e)
con   λ_overtime  >>  λ_fairness
```

dove:
- `C_overtime` è il costo totale di straordinario (minuti di overtime × costo orario per ruolo);
- `over_e, under_e ≥ 0` sono i minuti di scostamento dal target medio per il dipendente `e` (deviazione L1).

> Quando introdurrai lo **shortfall** (slack di copertura), la gerarchia consigliata diventerà:
> `λ_shortfall  >>  λ_overtime  >>  λ_fairness`  
> (oppure una vera ottimizzazione in due fasi: prima minimizzare lo shortfall, poi – a valore fissato – ottimizzare overtime + fairness).

---

## Installazione

Requisiti: Python **3.10+**

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -U ortools pandas python-dateutil
```

---

## Dati di input (CSV)

Metti i CSV in `data/` (o passa `--data-dir`).

### `employees.csv`

| colonna             | tipo  | descrizione                                       |
|---------------------|-------|---------------------------------------------------|
| employee_id         | str   | id univoco (es. `E1`)                             |
| name                | str   | nome                                              |
| roles               | str   | ruoli separati da `|` (es. `nurse|triage`)       |
| max_week_hours      | float | ore massime settimanali                           |
| min_rest_hours      | float | riposo minimo personale (h)                       |
| max_overtime_hours  | float | straordinario massimo consentito (h)              |

### `shifts.csv`

| colonna        | tipo | descrizione                                                                 |
|----------------|------|-----------------------------------------------------------------------------|
| shift_id       | str  | id turno (es. `S12`)                                                        |
| day            | date | `YYYY-MM-DD`                                                                |
| start          | time | `HH:MM`                                                                     |
| end            | time | `HH:MM` — se `end <= start` il turno è **cross-midnight**                   |
| role           | str  | ruolo richiesto                                                             |
| required_staff | int  | numero di persone richieste (≥1)                                            |

> Gli orari vengono normalizzati in `start_dt` / `end_dt`. Il precompute gestisce i casi *cross‑midnight* e deduce anche la **durata**.

### `availability.csv`

| colonna       | tipo | descrizione                               |
|---------------|------|-------------------------------------------|
| employee_id   | str  | deve esistere in `employees.csv`          |
| shift_id      | str  | deve esistere in `shifts.csv`             |
| is_available  | 0/1  | 1 se disponibile, 0 altrimenti            |

> **Scelta attuale:** le coppie **mancanti** sono trattate come **disponibili** (default = 1). È modificabile in futuro.

### `overtime_costs.csv`

| colonna                  | tipo  | descrizione                      |
|--------------------------|-------|----------------------------------|
| role                     | str   | ruolo                            |
| overtime_cost_per_hour   | float | costo orario di straordinario    |

---

## Esecuzione

### 1) Validazione & riepilogo

```bash
python loader.py --data-dir data
```

Stampa riepiloghi su:
- n. dipendenti / turni / ruoli / giorni;
- coppie eleggibili `(employee, shift)` (costruite per ruolo);
- eventuali turni senza candidati.

### 2) Costruzione e solve del modello

```bash
python model_cp.py   --data-dir data   --max-seconds 60   --log-search   --global-ot-cap-hours 999   --output outputs/assignments.csv
```

Opzioni principali:
- `--max-seconds` — time limit del solver CP-SAT;
- `--log-search` — log verbose della ricerca;
- `--global-ot-cap-hours` — (opzionale) tetto globale di straordinari (ore);
- `--output` — percorso CSV per le assegnazioni.

**Output**: `assignments.csv` con almeno `shift_id, employee_id` e metadati del turno (`day, start_dt, end_dt, role`).

---

## Come funziona

1. **Loader**
   - Legge e valida gli schemi CSV.
   - Converte i ruoli in insiemi.
   - Costruisce la maschera di eleggibilità `can_assign` **via join per ruolo** + disponibilità (no prodotto cartesiano).

2. **Precompute**
   - Normalizza orari (`start_dt`, `end_dt`), gestendo **cross‑midnight**.
   - Calcola la **gap table** e costruisce le **coppie di riposo**: mantiene solo la **direzione temporale corretta**, **rimuove self‑pair e duplicati** `(A,B)/(B,A)`, e conserva gli **overlap** (gap negativo).

3. **Model (CP‑SAT)**
   - Variabili binarie `x[e,s]` per le sole coppie eleggibili.
   - **Copertura**: `Σ_e x[e,s] == required_staff_s` per ogni turno `s`.
   - **Riposo**: per ogni conflitto `(s, s')`, `x[e,s] + x[e,s'] ≤ 1` per ogni dipendente `e`.
   - **Un turno al giorno**: `Σ_{s ∈ day(d)} x[e,s] ≤ 1`.
   - **Notti**: nessuna consecutiva; `#notti_week(e) ≤ 3`.
   - **Ore massime & overtime**: vincoli per dipendente su minuti assegnati e straordinari.
   - **Fairness L1** rispetto alla quota media (solo dipendenti attivi):  
     `N * assigned_minutes(e) - TOT == N * (over_e - under_e)` e **min** `Σ_e (over_e + under_e)`.
   - **Obiettivo lessicografico con pesi**: `λ_overtime >> λ_fairness`.

---

## Troubleshooting

- **INFEASIBLE**: senza shortfall, può accadere se mancano candidati per alcuni turni.  
  → Usa l’output di `loader.py` per individuare i buchi (turni senza candidati) o riduci vincoli.
- **Solve lento**: assicurati che i conflitti siano deduplicati (già fatto) e che l’eleggibilità sia via join per ruolo (già fatto). Riduci orizzonte o imposta `--max-seconds`.

---

## Roadmap

1. **Shortfall di copertura** (slack per turno) con peso **dominante** (o 2‑fase).
2. **Preferenze** dipendente‑turno (pesi positivi/negativi).
3. **Multi‑periodo** (settimane/mesi):
   - vincoli per **ISO‑week** su ore, notti, fairness;
   - riposi che attraversano confini settimana;
   - budgeting overtime per settimana e/o globale;
   - approccio **rolling horizon**.

---

## Struttura del progetto

```
.
├─ loader.py        # Lettura/validazione CSV, eleggibilità via join per ruolo, riepiloghi
├─ precompute.py    # Normalizzazione orari, gap table, conflitti di riposo (deduplicati, direzionali)
├─ model_cp.py      # Modello CP-SAT, vincoli, obiettivo lessicografico con pesi, export
└─ data/
   ├─ employees.csv
   ├─ shifts.csv
   ├─ availability.csv
   └─ overtime_costs.csv
```

---

## Licenza

Scegli e aggiungi la licenza del progetto (es. MIT).
