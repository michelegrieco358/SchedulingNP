# Guida: Preservare l'Integrità dei Turni

## Panoramica

La funzionalità **preserve_shift_integrity** è stata implementata per garantire che l'ottimizzazione selezioni solo **turni interi**, non slot parziali, mantenendo al contempo la segmentazione temporale utile per calcolare la domanda.

## Problema Risolto

### Prima (Modalità Slot)
- Il sistema poteva frammentare i turni in slot più piccoli
- Esempio: turno 06:00-12:00 poteva essere diviso in slot 06:00-10:00 e 10:00-12:00
- L'ottimizzatore poteva selezionare solo una parte del turno (es. solo 06:00-10:00)

### Dopo (Modalità Integrità Turni)
- Ogni turno è trattato come un'unità indivisibile
- Se un turno viene assegnato, copre **tutto** il suo intervallo temporale
- La segmentazione rimane per calcolare domanda e copertura, ma non per decidere assegnazioni

## Configurazione

### File config.yaml
```yaml
shifts:
  preserve_shift_integrity: true  # Default: true
```

### Valori Possibili
- `true`: Attiva la modalità integrità turni (raccomandato)
- `false`: Mantiene la logica precedente con slot liberi

## Implementazione Tecnica

### Formulazione Matematica

#### Variabili Decisionali
- `x[e,s] ∈ {0,1}`: Variabile binaria per assegnazione dipendente-turno
- `y[s] = Σ_e x[e,s]`: Variabile aggregata per turno (numero di persone assegnate)

#### Vincoli di Copertura per Segmenti
Per ogni segmento temporale `seg`:
```
Σ_{turni i che coprono seg} a[i,seg] * y[i] + slack[seg] >= domanda[seg]
```

Dove:
- `a[i,seg]` = capacità fornita dal turno `i` nel segmento `seg` (persona-minuti)
- `domanda[seg]` = domanda richiesta nel segmento `seg` (persona-minuti)
- `slack[seg]` = variabile di shortfall per il segmento

#### Integrità dei Turni
- Se `y[i] = 1`, il turno `i` copre **tutti** i segmenti nel suo intervallo
- Non è possibile attivare solo una parte del turno

### Componenti Implementati

#### 1. Parametro di Configurazione
```python
class ShiftsConfig(BaseModel):
    preserve_shift_integrity: bool = True
```

#### 2. Variabili del Solver
```python
# Nuove variabili per segmenti con turni interi
self.segment_shortfall_vars: dict[str, cp_model.IntVar] = {}
self.shift_to_covering_segments: dict[str, list[str]] = {}
self.segment_demands: dict[str, int] = {}
```

#### 3. Vincoli di Segmenti
```python
def _add_segment_coverage_constraints(self) -> None:
    """Implementa vincoli di copertura per segmenti con turni interi."""
```

#### 4. Funzione Obiettivo Aggiornata
```python
# Se preserve_shift_integrity=True, usa i segmenti invece delle finestre
if self.preserve_shift_integrity and has_segment:
    priority_map["unmet_window"] = (segment_expr, has_segment)
```

## Esempio Pratico

### Scenario
- **Turno A**: 06:00-12:00 (6 ore)
- **Turno B**: 10:00-16:00 (6 ore)
- **Finestra di domanda**: 08:00-14:00 (serve 2 persone)

### Segmentazione Automatica
Il sistema crea automaticamente i segmenti:
1. **Segmento 1**: 06:00-08:00 (coperto solo da Turno A)
2. **Segmento 2**: 08:00-10:00 (coperto solo da Turno A)
3. **Segmento 3**: 10:00-12:00 (coperto da Turno A e Turno B)
4. **Segmento 4**: 12:00-14:00 (coperto solo da Turno B)
5. **Segmento 5**: 14:00-16:00 (coperto solo da Turno B)

### Con preserve_shift_integrity=True
- **Decisione**: Assegnare Turno A completo (06:00-12:00) o Turno B completo (10:00-16:00)
- **Risultato**: Se Turno A è assegnato, copre segmenti 1, 2, 3
- **Garanzia**: Non può essere assegnato solo 06:00-10:00

### Con preserve_shift_integrity=False (Legacy)
- **Decisione**: Può creare slot parziali come 06:00-10:00
- **Risultato**: Maggiore flessibilità ma turni frammentati

## Vantaggi

### 1. **Realismo Operativo**
- I turni nella realtà sono unità indivisibili
- I dipendenti lavorano turni completi, non frazioni

### 2. **Semplicità Gestionale**
- Più facile da pianificare e comunicare
- Riduce la complessità delle assegnazioni

### 3. **Mantenimento Precisione**
- La segmentazione rimane per calcoli accurati di domanda
- Nessuna perdita di precisione nell'ottimizzazione

### 4. **Compatibilità**
- Funziona con tutti i vincoli esistenti
- Retrocompatibile (può essere disabilitato)

## Impatto sulle Performance

### Test di Performance
```python
def test_performance_with_integrity():
    # 5 dipendenti, 3 turni
    # Tempo di esecuzione: < 10 secondi
    # Risultato: OPTIMAL/FEASIBLE
```

### Risultati
- **Tempo di build**: Comparabile alla modalità slot
- **Tempo di solve**: Spesso più veloce (meno variabili)
- **Qualità soluzione**: Mantiene ottimalità

## Casi d'Uso

### Quando Usare preserve_shift_integrity=True
- ✅ **Ospedali**: Turni infermieri/medici sono fissi
- ✅ **Sicurezza**: Guardie lavorano turni completi
- ✅ **Produzione**: Operatori su turni standard
- ✅ **Retail**: Commessi con orari definiti

### Quando Considerare preserve_shift_integrity=False
- ⚠️ **Consulenza**: Ore flessibili per progetto
- ⚠️ **Freelance**: Lavoro a ore variabili
- ⚠️ **Ricerca**: Quando serve massima flessibilità

## Migrazione

### Da Modalità Slot a Integrità Turni

#### 1. Aggiorna Configurazione
```yaml
# Prima
windows:
  coverage_mode: "adaptive_slots"

# Dopo
shifts:
  preserve_shift_integrity: true
windows:
  coverage_mode: "adaptive_slots"  # Mantieni per segmentazione
```

#### 2. Verifica Risultati
```python
# Test che le assegnazioni siano turni completi
assignments = solver.extract_assignments(cp_solver)
for _, assignment in assignments.iterrows():
    shift_id = assignment["shift_id"]
    # Verifica che sia un turno completo dal tuo catalogo
    assert shift_id in known_complete_shifts
```

#### 3. Confronta Performance
```python
# Modalità precedente
solver_old = ShiftSchedulingCpSolver(..., preserve_shift_integrity=False)

# Nuova modalità
solver_new = ShiftSchedulingCpSolver(..., preserve_shift_integrity=True)

# Confronta tempi e qualità soluzioni
```

## Risoluzione Problemi

### Problema: "Nessun segmento con domanda trovato"
**Causa**: Mancano dati di segmentazione o finestre
**Soluzione**: 
```python
# Verifica che ci siano finestre definite
assert len(window_demands) > 0
# Verifica che adaptive_slot_data sia presente
assert adaptive_slot_data is not None
```

### Problema: "Soluzione non trovata"
**Causa**: Vincoli troppo restrittivi con turni interi
**Soluzione**:
```python
# Temporaneamente disabilita per debug
solver = ShiftSchedulingCpSolver(..., preserve_shift_integrity=False)
# Analizza la soluzione flessibile, poi adatta i vincoli
```

### Problema: Performance degradate
**Causa**: Troppi segmenti generati
**Soluzione**:
```yaml
windows:
  warn_slots_threshold: 200  # Riduci soglia
  hard_slots_threshold: 500  # Limita segmenti
```

## Test e Validazione

### Test Automatici
```bash
# Test specifici per integrità turni
python -m pytest tests/test_preserve_shift_integrity.py -v

# Suite completa
python -m pytest --tb=short
```

### Test Manuali
```python
# Verifica integrità assegnazioni
def verify_shift_integrity(assignments, shifts):
    for _, assignment in assignments.iterrows():
        shift_id = assignment["shift_id"]
        shift_info = shifts[shifts["shift_id"] == shift_id].iloc[0]
        
        # Verifica che sia un turno completo
        assert assignment["start_dt"] == shift_info["start_dt"]
        assert assignment["end_dt"] == shift_info["end_dt"]
        assert assignment["duration_h"] == shift_info["duration_h"]
```

## Conclusioni

La funzionalità **preserve_shift_integrity** rappresenta un significativo miglioramento del sistema di ottimizzazione, fornendo:

1. **Maggiore realismo** nelle assegnazioni
2. **Semplicità operativa** per i gestori
3. **Mantenimento della precisione** nei calcoli
4. **Compatibilità** con il sistema esistente

È **raccomandato** utilizzare `preserve_shift_integrity=True` per la maggior parte dei casi d'uso reali, mantenendo `preserve_shift_integrity=False` solo per scenari che richiedono massima flessibilità temporale.
