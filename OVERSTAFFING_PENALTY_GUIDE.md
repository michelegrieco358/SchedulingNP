# Guida alla Penalità di Overstaffing

## Panoramica

La penalità di overstaffing è una nuova funzionalità che permette al modello di ottimizzazione di penalizzare l'assegnazione di personale in eccesso rispetto alla domanda richiesta nelle finestre temporali.

## Motivazione

- **Controllo dei costi**: Evitare di assegnare più personale del necessario
- **Efficienza operativa**: Ottimizzare l'uso delle risorse umane
- **Bilanciamento**: Peso basso per non compromettere la copertura essenziale

## Configurazione

### File config.yaml

```yaml
penalties:
  unmet_window: 2.0      # Priorità massima: copertura domanda
  unmet_demand: 1.0      # Copertura turni obbligatori
  unmet_skill: 0.8       # Copertura competenze
  unmet_shift: 0.6       # Domanda soft dei turni
  overstaff: 0.15        # NUOVO: Penalità overstaffing (peso basso)
  overtime: 0.30         # Straordinari
  preferences: 0.33      # Preferenze dipendenti
  fairness: 0.05         # Equità carico di lavoro

objective:
  priority:
    - unmet_window         # 1° priorità: soddisfare domanda
    - unmet_demand         # 2° priorità: coprire turni
    - unmet_skill          # 3° priorità: competenze
    - unmet_shift          # 4° priorità: domanda soft
    - overstaff            # 5° priorità: evitare eccessi
    - overtime             # 6° priorità: limitare straordinari
    - fairness             # 7° priorità: equità
    - preferences          # 8° priorità: preferenze
```

## Funzionamento Tecnico

### Calcolo dell'Overstaffing

Per ogni finestra temporale `g`:

```
overstaff[g] = max(0, assigned[g] - window_demand[g])
```

Dove:
- `assigned[g]` = somma dei turni assegnati che coprono la finestra g
- `window_demand[g]` = domanda richiesta per la finestra g

### Integrazione nel Modello CP-SAT

1. **Variabili**: `window_overstaff_vars[window_id]`
2. **Vincoli**: `assigned_capacity - demand = overstaff_var`
3. **Obiettivo**: `minimize(... + w_overstaff * sum(overstaff_vars))`

### Ordine di Priorità

Il peso dell'overstaffing (0.15) è posizionato strategicamente:

- **Superiore a**: preferences (0.33), fairness (0.05) - può influenzare scelte secondarie
- **Inferiore a**: unmet_window (2.0), overtime (0.30) - non compromette copertura essenziale

## Esempi d'Uso

### Scenario 1: Copertura Ottimale
```
Finestra: 09:00-17:00, Domanda: 3 persone
Turni disponibili: 4 persone qualificate
Risultato: Assegna 3 persone (overstaffing = 0)
```

### Scenario 2: Overstaffing Inevitabile
```
Finestra: 09:00-17:00, Domanda: 3 persone
Turni indivisibili: 2 turni da 2 persone ciascuno
Risultato: Assegna 4 persone (overstaffing = 1, penalizzato ma accettabile)
```

### Scenario 3: Bilanciamento con Altri Obiettivi
```
Finestra A: Domanda 2, Capacità 3 → Overstaffing = 1
Finestra B: Domanda 2, Capacità 1 → Shortfall = 1
Il modello bilancia: preferisce coprire B anche se crea overstaffing in A
```

## Benefici

1. **Controllo automatico**: Il solver evita automaticamente assegnazioni eccessive
2. **Flessibilità**: Peso configurabile per adattarsi a diverse esigenze operative
3. **Compatibilità**: Non interferisce con vincoli di copertura essenziali
4. **Trasparenza**: Overstaffing visibile nei report per analisi post-ottimizzazione

## Limitazioni

1. **Turni indivisibili**: Un po' di overstaffing può essere inevitabile
2. **Peso bilanciato**: Troppo alto può compromettere la copertura, troppo basso è inefficace
3. **Granularità**: Basato su finestre temporali, non su singoli turni

## Monitoraggio

L'overstaffing sarà visibile in:
- Report CSV di breakdown obiettivo
- Log di ottimizzazione
- Metriche di performance del modello

## Raccomandazioni

- **Peso iniziale**: 0.15 (testato e bilanciato)
- **Monitoraggio**: Verificare impact su copertura essenziale
- **Tuning**: Aggiustare peso basandosi su risultati operativi
- **Analisi**: Confrontare costi overstaffing vs. benefici copertura
