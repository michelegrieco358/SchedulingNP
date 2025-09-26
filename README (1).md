# Shift Scheduling – Demand & Skill by Time Windows

Un motore di pianificazione turni basato su **OR-Tools CP-SAT** che:
- usa **turni come contenitori temporali indivisibili**,
- definisce **domanda di personale e competenze richieste solo per finestre temporali**,
- rispetta vincoli contrattuali e di riposo,
- minimizza mancanze di copertura, straordinari e overstaffing superfluo.

---

## Struttura dei dati

Tutti i file CSV vanno nella cartella `data/`.

### `employees.csv`
Anagrafica e regole orarie.
```
employee_id,name,roles,skills,contracted_hours,max_overtime_hours,min_rest_hours
```
- **roles**: ruoli che può coprire (es. `nurse|triage`).
- **skills**: competenze (es. `icu,leadership`).
- **contracted_hours**: se presente ⇒ dipendente **interno**  
  (minimo ore settimanali hard, malattia e ferie conteggiate come ore lavorate).
- Assenza del campo ⇒ risorsa **esterna** (nessun minimo; solo limiti max e costi di attivazione).

### `shifts.csv`
Turni come contenitori temporali.
```
shift_id,day,start,end,role
```
- Nessuna domanda o skill: servono solo a definire la griglia di assegnazione e a rispettare vincoli (riposi, disponibilità).

### `windows.csv`
Fonte unica di domanda e competenze richieste.
```
window_id,day,window_start,window_end,role,window_demand,window_skill_requirements
```
- `window_demand`: persone richieste nella finestra.
- `window_skill_requirements`: es. `icu=2,leadership=1`  
  (una stessa persona può contare su più skill).

### `time_off.csv`
Assenze o indisponibilità.
- **Interni**: malattia/ferie (conteggiate come ore lavorate per il minimo contrattuale).
- **Esterni**: semplice indisponibilità.

### `availability.csv`
Disponibilità dichiarata (0/1) per ciascun turno.  
Serve per escludere a priori turni che un dipendente non può o non vuole coprire.

### `preferences.csv` (opzionale)
Preferenze soft su turni (`employee_id,shift_id,score`) per incentivare o scoraggiare assegnazioni.

---

## Flusso di elaborazione

1. **Loader**  
   - Valida i CSV e crea le maschere `qualification` e `availability`.
   - Associa ogni turno alle finestre che copre.
   - Determina interni/esterni in base a `contracted_hours`.

2. **Precompute**  
   - Costruisce segmenti/finestra.
   - Genera vincoli di riposo tra turni.

3. **Modello CP-SAT**  
   - Variabili: `x[e,s]` (assegnazione dipendente-turno), straordinari, slack di copertura/skill.
   - Vincoli hard:
     - Copertura domanda e skill per ogni finestra.
     - Turni indivisibili, no sovrapposizioni, rispetto riposi e contratti.
   - Funzione obiettivo (ordine decrescente di peso):
     1. minuti non coperti (domanda),
     2. minuti senza skill,
     3. minuti di straordinario,
     4. minuti di overstaffing (peso basso, senza buffer),
     5. preferenze,
     6. fairness (equilibrio del carico).

4. **Output**
   - `assignments.csv`: assegnazioni definitive.
   - Report su copertura finestre, skill, straordinari, preferenze e overstaffing.

---

## Configurazione (`config.yaml`)

Sezione tipica:
```yaml
penalties:
  w_unmet_window: 1000
  w_unmet_skill: 500
  w_overtime: 50
  w_overstaff: 5
  w_preferences: 1
  w_fairness: 0.5
rest:
  min_between_shifts: 11  # ore di riposo minimo
solver:
  time_limit_sec: 600
  mip_gap: 0.01
```

---

## Esecuzione

1. Verifica dati
   ```bash
   python src/loader.py --data-dir data
   ```
2. Risolvi il problema
   ```bash
   python src/model_cp.py --config config.yaml --data-dir data
   ```
3. Risultati
   - File `assignments.csv` e report nella cartella `reports/`.

---

## Principi chiave
- **Turni = contenitori temporali**: definiscono quando una persona può lavorare, non la domanda.
- **Finestre = domanda & competenze**: unico punto di verità per quante persone e quali skill servono in ogni momento.
- **Multi-skill**: una stessa persona può coprire più requisiti skill nella stessa finestra.
- **Contratti interni hard**: minimo ore obbligatorio; malattia e ferie contano come ore lavorate.
- **Overstaffing**: ammesso e inevitabile; penalizzato leggermente per ridurre solo l’eccesso superfluo.

---

## Licenza
Specificare qui la licenza del progetto (es. MIT, GPL, ecc.).
