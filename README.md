# Shift Scheduling - NP-Hard 

Un motore di pianificazione turni avanzato basato su **OR-Tools CP-SAT**. 
Caratteristiche principali:
- prevede la presenza di **vincoli hard** da rispettare necessariamente, se non è possibile l'istanza del problema si dichiara **infeasible**.
- prevede la presenza di **vincoli soft**: si cerca di rispettarli, se non è possibile si assegnano varibili di slack e **penalità, da minimizzare nella funzione obiettivo.**
- prevede l'assegnazione di **turni intesi come contenitori temporali indivisibili (ne viene sempre preservata l'integrità)**
- prevede la possibilità di definire **finestre temporali in cui indicare la domanda di personale e di competenze richieste, in modo da adattare l'orario alle esigenze dell'azienda**; per esempio in un certo intervallo di tempo possono essere previsti picchi di lavoro e quindi di domanda di personale, oppure sono necessarie delle particolari skill
- prevede la presenza di vincoli contrattuali: numero massimo di ore, massimo di straordinari, minimo periodo di riposo tra un turno e l'altro, ecc..
- tiene conto delle preferenze dei dipendenti riguardo i turni che gli possono venire assegnati (vincolo soft)
- considera la presenza di lavoratori dipendenti interni e di possibili risorse esterne, che non necessariamente devono essere attivate.
- considera la fairness (workload balance): l'assegnazione di ore deve essere il piu omogenea possibile tra i dipendenti, in modo tale che gli straordinari, se previsti, non vengano concentrati su poche persone
- **la funzione obiettivo rispecchia una logica lessicografica, implementata con l'uso di pesi diversi.**
Significa che le penalità da minimizzare nella funzione obiettivo sono ordinate per priorità e ogni priorità viene espressa da un peso numerico di un ordine di grandezza maggiore del successivo.
In questo modo il risolutore CP-SAT minimizza prima i minuti non coperti (domanda), poi le skill non coperte, poi la quantità di straordinari, l'overstaffing, le preferenze espresse dai dipendenti e infine la fairness, rispettando l’ordine di importanza desiderato. L'ordine di importanza e i pesi possono essere modificati nel file di configurazione in base alle esigenze. In questo modo si può anche passare da una lessicografica a una classica funzione obiettivo con pesi.
---

## INPUT 

## Struttura dei dati

Tutti i file CSV vanno nella cartella `data/`. Il loader costruisce poi i dataframe pandas e le altre strutture dati necessarie.

### `employees.csv`
Anagrafica e regole orarie per ogni lavoratore 
```
employee_id,name,roles,skills,contracted_hours,max_overtime_hours,min_rest_hours
```
- **employee_id**: chiave primaria 
- **roles**: ruoli che può coprire (es. `nurse|doctor`).
- **skills**: competenze (es. `seniority,leadership`).
- **contracted_hours**: se presente ⇒ dipendente **interno**
- **max_overtime_hours**: massimo di ore di straordinario settimanali (solo per i dipendenti)
- **min_rest_hours** : riposo minimo tra un turno e l'altro. Se non indicato, si usa riposo minimo globale.
-  Assenza del campo **contracted_hours** ⇒ risorsa **esterna**  ⇒ costi di attivazione
  

### `shifts.csv`
Turni come contenitori temporali indivisibili.
```
shift_id,day,start,end,role
```
Ogni turno ha un ID, una data, un orario di inizio e di fine, è riferito a un ruolo. 
Opzionale: Può essere indicata la domanda di personale per ruolo (REQUIRED_STAFF) e di skill. 
Sia le richieste di personale che quelle di skill possono essere definite sulle finestre temporali (windows) e non sui turni. 
Se sono presenti sia per turni che per finestre il programma usa di default quelle indicate in windows.

### `windows.csv`
Le windows sono finestre temporali in cui l'azienda può avere esigenze di personale / skill. Possono essere quindi intervalli di tempo diversi dai turni.
(Esempio: un bar ha turni di 8 ore ma nelle finestre temporale 12-14 e 18-21 ha una maggiore esigenza di personale. Oppure un'azienda che ha picchi di produzione.)
Dati:
``` 
window_id,day,window_start,window_end,role,window_demand,window_skill_requirements
```
- id, data, ora inizio e fine
- `window_demand`: persone richieste nella finestra.
- `window_skill_requirements`: es. `seniority=2,leadership=1`  
  N.B. : una stessa persona può avere su più skill.

### `time_off.csv`
Assenze o indisponibilità.
- **Interni**: malattia/ferie (conteggiate come ore lavorate per il computo del minimo contrattuale).
- **Esterni**: semplice indisponibilità.

### `availability.csv`
Disponibilità dichiarata (0/1) per ciascun turno.  
Serve per escludere a priori turni che un dipendente non può o non vuole coprire.

### `preferences.csv` (opzionale)
Preferenze soft su turni (`employee_id,shift_id,score`) per incentivare o scoraggiare assegnazioni.
Ogni preferenza appartiene al range [-2, 2 ] in base a una scala di gradimento (-2=turno molto sgradito, 2=turno molto gradito)

---

## Flusso di elaborazione

1. **Loader**  
   - Valida i CSV e crea le maschere `qualification` e `availability`(per indicare, per ogni turno, i lavoratori che possono coprirlo in base a qualifica e disponibilità)
   - Associa ogni turno alle finestre temporali che copre.
   - Determina interni/esterni in base a `contracted_hours`.

2. **Precompute**  
   - Costruisce segmenti(slot) in base ai punti di discontinuità di turni e finestre. In questo modo ogni slot è contenuto o non contenuto in una finestra temporale (non c'è copertura parziale)
   - Genera vincoli di riposo tra turni.

3. **Modello CP-SAT**  
   - Variabili: `x[e,s]` (assegnazione dipendente-turno), straordinari, slack di copertura/skill.
   - Vincoli hard: Turni indivisibili, no sovrapposizioni, rispetto riposi e vincoli contrattuali (per esempio massimo ore di strordinario).
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
   - breakdown: serve per stabilire quale delle clausole di penalità della funzione obiettivo ha avuto un peso maggiore e quindi quale esigenza/richiesta è stata soddisfatta in misura minore.

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
- **Overstaffing**: ammesso se inevitabile; penalizzato per ridurre l’eccesso superfluo.

---



## TO-DO: 
- implementare lexicografica pura perchè i pesi con ordini di grandezza troppo diversi possono rallentare il solver.
- decidere come utilizzare time-off in modo più realistico.
- passare a logica multi-periodo / rolling-horizon.

