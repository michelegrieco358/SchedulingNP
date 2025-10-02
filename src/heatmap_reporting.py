import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import os

def plot_coverage_heatmap(assignments_path, windows_path, slot_minutes=60):
    # Carica dati
    assignments = pd.read_csv(assignments_path)
    windows = pd.read_csv(windows_path)

    # Prepara giorni e slot orari
    days = sorted(windows["day"].unique())
    n_days = len(days)
    n_slots = 24 * 60 // slot_minutes
    slot_labels = [f"{h:02d}:{m:02d}" for h in range(0, 24) for m in range(0, 60, slot_minutes)]

    day_to_col = {d: i for i, d in enumerate(days)}
    demand_matrix = np.zeros((n_slots, n_days))
    coverage_matrix = np.zeros((n_slots, n_days))

    # Aggrega domanda per slot
    for _, win in windows.iterrows():
        col = day_to_col[win["day"]]
        start_h, start_m = map(int, win["window_start"].split(":"))
        end_h, end_m = map(int, win["window_end"].split(":"))
        start = (start_h * 60 + start_m) // slot_minutes
        end = (end_h * 60 + end_m) // slot_minutes
        demand = win["window_demand"]
        demand_matrix[start:end, col] += demand

    # Aggrega copertura per slot
    for _, row in assignments.iterrows():
        col = day_to_col.get(row["day"])
        if col is None:
            continue
        start_dt = pd.to_datetime(row["start_dt"])
        end_dt = pd.to_datetime(row["end_dt"])
        start = (start_dt.hour * 60 + start_dt.minute) // slot_minutes
        end = (end_dt.hour * 60 + end_dt.minute) // slot_minutes
        coverage_matrix[start:end, col] += 1

    # Calcola shortfall
    shortfall_matrix = np.maximum(demand_matrix - coverage_matrix, 0)

    # Plot heatmap
    plt.figure(figsize=(n_days * 1.5, 8))
    plt.imshow(shortfall_matrix, aspect='auto', cmap='Reds', origin='lower')
    for col in range(1, n_days):
        plt.axvline(x=col - 0.5, color='black', linestyle='-', linewidth=2.5, alpha=1.0)
    plt.xlabel("Giorno")
    plt.ylabel("Orario")
    plt.xticks(ticks=range(n_days), labels=days)
    # Etichette ogni ora
    hour_ticks = [i for i in range(n_slots) if (i * slot_minutes) % 60 == 0]
    hour_labels = [f"{i:02d}:00" for i in range(24)]
    plt.yticks(ticks=hour_ticks, labels=hour_labels)
    plt.title("Shortfall di copertura (rosso = mancata copertura)")
    plt.colorbar(label="Shortfall (persone mancanti)")
    plt.tight_layout()
    import os
    output_dir = "reports"
    os.makedirs(output_dir, exist_ok=True)
    plot_path = os.path.join(output_dir, "coverage_plot.png")
    plt.savefig(plot_path)
    print(f"Heatmap salvata in {plot_path}")
    plt.close()


if __name__ == "__main__":
    # Modifica i percorsi se necessario
    plot_coverage_heatmap(
        "reports/assignments_dataset4.csv",
        "dataset4/windows.csv",
        slot_minutes=60  # puoi cambiare la risoluzione
    )