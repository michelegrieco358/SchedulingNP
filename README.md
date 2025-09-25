# Shift Scheduling CP-SAT

Pianificatore turni basato su **OR-Tools CP-SAT**.

- **Input**: dipendenti, turni, disponibilità, costi di straordinario, preferenze soft e time-off hard.
- **Output**: assegnazioni turno-dipendente che rispettano i vincoli operativi, minimizzano gli shortfall e, a cascata, ottimizzano straordinari, preferenze e fairness.

> **Stato attuale:** modello su orizzonte settimanale; l'estensione multi-periodo resta in roadmap.

---

## Indice

- [Caratteristiche](#caratteristiche)
- [Funzione obiettivo (persona-ora)](#funzione-obiettivo-persona-ora)
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

- **Copertura turni hard**: ogni turno richiede esattamente `required_staff`; se i candidati non bastano interviene lo shortfall (penalizzato fortemente).
- **Domanda aggregata per finestra**: `demand_windows.csv` impone la copertura minima per fasce orarie/ruolo con slack pesantemente penalizzato.
- **Disponibilita hard**: `availability.csv` definisce le coppie consentite (missing e disponibile di default).
- **Time-off hard**: `time_off.csv` blocca qualsiasi sovrapposizione turno/assenza (full day o parziale, con supporto overnight).
- **Riposi minimi e regole notte**: vieta notti consecutive e limita a tre le notti per settimana ISO.
- **Un turno per giorno/dipendente**.
- **Ore contrattuali e straordinari**: limiti settimanali per dipendente + overtime opzionale con costo per ruolo.
- **Fairness (L1)**: riequilibra i minuti lavorati sul target medio dei dipendenti attivi, al netto degli shortfall.
- **Preferenze soft**: bonus/malus per turno tramite `preferences.csv` (score -2..+2) con peso configurabile.
- **Pre-processing robusto**: normalizzazione orari cross-midnight, conflitti di riposo e costruzione dell'eligibility via join sui ruoli.

---

## Funzione obiettivo (persona-ora)

Tutti i termini dell'obiettivo sono normalizzati in persona-ore: ogni slack viene moltiplicato per la durata del turno/finestra in ore, mentre straordinari, fairness e preferenze sono anch'essi misurati in ore. In questo modo i contributi restano confrontabili senza ricorrere a pesi astronomici.

Pesi di default (costo per 1 persona-ora):

- 2.0 domanda aggregata di finestra (`unmet_window`)
- 1.0 copertura del turno (`unmet_demand`)
- 0.8 skill richieste dal turno (`unmet_skill`)
- 0.6 minimi di turno soft (`unmet_shift`)
- 0.3 straordinario (`overtime`)
- 0.05 fairness sul carico (`fairness`)
- 0.05 preferenze (`preferences`)

Il solver minimizza:

```
min  2.0 * sum_W  H_W * short_window_W
    + 1.0 * sum_s  H_s * shortfall_s
    + 0.8 * sum_{s,k} H_s * short_skill_{s,k}
    + 0.6 * sum_s  H_s * short_shift_soft_s
    + 0.3 * sum_e  overtime_hours_e
    + 0.05 * fairness_hours
    + 0.01 * sum_{(e,s)} (-score_es) * H_avg
```

con H_W/H_s in ore e H_avg la durata media dei turni. Internamente il modello usa i minuti per mantenere coefficienti interi, ma i pesi sono equivalenti ai valori sopra indicati.

---

---

## Installazione---

## Installazione

Requisiti: Python **3.10+**

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -U ortools pandas python-dateutil
```

---

## Dati di input (CSV)

Mettere i CSV in `data/` (o passa `--data-dir`).

### `employees.csv`

| colonna             | tipo  | descrizione                                       |
|---------------------|-------|--------------------------------------------------|
| employee_id         | str   | id univoco (es. `E1`)                            |
| name                | str   | nome                                             |
| roles               | str   | ruoli separati da `|` (es. `nurse|triage`)      |
| max_week_hours      | float | ore massime settimanali                          |
| min_rest_hours      | float | riposo minimo personale (h)                      |
| max_overtime_hours  | float | straordinario massimo consentito (h)             |
| skills             | str   | elenco opzionale di skill, separato da virgole (es. `muletto,primo_soccorso`) |

### `shifts.csv`

| colonna        | tipo | descrizione                                                                 |
|----------------|------|-----------------------------------------------------------------------------|
| shift_id       | str  | id turno (es. `S12`)                                                        |
| day            | date | `YYYY-MM-DD`                                                                |
| start          | time | `HH:MM`                                                                     |
| end            | time | `HH:MM` (se `end <= start` indica turno cross-midnight)                     |
| role           | str  | ruolo richiesto                                                             |
| required_staff | int  | numero di persone richieste                                                 |
| demand         | int  | opzionale: domanda minima per il turno (soft, default = 0)                  |
| demand_id      | str  | opzionale: identifica la finestra in `demand_windows.csv`                   |
| skill_requirements | str  | opzionale: requisiti di skill (`{{"muletto":1}}` oppure `muletto=1,primo=1`) |

> Il pre-processing genera `start_dt`, `end_dt` e `duration_h`, gestendo i casi cross-midnight.
### `availability.csv`

| colonna      | tipo | descrizione                               |
|--------------|------|-------------------------------------------|
| employee_id  | str  | deve esistere in `employees.csv`          |
| shift_id     | str  | deve esistere in `shifts.csv`             |
| is_available | 0/1  | 1 se disponibile, 0 altrimenti            |

> Le coppie mancanti sono considerate disponibili (default = 1).

### `demand_windows.csv`

| colonna       | tipo | descrizione                                             |
|---------------|------|---------------------------------------------------------|
| demand_id     | str  | chiave della finestra (richiamata dai turni)           |
| window_start  | time | inizio finestra (`HH:MM`)                               |
| window_end    | time | fine finestra (`HH:MM`)                                |
| role          | str  | ruolo richiesto nella finestra                         |
| window_demand | int  | domanda minima aggregata per la finestra               |

> Una finestra puo essere associata a piu turni tramite `demand_id`; il solver somma le assegnazioni e applica slack penalizzato se la domanda non viene coperta.
### `overtime_costs.csv`

| colonna                | tipo  | descrizione                      |
|------------------------|-------|----------------------------------|
| role                   | str   | ruolo                            |
| overtime_cost_per_hour | float | costo orario di straordinario    |

### `preferences.csv` (opzionale)

| colonna      | tipo | descrizione                                            |
|--------------|------|--------------------------------------------------------|
| employee_id  | str  | id dipendente                                          |
| shift_id     | str  | id turno                                               |
| score        | int  | valore in `{-2, -1, 0, 1, 2}` (duplicati ? ultima riga) |

> Score positivi incentivano l�assegnazione, negativi la penalizzano. Le coppie assenti valgono 0.

### `time_off.csv` (opzionale)

| colonna     | tipo    | descrizione                                                           |
|-------------|---------|-----------------------------------------------------------------------|
| employee_id | str     | id dipendente                                                         |
| day         | date    | giorno dell�assenza (`YYYY-MM-DD`)                                    |
| start_time  | time    | opzionale (`HH:MM`). Vuoto ? inizio 00:00                             |
| end_time    | time    | opzionale (`HH:MM`). Vuoto ? fine 24:00 del giorno successivo         |
| reason      | str     | testo libero (informativo)                                            |

> La colonna `skill_requirements` puo utilizzare JSON (`{"muletto":1}`) oppure la forma compatta `muletto=1,primo=1`; i valori devono essere interi >= 0 e la somma dovrebbe rimanere entro `required_staff`.
> Gli intervalli che attraversano la mezzanotte sono gestiti sommando un giorno a `end_time`. Qualsiasi sovrapposizione con un turno blocca la coppia (hard constraint).

---

## Configurazione

- Pesi di default (persona-ora): unmet_window=2.0, unmet_demand=1.0, unmet_skill=0.8, unmet_shift=0.6, overtime=0.3, fairness=0.05, preferences=0.05.
- Il file config.yaml contiene i valori di default per ore, riposi, pesi dell'obiettivo, seed e logging.
- Sezione `skills`: `enable_slack` attiva le variabili di shortfall per skill (true di default). `penalties.unmet_skill` imposta il peso, leggermente inferiore a `penalties.unmet_demand`.
- Puoi passare un file alternativo con python model_cp.py --config custom.yaml. Sono supportati YAML e JSON.
- Le chiavi mancanti usano i default dello schema; gli override da CLI restano prioritari.
- Errori di validazione (tipi errati, priorita sconosciute, range incoerenti) producono messaggi espliciti.
- Il riepilogo dei parametri effettivi viene loggato prima del solve (livello configurabile).

## Esecuzione

### 1) Validazione & riepilogo

```bash
python loader.py --data-dir data
```

Lo script stampa:
- numero di dipendenti, turni, ruoli, giorni;
- coppie eleggibili e percentuali di assegnabilit�;
- eventuali turni senza candidati;
- riassunto dei time-off applicati (coppie escluse per dipendente).

### 2) Costruzione e solve del modello

```bash
python model_cp.py \
  --data-dir data \
  --max-seconds 60 \
  --log-search \
  --overtime-priority 1000 \
  --preferences-weight 200 \
  --fairness-weight 1 \
  --global-ot-cap-hours 999 \
  --output outputs/assignments.csv
```

Opzioni principali:
- `--max-seconds`: time limit CP-SAT.
- `--log-search`: abilita i log del solver.
- `--overtime-priority`, `--preferences-weight`, `--fairness-weight`: override dei pesi dell�obiettivo.
- `--default-ot-weight`: costo overtime predefinito se manca il ruolo nel CSV.
- `--global-ot-cap-hours`: tetto globale di straordinari (ore, opzionale).
- `--global-rest-hours`: soglia di riposo minimo globale (di default usa i valori per dipendente).
- `--output`: percorso CSV per le assegnazioni.

### Output

- `assignments.csv`: `shift_id`, `employee_id`, `day`, `start_dt`, `end_dt`, `role`, `required_staff`.
- Console: riepilogo shortfall, skill coverage, overtime, preferenze, consumi per dipendente.

---

## Come funziona

1. **Loader**
   - Valida gli input, converte i ruoli in insiemi e costruisce la maschera `can_assign` unendo qualifiche, disponibilita e time-off.
   - Integra `demand_windows.csv`, verifica che ogni `demand_id` sia definito e confronta la domanda con la capacita teorica (warning se insufficiente).
   - Fornisce riepiloghi diagnostici per turni scoperti e coppie bloccate.

2. **Precompute**
   - Normalizza gli orari (`start_dt`, `end_dt`), calcola le durate in minuti/ore.
   - Genera le coppie di riposo minime (direzionate, senza duplicati).

3. **Model (CP-SAT)**
   - Variabili binarie `x[e,s]` solo per le coppie `can_assign == 1`.
   - Vincoli hard: copertura per turno, copertura aggregata per finestra, skill per turno, riposi, un turno al giorno, limiti orari e straordinario.
   - Vincoli soft: domanda minima per turno (`demand`), preferenze e fairness.
   - Funzione obiettivo pesata come descritto sopra.
   - Estrazioni: assegnazioni, overtime, shortfall, skill coverage, preferenze, consumi per dipendente.
---

## Troubleshooting

- **Shortfall inatteso**: controlla i log di `loader.py` per coppie bloccate da disponibilit�/time-off o qualifiche mancanti.
- **Molto straordinario**: verifica `max_week_hours` e `max_overtime_hours`; valuta l�uso di `--overtime-priority` pi� alto.
- **Preferenze non rispettate**: aumenta `--preferences-weight` (resta comunque soft rispetto a shortfall/overtime).
- **Solve lento**: riduci l�orizzonte o imposta `--max-seconds`.

---

## Roadmap

1. **Multi-periodo** (multi-settimana) con vincoli su rolling horizon e gestione riposi oltre i confini ISO-week.
2. **Calibrazione automatica dei pesi** (multi-run o lexicographic multi-stage).
3. **Reportistica avanzata** (dashboard sulle preferenze, scenario analysis).

---

## Struttura del progetto

```
.
+-- loader.py         # Lettura/validazione CSV, availability/time-off, riepiloghi diagnostici
+-- precompute.py     # Normalizzazione orari, conflitti di riposo, utilit� temporali
+-- model_cp.py       # Modello CP-SAT, vincoli, obiettivo, CLI/estrazioni
+-- data/
�   +-- employees.csv
�   +-- shifts.csv
�   +-- availability.csv
�   +-- overtime_costs.csv
�   +-- preferences.csv          # opzionale
�   +-- time_off.csv             # opzionale
+-- tests/
    +-- test_loader.py
    +-- test_model_constraints.py
    +-- test_precompute.py
```

---

## Licenza

MIT License.