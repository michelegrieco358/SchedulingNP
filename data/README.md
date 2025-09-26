# Dataset Shift Scheduling

Questa cartella contiene i dati di input per il sistema di shift scheduling con **finestre istantanee** e **slot adattivi**.

## 📁 File inclusi

### **Dataset di esempio**
- `employees.csv` - 8 dipendenti con skill diversificate (nurse, doctor, triage)
- `shifts.csv` - 35 turni su 7 giorni con **nuovo schema** (demand + skill_requirements)
- `windows.csv` - **Finestre istantanee** per copertura granulare
- `availability.csv` - Alcune restrizioni di disponibilità
- `preferences.csv` - Preferenze dipendenti per turni specifici
- `overtime_costs.csv` - Costi straordinario per ruolo
- `config.yaml` - Configurazione con **slot adattivi abilitati**

### **Caratteristiche dimostrate**
- ✅ **Finestre istantanee**: WIN_NURSE_MORNING_RUSH (07:00-11:00, domanda=3)
- ✅ **Skill requirements**: first_aid=1, icu=1, emergency=1, customer_service=1
- ✅ **Nuovo schema CSV**: demand + skill_requirements invece di required_staff
- ✅ **Obiettivo persona-minuti**: Pesi convertiti automaticamente
- ✅ **Breakdown dettagliato**: Report costi per componente
- ✅ **Preferenze ≳ straordinari**: 0.33 vs 0.30

## 🚀 Quick Start

### **1. Esecuzione base**
```bash
# Dalla directory principale del progetto
python -m src.model_cp --config data/config.yaml --data-dir data --max-seconds 30
```

### **2. Con output salvato**
```bash
python -m src.model_cp \
  --config data/config.yaml \
  --data-dir data \
  --max-seconds 60 \
  --output data/results_assignments.csv
```

### **3. Validazione dati**
```bash
python -m src.loader --data-dir data
```

## 📊 Output atteso

### **Console**
```
=== Breakdown Obiettivo (persona-minuti) ===
- unmet_demand:  10080 min = 168.0000
- unmet_shift :  10080 min = 100.8000  
- unmet_skill :   4800 min =  64.0000
- fairness    :   3240 dev-min =   2.7000
- TOTALE      : 335.5000
Top-5 costi: unmet_demand(168.000), unmet_shift(100.800), unmet_skill(64.000)

Breakdown obiettivo salvato in reports/objective_breakdown.csv
```

### **File generati**
- `reports/objective_breakdown.csv` - Breakdown dettagliato costi
- `data/results_assignments.csv` - Assegnazioni finali (se specificato)

## 🔧 Personalizzazioni

### **Modifica pesi**
```bash
# Aumenta peso preferenze
python -m src.model_cp \
  --data-dir data \
  --preferences-weight 0.5 \
  --max-seconds 30

# Riduce peso straordinari
python -m src.model_cp \
  --data-dir data \
  --overtime-priority 0.1 \
  --max-seconds 30
```

### **Disabilita finestre (modalità legacy)**
Modifica `data/config.yaml`:
```yaml
windows:
  coverage_mode: "disabled"  # Invece di "adaptive_slots"
```

### **Vincoli hard per finestre**
```yaml
windows:
  coverage_mode: "adaptive_slots"
  enable_slot_slack: false  # Vincoli hard, no violazioni
```

## 📈 Analisi risultati

### **Skill coverage**
Il sistema mostra automaticamente la copertura delle skill richieste:
```
Skill coverage:
        shift_id            skill  required  covered  shortfall
S1_NURSE_MORNING        first_aid         1        1          0
  S1_NURSE_NIGHT              icu         1        1          0
   S1_DOCTOR_DAY        emergency         1        1          0
```

### **Preferenze soddisfatte**
```
Preferenze assegnate:
employee_id  liked_assigned  disliked_assigned  total_score
         E1               3                  0            6
         E4               3                  0            6
```

### **Breakdown costi**
Il file CSV contiene analisi dettagliata:
- **component**: Tipo di costo (unmet_demand, preferences, etc.)
- **cost**: Costo in persona-minuti
- **minutes**: Minuti di violazione
- **weight_per_min**: Peso applicato

## 🎯 Scenari di test

### **Test 1: Copertura finestre**
Verifica che le finestre WIN_NURSE_MORNING_RUSH siano coperte negli orari 07:00-11:00.

### **Test 2: Skill requirements**
Controlla che i turni notturni abbiano dipendenti con skill "icu".

### **Test 3: Preferenze vs straordinari**
Modifica i pesi e verifica che le preferenze siano prioritarie rispetto agli straordinari.

### **Test 4: Modalità legacy**
Confronta risultati con coverage_mode="disabled" vs "adaptive_slots".

## 🔍 Troubleshooting

### **Warning skill_requirements**
```
RuntimeWarning: skill_requirements per S1_NURSE_NIGHT sommano 2 > capacità di riferimento 1
```
**Soluzione**: È normale, indica che il turno richiede più skill della capacità base. Il sistema gestisce automaticamente con slack variables.

### **Nessuna domanda aggregata attiva**
Se vedi questo messaggio, significa che il sistema non ha trovato finestre da mappare. Verifica:
1. `windows.csv` esiste e ha contenuto valido
2. `coverage_mode: "adaptive_slots"` in config.yaml
3. Ruoli in windows.csv corrispondono a quelli in shifts.csv

### **Molti shortfall**
L'esempio ha intenzionalmente pochi dipendenti per dimostrare il sistema di shortfall. In scenari reali, aumenta il numero di dipendenti o riduci la domanda.

## 📚 Prossimi passi

1. **Modifica gli esempi** per i tuoi casi d'uso specifici
2. **Sperimenta con i pesi** per trovare il bilanciamento ottimale
3. **Aggiungi time_off.csv** per testare la gestione assenze
4. **Scala il dataset** con più dipendenti e turni per test realistici

Per documentazione completa, vedi il [README principale](../README.md).
