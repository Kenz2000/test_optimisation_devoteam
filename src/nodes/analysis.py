"""
Nœud 2 : Détection d'Anomalies.

Rôle : calculer des insights agrégés sur l'ensemble des logs valides, et
détecter les anomalies en comparant chaque métrique à des seuils définis.

Choix technique : détection "rule-based" (à seuils), volontairement sans
LLM. Un LLM n'apporte aucune fiabilité supplémentaire pour comparer un
nombre à un seuil , il ajouterait de la latence, du coût, et un risque
d'hallucination sur des données chiffrées. Le LLM est réservé au nœud
suivant (recommandations), là où la génération de texte a de la valeur.

Seuils choisis (documentés) :
- cpu_usage      > 80 %      -> high, > 65 % -> medium
- memory_usage   > 85 %      -> high, > 70 % -> medium
- latency_ms     > 300 ms    -> high, > 150 ms -> medium
- error_rate     > 0.05      -> high, > 0.01 -> medium
- disk_usage     > 90 %      -> high, > 75 % -> medium
- io_wait        > 10        -> medium
- temperature_celsius > 75   -> high, > 65 -> medium
- service "degraded"         -> medium
- service "offline"          -> high
"""

from typing import Any, Dict, List
from src.state import PipelineState

THRESHOLDS = {
    "cpu_usage": {"medium": 65, "high": 80},
    "memory_usage": {"medium": 70, "high": 85},
    "latency_ms": {"medium": 150, "high": 300},
    "error_rate": {"medium": 0.01, "high": 0.05},
    "disk_usage": {"medium": 75, "high": 90},
    "io_wait": {"medium": 10, "high": 20},
    "temperature_celsius": {"medium": 65, "high": 75},
}

# Encodage numérique du statut de service. Le schéma d'anomalie déclare
# `value`/`threshold` comme `number` pour toutes les anomalies (métriques
# incluses) ; un statut de service ("online"/"degraded"/"offline") n'est pas
# nativement numérique, donc on le fait rentrer dans ce schéma via un niveau
# de gravité ordonné (0 = nominal, 2 = pire cas) plutôt que de casser le
# contrat de type avec des strings.
SERVICE_STATUS_LEVEL = {"online": 0, "degraded": 1, "offline": 2}

METRIC_DESCRIPTIONS = {
    "cpu_usage": "Utilisation CPU élevée, risque de saturation du serveur.",
    "memory_usage": "Utilisation mémoire élevée, risque de swap ou d'OOM kill.",
    "latency_ms": "Latence anormalement élevée, impact direct sur l'expérience utilisateur.",
    "error_rate": "Taux d'erreur élevé, possible dysfonctionnement applicatif.",
    "disk_usage": "Espace disque proche de la saturation.",
    "io_wait": "Temps d'attente disque élevé, goulot d'étranglement I/O.",
    "temperature_celsius": "Température matérielle élevée, risque de throttling.",
}


def _severity_for(metric: str, value: float) -> str | None:
    # Pas de "low" : ici on ne classe pas la sévérité de toutes les valeurs,
    # on détecte des anomalies. En dessous du seuil "medium", la valeur est
    # jugée normale -> None, et l'anomalie n'est pas remontée (voir filtre
    # `if severity:` dans _detect_metric_anomalies). Un "low" ferait remonter
    # une entrée par métrique par log, noyant le signal envoyé au LLM de
    # recommandations en aval.
    bounds = THRESHOLDS.get(metric)
    if bounds is None:
        return None
    if value > bounds["high"]:
        return "high"
    if value > bounds["medium"]:
        return "medium"
    return None


def _detect_metric_anomalies(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    anomalies = []
    for metric, bounds in THRESHOLDS.items():
        value = entry.get(metric)
        if value is None:
            continue
        severity = _severity_for(metric, value)
        if severity:
            anomalies.append({
                "metric": metric,
                "value": value,
                "threshold": bounds["high"] if severity == "high" else bounds["medium"],
                "severity": severity,
                "description": METRIC_DESCRIPTIONS.get(metric, f"Valeur anormale pour {metric}."),
            })
    return anomalies


def _detect_service_anomalies(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    anomalies = []
    statuses = entry.get("service_status", {}) or {}
    for service, status in statuses.items():
        # `value`/`threshold` restent des `number` (SERVICE_STATUS_LEVEL),
        # conformes au schéma générique des anomalies. Le statut textuel
        # d'origine reste lisible dans `description`.
        if status == "offline":
            anomalies.append({
                "metric": f"service_status.{service}",
                "value": SERVICE_STATUS_LEVEL["offline"],
                "threshold": SERVICE_STATUS_LEVEL["online"],
                "severity": "high",
                "description": f"Le service '{service}' est hors ligne.",
            })
        elif status == "degraded":
            anomalies.append({
                "metric": f"service_status.{service}",
                "value": SERVICE_STATUS_LEVEL["degraded"],
                "threshold": SERVICE_STATUS_LEVEL["online"],
                "severity": "medium",
                "description": f"Le service '{service}' fonctionne en mode dégradé.",
            })
    return anomalies


def analysis_node(state: PipelineState) -> PipelineState:
    entries = state.get("raw_logs", [])

    if not entries:
        return {
            "insights": {},
            "anomalies": [],
            "service_status_summary": {"online": [], "degraded": [], "offline": []},
        }

    # --- Insights agrégés ---
    latencies = [e["latency_ms"] for e in entries]
    cpu_values = [e["cpu_usage"] for e in entries]
    memory_values = [e["memory_usage"] for e in entries]
    error_rates = [e["error_rate"] for e in entries]
    uptimes = [e["uptime_seconds"] for e in entries]

    insights = {
        "average_latency_ms": round(sum(latencies) / len(latencies), 2),
        "max_cpu_usage": max(cpu_values),
        "max_memory_usage": max(memory_values),
        "error_rate": round(sum(error_rates) / len(error_rates), 4),
        "uptime_seconds": max(uptimes),
    }

    # --- Anomalies (sur toutes les entrées) ---
    all_anomalies: List[Dict[str, Any]] = []
    for entry in entries:
        all_anomalies.extend(_detect_metric_anomalies(entry))
        all_anomalies.extend(_detect_service_anomalies(entry))

    # --- Résumé du statut des services (sur la dernière entrée = état courant) ---
    latest_entry = entries[-1]
    summary = {"online": [], "degraded": [], "offline": []}
    for service, status in (latest_entry.get("service_status", {}) or {}).items():
        if status in summary:
            summary[status].append(service)
        else:
            summary.setdefault(status, []).append(service)

    return {
        "insights": insights,
        "anomalies": all_anomalies,
        "service_status_summary": summary,
    }
