"""
Nœud 1 : Ingestion et Analyse de Données Techniques.

Rôle : charger les logs (une liste d'entrées JSON, ou une seule entrée),
valider la présence des champs indispensables, et normaliser le format
pour les nœuds suivants.

On reste volontairement tolérant : si un champ optionnel manque sur une
entrée, on ne casse pas tout le pipeline, on log l'erreur dans
`ingestion_errors` et on continue avec les entrées valides.
"""

from typing import Any, Dict
from src.state import PipelineState

# Champs qu'on considère indispensables pour pouvoir analyser une entrée.
REQUIRED_FIELDS = [
    "timestamp",
    "cpu_usage",
    "memory_usage",
    "latency_ms",
    "error_rate",
    "uptime_seconds",
    "service_status",
]

# Type attendu pour chaque champ requis, pour éviter qu'une valeur malformée
# (ex: cpu_usage en string) fasse planter les comparaisons numériques du
# nœud d'analyse en aval.
FIELD_TYPES: Dict[str, Any] = {
    "timestamp": str,
    "cpu_usage": (int, float),
    "memory_usage": (int, float),
    "latency_ms": (int, float),
    "error_rate": (int, float),
    "uptime_seconds": (int, float),
    "service_status": dict,
}

# Plage acceptable par métrique (bornes incluses). Une valeur hors plage
# indique une donnée corrompue (capteur défaillant, bug d'export) plutôt
# qu'une vraie mesure. Couvre aussi des métriques optionnelles (disk_usage,
# io_wait, temperature_celsius) utilisées par le nœud d'analyse : la plage
# n'est vérifiée que si le champ est présent et numérique.
FIELD_RANGES: Dict[str, tuple] = {
    "cpu_usage": (0, 100),
    "memory_usage": (0, 100),
    "disk_usage": (0, 100),
    "error_rate": (0, 1),
    "latency_ms": (0, float("inf")),
    "uptime_seconds": (0, float("inf")),
    "io_wait": (0, float("inf")),
    "temperature_celsius": (-20, 150),
}


def _validate_entry(entry: Dict[str, Any]) -> str | None:
    """Retourne un message d'erreur si l'entrée est invalide, sinon None."""
    missing = [f for f in REQUIRED_FIELDS if f not in entry]
    if missing:
        return f"Entrée ignorée, champs manquants: {missing}"

    invalid = [f for f, expected in FIELD_TYPES.items() if not isinstance(entry[f], expected)]
    if invalid:
        return f"Entrée ignorée, champs de type invalide: {invalid}"

    # S'applique aussi aux métriques optionnelles (disk_usage, io_wait,
    # temperature_celsius) uniquement si elles sont présentes et numériques,
    # pour ne pas interférer avec la tolérance déjà en place sur les champs
    # optionnels.
    out_of_range = [
        f
        for f, (low, high) in FIELD_RANGES.items()
        if f in entry and isinstance(entry[f], (int, float)) and not (low <= entry[f] <= high)
    ]
    if out_of_range:
        return f"Entrée ignorée, valeurs hors plage: {out_of_range}"

    return None


def ingestion_node(state: PipelineState) -> PipelineState:
    raw_input = state.get("raw_logs", [])

    valid_entries = []
    errors = []

    for entry in raw_input:
        error = _validate_entry(entry)
        if error:
            errors.append(error)
            continue
        valid_entries.append(entry)

    return {
        "raw_logs": valid_entries,
        "ingestion_errors": errors,
    }
