"""
Point d'entrée du pipeline.

Usage:
    python -m src.main <input.json> [output.json]

<input.json> peut contenir :
  - une seule entrée de log (objet JSON), ou
  - une liste d'entrées de logs (tableau JSON)

Si aucun argument n'est fourni, utilise sample_logs/example_critical.json
et écrit le résultat dans output/output.json.
"""

import os
import sys
import json
from datetime import datetime, timezone
from src.graph import build_graph


def load_logs(path: str):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # On normalise : une seule entrée devient une liste à un élément.
    if isinstance(data, dict):
        return [data]
    return data


def run_pipeline(input_path: str, output_path: str):
    app = build_graph()

    raw_logs = load_logs(input_path)

    initial_state = {"raw_logs": raw_logs}
    final_state = app.invoke(initial_state)

    output = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "insights": final_state.get("insights", {}),
        "anomalies": final_state.get("anomalies", []),
        "recommendations": final_state.get("recommendations", []),
        "service_status_summary": final_state.get(
            "service_status_summary", {"online": [], "degraded": [], "offline": []}
        ),
    }

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Pipeline exécuté avec succès. Résultat écrit dans : {output_path}")

    ingestion_errors = final_state.get("ingestion_errors", [])
    if ingestion_errors:
        print("Avertissements d'ingestion :")
        for err in ingestion_errors:
            print(f"  - {err}")

    return output


if __name__ == "__main__":
    input_path = sys.argv[1] if len(sys.argv) > 1 else "sample_logs/example_from_subject.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "output/output.json"
    run_pipeline(input_path, output_path)
