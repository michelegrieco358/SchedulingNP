# Shift Scheduling - NP-Hard 

Un motore di pianificazione turni avanzato basato su **OR-Tools CP-SAT**. 
Caratteristiche principali:
- prevede la presenza di **vincoli hard**: da rispettare necessariamente, se non è possibile l'istanza del problema si dichiara **infeasible**.
- prevede la presenza di **vincoli soft**: si cerca di rispettarli, se non è possibile si assegnano **penalità, da minimizzare nella funzione obiettivo.**
- prevede l'assegnazione di **turni intesi come contenitori temporali indivisibili (ne viene sempre preservata l'integrità)**
- prevede la possibilità di definire **finestre temporali in cui indicare la domanda di personale e di competenze richieste, in modo da adattare l'orario alle esigenze dell'azienda**; per esempio in un certo intervallo di tempo possono essere previsti picchi di lavoro e quindi di domanda di personale, oppure sono necessarie delle particolari skill
- prevede la presenza di vincoli (hard) contrattuali: numero massimo di ore, massimo di straordinari, riposo, ecc..
- tiene conto delle preferenze dei dipendenti riguardo i turni che gli possono venire assegnati (vincolo soft)
- considera la presenza di lavoratori dipendenti interni e di possibili risorse esterne, che non necessariamente devono essere attivate
- considera la fairness (workload balance): l'assegnazione di ore deve essere il piu omogenea possibile tra i dipendenti, in modo tale che gli straordinari, se previsti, non vengano concentrati su poche persone
- **la funzione obiettivo rispecchia una logica lessicografica, implementata con l'uso di pesi diversi.**
Significa che le penalità da minimizzare nella funzione obiettivo sono ordinate per priorità e ogni priorità viene espressa da un peso numerico di un ordine di grandezza maggiore del successivo.
In questo modo il risolutore CP-SAT minimizza prima i minuti non coperti (domanda), poi quelli senza skill, poi la quantità di straordinari, l'overstaffing, le preferenze espresse dai dipendenti e infine fairness, rispettando l’ordine di importanza desiderato. L'ordine di importanza e i pesi possono essere modificati nel file di configurazione in base alle esigenze.
---

## Struttura dei dati

Tutti i file CSV vanno nella cartella `data/`.

### `employees.csv`
Anagrafica e regole orarie.
```
employee_id,name,roles,skills,contracted_hours,max_overtime_hours,min_rest_hours
```
- **roles**: ruoli che può coprire (es. `nurse|doctor`).
- **skills**: competenze (es. `icu,leadership`).
- **contracted_hours**: se presente ⇒ dipendente **interno**  
  (minimo ore settimanali hard, malattia e ferie conteggiate come ore lavorate).
- Assenza del campo ⇒ risorsa **esterna** (nessun minimo; solo limiti max e costi di attivazione).

### `shifts.csv`
Turni come contenitori temporali.
```
shift_id,day,start,end,role
```

### `windows.csv`
Indicazione di domanda e competenze richieste.
```
window_id,day,window_start,window_end,role,window_demand,window_skill_requirements
```
- `window_demand`: persone richieste nella finestra.
- `window_skill_requirements`: es. `seniority=2,leadership=1`  
  N.B. : una stessa persona può avere su più skill).

### `time_off.csv`
Assenze o indisponibilità.
- **Interni**: malattia/ferie (conteggiate come ore lavorate per il computo del minimo contrattuale).
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
   - Costruisce segmenti in base ai punti di discontinuità di turni e finestre.
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
- **Finestre = domanda & competenze**: quante persone e quali skill servono in ogni momento.
- **Multi-skill**: una stessa persona può coprire più requisiti skill nella stessa finestra.
- **Contratti interni hard**: minimo ore obbligatorio; malattia e ferie contano come ore lavorate.
- **Overstaffing**: ammesso e inevitabile; penalizzato per ridurre l’eccesso superfluo.

---
