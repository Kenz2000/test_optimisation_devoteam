"""
Définition du state partagé entre les nœuds du graphe LangGraph.

Choix technique : on utilise un TypedDict simple plutôt qu'un objet Pydantic
complexe, car le state ne fait que transiter entre 3 nœuds séquentiels et
n'a pas besoin de validation avancée à ce stade (la validation du JSON
d'entrée est faite explicitement dans le nœud d'ingestion).
"""

from typing import TypedDict, List, Dict, Any, Optional


class RawLog(TypedDict, total=False):
    timestamp: str
    cpu_usage: float
    memory_usage: float
    latency_ms: float
    disk_usage: float
    network_in_kbps: float
    network_out_kbps: float
    io_wait: float
    thread_count: int
    active_connections: int
    error_rate: float
    uptime_seconds: int
    temperature_celsius: float
    power_consumption_watts: float
    service_status: Dict[str, str]


class PipelineState(TypedDict, total=False):
    # --- Entrée / ingestion ---
    raw_logs: List[RawLog]          # logs bruts chargés depuis le fichier JSON
    ingestion_errors: List[str]     # erreurs de validation éventuelles

    # --- Sortie de l'étape d'analyse ---
    insights: Dict[str, Any]
    anomalies: List[Dict[str, Any]]
    service_status_summary: Dict[str, List[str]]

    # --- Sortie de l'étape de recommandation ---
    recommendations: List[Dict[str, Any]]

    # --- Sortie finale ---
    output: Dict[str, Any]
