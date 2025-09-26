# Shift Scheduling CP-SAT

Pianificatore turni avanzato basato su **OR-Tools CP-SAT** con supporto per **finestre istantanee** e **slot adattivi**.

- **Input**: dipendenti, turni, disponibilità, costi di straordinario, preferenze soft, time-off hard e **finestre di copertura istantanea**.
- **Output**: assegnazioni turno-dipendente che rispettano i vincoli operativi, garantiscono copertura istantanea per finestre critiche, minimizzano gli shortfall e ottimizzano straordinari, preferenze e fairness.
- **Novità**: Sistema di **slot adattivi** per copertura granulare, **skill requirements** per turno, **obiettivo unificato** in persona-minuti.

> **Stato attuale:** modello su orizzonte settimanale con finestre istantanee; l'estensione multi-periodo resta in roadmap.

---

## Indice

- [Preservare l'integrità dei turni](#preservare-lintegrità-dei-turni)
- [Caratteristiche](#caratteristiche)
- [Finestre istantanee con slot adattivi](#finestre-istantanee-con-slot-adattivi)
- [Schema CSV e migrazione](#schema-csv-e-migrazione)
- [Feature flags e configurazione](#feature-flags-e-configurazione)
- [Funzione obiettivo (persona-minuti)](#funzione-obiettivo-persona-minuti)
- [Quick start](#quick-start)
- [Installazione](#installazione)
- [Dati di input (CSV)](#dati-di-input-csv)
- [Esecuzione](#esecuzione)
- [Come funziona](#come-funziona)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [Struttura del progetto](#struttura-del-progetto)
- [Licenza](#licenza)

---

## Preservare l'integrità dei turni

Il sistema garantisce che ogni turno assegnato sia sempre **completo e indivisibile**, eliminando la frammentazione artificiale per maggiore realismo operativo.

### **Come funziona**
1. **Segmentazione mantenuta**: Il sistema continua a creare segmenti temporali per calcolare la domanda con precisione
2. **Turni interi**: L'ottimizzazione seleziona solo turni completi, mai frazioni o slot parziali
3. **Vincoli di copertura**: `∑_{turni i che coprono segmento s} capacità_i * y[i] >= domanda_s`
4. **Integrità garantita**: Se un turno viene assegnato (y[i] = 1), copre **tutti** i segmenti nel suo intervallo

### **Configurazione**
```yaml
shifts:
  preserve_shift_integrity: true    # Default: true (raccomandato)
```

### **Esempio pratico**
```
Turno disponibile: 06:00-12:00
Segmenti automatici: [06-08], [08-10], [10-12]  
Domanda: serve solo fino alle 10:00

❌ PRIMA (slot): Poteva assegnare solo 06:00-10:00
✅ ADESSO (integrità): Assegna 06:00-12:00 completo o niente
```

### **Vantaggi**
- **Realismo operativo**: I dipendenti lavorano turni completi, non frazioni
- **Semplicità gestionale**: Più facile da pianificare e comunicare
- **Precisione mantenuta**: Calcoli accurati di domanda e copertura
- **Performance**: Spesso più veloce (meno variabili)

---

## Finestre istantanee con slot adattivi

Il sistema supporta **copertura istantanea** tramite finestre temporali che garantiscono un numero minimo di persone contemporaneamente presenti in specifici intervalli orari.

### **Come funziona**
1. **Definizione finestre**: `windows.csv` specifica intervalli (es. 07:00-11:00) con domanda minima per ruolo
2. **Generazione slot**: Il precompute crea automaticamente slot temporali adattivi basati sui turni esistenti
3. **Vincoli istantanei**: Ogni slot deve avere copertura ≥ domanda finestra (con slack opzionale)
4. **Ottimizzazione**: Violazioni pesate in base alla durata dello slot (persona-minuti)

### **Vantaggi vs Legacy**
- **Granularità fine**: Copertura per slot di 15-30-60 min invece di aggregati giornalieri
- **Flessibilità**: Finestre di durata variabile e sovrapposte
- **Precisione**: Garantisce copertura istantanea, non solo somme giornaliere
- **Performance**: Slot precomputati e variabili aggregate y[s] per efficienza

### **Esempio pratico**
```
Finestra: WIN_NURSE_MORNING_RUSH (07:00-11:00, domanda=3)
Turni:    S1_MORNING (06:00-14:00), S1_EVENING (14:00-22:00)
Slot:     [07:00-14:00] coperto da S1_MORNING
Vincolo:  persone_in_slot[07:00-14:00] ≥ 3
```

---

## Schema CSV e migrazione

### **Nuovo schema (raccomandato)**
Il nuovo schema supporta skill requirements e finestre istantanee:

```csv
# shifts.csv (nuovo)
shift_id,day,start,end,role,demand,skill_requirements
S1_NURSE_MORNING,2025-10-07,06:00,14:00,nurse,2,"first_aid=1"
```

```csv
# windows.csv (nuovo)
window_id,day,window_start,window_end,role,window_demand
WIN_NURSE_RUSH,2025-10-07,07:00,11:00,nurse,3
```

### **Schema legacy (supportato)**
Il sistema mantiene compatibilità con il formato precedente:

```csv
# shifts.csv (legacy)
shift_id,day,start,end,role,required_staff,demand_id
S1_NURSE_MORNING,2025-10-07,06:00,14:00,nurse,2,WIN_MORNING

# demand_windows.csv (legacy)
demand_id,window_start,window_end,role,window_demand
WIN_MORNING,07:00,11:00,nurse,3
```

### **Guida migrazione**
1. **Mantieni i file esistenti** per continuità
2. **Aggiungi `windows.csv`** per finestre istantanee
3. **Aggiungi colonna `skill_requirements`** in `shifts.csv` se necessario
4. **Configura `coverage_mode: "adaptive_slots"`** per attivare le nuove funzionalità
5. **Testa gradualmente** confrontando risultati legacy vs nuovo schema

---

## Feature flags e configurazione

### **Configurazione finestre**
```yaml
windows:
  coverage_mode: "adaptive_slots"    # "disabled" | "adaptive_slots"
  enable_slot_slack: true            # Permette violazioni con penalità
  warn_slots_threshold: 100          # Warning se slot > soglia
  hard_slots_threshold: 500          # Errore se slot > soglia
  midnight_policy: "split"           # "split" | "exclude"
```

### **Feature flags principali**
- **`coverage_mode`**: 
  - `"disabled"`: Solo vincoli legacy (demand_windows.csv)
  - `"adaptive_slots"`: Finestre istantanee con slot adattivi
- **`enable_slot_slack`**: 
  - `true`: Violazioni finestre penalizzate ma permesse
  - `false`: Vincoli hard, fallimento se impossibili
- **`midnight_policy`**:
  - `"split"`: Turni cross-midnight divisi in segmenti
  - `"exclude"`: Turni cross-midnight esclusi dalle finestre

### **Soglie performance**
- **`warn_slots_threshold`**: Avviso se troppi slot generati
- **`hard_slots_threshold`**: Blocco esecuzione per evitare esplosione combinatoriale

---

## Funzione obiettivo (persona-minuti)

Il sistema usa un **obiettivo unificato in persona-minuti** per stabilità numerica e coerenza semantica.

### **Conversione automatica**
I pesi in configurazione sono espressi in **persona-ora** (user-friendly) e convertiti automaticamente:
```yaml
penalties:
  unmet_window: 2.0      # 2.0 €/persona-ora → 0.033 €/persona-minuto
  overtime: 0.30         # 0.30 €/persona-ora → 0.005 €/persona-minuto
```

### **Termini obiettivo**
```
Minimizza:
  2.0/60 * Σ(slot_minutes[t] * short_slot[w,t])     # Finestre
+ 1.0/60 * Σ(shift_minutes[s] * short_shift[s])     # Turni
+ 0.8/60 * Σ(shift_minutes[s] * short_skill[s,k])   # Skill
+ 0.3/60 * Σ(overtime_minutes[e])                   # Straordinari
+ 0.33/60 * Σ(violations * mean_shift_minutes)      # Preferenze
+ 0.05/60 * Σ(fairness_deviations_minutes)          # Fairness
```

### **Vantaggi**
- **Stabilità numerica**: Coefficienti bilanciati (decine vs migliaia)
- **Coerenza semantica**: Tutti i termini in stessa unità
- **Granularità**: Slot di durata variabile pesati correttamente
- **Interpretabilità**: Costi direttamente confrontabili

---

## Quick start

### **1. Esempio rapido**
```bash
# Clona e installa
git clone <repo-url>
cd shift-scheduling
pip install -r requirements.txt

# Esegui esempio con finestre istantanee
python -m src.model_cp --config examples/config.yaml --data-dir examples --max-seconds 30

# Visualizza risultati
cat reports/objective_breakdown.csv
```

### **2. Esempio passo-passo**
```bash
# 1. Valida i dati
python -m src.loader --data-dir examples

# 2. Esegui ottimizzazione
python -m src.model_cp \
  --config examples/config.yaml \
  --data-dir examples \
  --max-seconds 60 \
  --output results/assignments.csv

# 3. Analizza breakdown costi
echo "=== Breakdown obiettivo ==="
cat reports/objective_breakdown.csv

# 4. Verifica copertura finestre
grep "WIN_" results/assignments.csv
```

### **3. Personalizzazione**
```bash
# Modifica pesi (preferenze > straordinari)
python -m src.model_cp \
  --data-dir examples \
  --preferences-weight 0.5 \
  --overtime-priority 0.2 \
  --max-seconds 60

# Disabilita finestre (modalità legacy)
python -m src.model_cp \
  --data-dir data \
  --config config_legacy.yaml \
  --max-seconds 60
```

### **4. Output atteso**
```
=== Breakdown Obiettivo (persona-minuti) ===
- unmet_window:    120 min =   4.0000
- unmet_demand:     60 min =   1.0000  
- preferences :      3 violazioni × 480min =   0.8000
- TOTALE      :   5.8000
Top-5 costi: unmet_window(4.000), unmet_demand(1.000), preferences(0.800)

Breakdown obiettivo salvato in reports/objective_breakdown.csv
```

---

## Caratteristiche

- **Copertura turni hard**: ogni turno richiede esattamente `required_staff`; se i candidati non bastano interviene lo shortfall (penalizzato fortemente).
- **Domanda aggregata per finestra**: il loader supporta sia il nuovo `windows.csv` (slot adattivi) sia il legacy `demand_windows.csv`, imponendo la copertura minima per fasce orarie/ruolo con slack pesantemente penalizzato.
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

### **Configurazione PYTHONPATH**

Il progetto utilizza import relativi per la riusabilità come package. Per utilizzare i moduli singolarmente o in ambienti diversi, assicurati che la directory root del progetto sia nel PYTHONPATH:

```bash
# Linux/macOS
export PYTHONPATH="${PYTHONPATH}:/path/to/shift-scheduling"

# Windows
set PYTHONPATH=%PYTHONPATH%;C:\path\to\shift-scheduling

# Oppure aggiungi programmaticamente in Python
import sys
sys.path.insert(0, '/path/to/shift-scheduling')
```

**Esempi di utilizzo:**

```python
# ✅ Corretto - esecuzione come modulo (raccomandato)
python -m src.model_cp --config config.yaml

# ✅ Corretto - con PYTHONPATH configurato
from src.loader import load_employees
from src.model_cp import ShiftSchedulingCpSolver

# ❌ Fallisce senza PYTHONPATH
python src/model_cp.py  # ImportError: No module named 'src.time_utils'
```

**Nota:** L'esecuzione tramite `python -m src.module` è il metodo raccomandato in quanto gestisce automaticamente i path relativi.

---

## Dati di input (CSV)

Mettere i CSV in `data/` (o passa `--data-dir`).

> **Nota transizione:** `load_shifts` accetta sia lo schema legacy (colonna `required_staff`) sia il nuovo schema basato su `demand` e `skill_requirements`; il loader sceglie automaticamente il mapping più coerente e mette a disposizione `load_data_bundle` per ottenere una vista normalizzata dei dati.

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
| required_staff | int  | (legacy) minimo hard per turno; se assente viene rimpiazzato da `demand` |
| demand         | int  | domanda minima soft; se la colonna manca viene usato `required_staff`      |
| demand_id      | str  | legacy: identifica la finestra in `demand_windows.csv`                      |
| skill_requirements | str  | opzionale: requisiti di skill (`{{"muletto":1}}` o `muletto=1,primo=1`)    |

> Il pre-processing genera `start_dt`, `end_dt` e `duration_h`, gestendo i casi cross-midnight.
### `windows.csv` (nuovo schema)

| colonna        | tipo | descrizione                                                                |
|----------------|------|----------------------------------------------------------------------------|
| window_id      | str  | id finestra (es. `WIN_DAY_1`)                                              |
| day            | date | `YYYY-MM-DD`                                                               |
| window_start   | time | `HH:MM` (accetta `24:00` come fine giornata)                               |
| window_end     | time | `HH:MM`, deve essere maggiore di `window_start`                           |
| role           | str  | ruolo cui si applica la finestra                                          |
| window_demand  | int  | numero minimo di persone contemporanee richieste nella finestra           |

> Se `windows.csv` manca il loader mantiene la modalità legacy, logga un warning e forza `config.windows.coverage_mode = "disabled"`.
> Il file `demand_windows.csv` resta supportato come fallback per compatibilità retroattiva.

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
