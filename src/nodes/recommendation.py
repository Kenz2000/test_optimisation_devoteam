"""
Nœud 3 : Génération de Recommandations.

Rôle : transformer chaque anomalie détectée en une action concrète et
actionnable, avec une estimation du bénéfice attendu.

Choix technique : approche hybride.
  - Un mapping "rule-based" garantit une recommandation fiable et
    structurée pour CHAQUE anomalie connue, même sans accès à un LLM
    (robustesse, coût nul, déterminisme — important pour un rapport
    d'infrastructure qu'on veut reproductible).
  - Si une clé API Anthropic est disponible (variable d'environnement
    ANTHROPIC_API_KEY), le nœud appelle Claude pour reformuler/enrichir
    le champ `benefit_estimate` en langage naturel plus contextualisé.
    Le LLM est donc un "plus", jamais une dépendance bloquante :
    le pipeline fonctionne à l'identique sans lui.
"""

import os
from typing import Any, Dict, List
from src.state import PipelineState

# Mapping métrique -> action de remédiation type
ACTION_CATALOG = {
    "cpu_usage": {
        "action": "Répartir la charge CPU (scaling horizontal ou vertical)",
        "target": "compute",
        "parameters": {"strategy": "autoscaling", "min_replicas": 2},
        "benefit_estimate": "Réduction attendue de 20-30% du taux d'utilisation CPU par instance.",
    },
    "memory_usage": {
        "action": "Augmenter la mémoire allouée ou identifier une fuite mémoire",
        "target": "compute",
        "parameters": {"strategy": "resize_or_profile"},
        "benefit_estimate": "Réduction du risque de swap et d'arrêt par OOM killer.",
    },
    "latency_ms": {
        "action": "Mettre en place ou renforcer un cache applicatif",
        "target": "api_gateway",
        "parameters": {"strategy": "caching", "ttl_seconds": 60},
        "benefit_estimate": "Réduction estimée de 30-50% de la latence moyenne.",
    },
    "error_rate": {
        "action": "Analyser les logs applicatifs pour isoler la source des erreurs",
        "target": "application",
        "parameters": {"strategy": "log_analysis"},
        "benefit_estimate": "Réduction du taux d'erreur et amélioration de la fiabilité perçue.",
    },
    "disk_usage": {
        "action": "Purger les données obsolètes ou étendre le stockage",
        "target": "storage",
        "parameters": {"strategy": "cleanup_or_resize"},
        "benefit_estimate": "Élimination du risque de saturation disque à court terme.",
    },
    "io_wait": {
        "action": "Migrer vers un stockage plus performant (SSD/NVMe)",
        "target": "storage",
        "parameters": {"strategy": "storage_upgrade"},
        "benefit_estimate": "Réduction du temps d'attente I/O et amélioration du débit global.",
    },
    "temperature_celsius": {
        "action": "Vérifier le refroidissement matériel ou répartir la charge",
        "target": "hardware",
        "parameters": {"strategy": "cooling_check"},
        "benefit_estimate": "Réduction du risque de throttling matériel.",
    },
}


def _service_recommendation(metric: str, service: str) -> Dict[str, Any]:
    return {
        "action": f"Redémarrer et investiguer le service '{service}'",
        "target": service,
        "parameters": {"strategy": "restart_and_investigate"},
        "benefit_estimate": f"Rétablissement de la disponibilité du service '{service}'.",
    }


def _build_recommendation(anomaly: Dict[str, Any], idx: int) -> Dict[str, Any]:
    metric = anomaly["metric"]

    if metric.startswith("service_status."):
        service = metric.split(".", 1)[1]
        template = _service_recommendation(metric, service)
    else:
        template = ACTION_CATALOG.get(metric, {
            "action": f"Investiguer la métrique '{metric}'",
            "target": "infrastructure",
            "parameters": {},
            "benefit_estimate": "Amélioration générale de la stabilité du système.",
        })

    return {
        "id": f"rec-{idx:03d}",
        "action": template["action"],
        "target": template["target"],
        "parameters": template["parameters"],
        "benefit_estimate": template["benefit_estimate"],
    }


def _enrich_with_llm_structured(client: Any, recommendations: List[Dict[str, Any]], actions_list: str) -> None:
    """Tentative n°1 : sortie structurée via tool use. Le schéma garantit un
    JSON conforme (pas de parsing manuel fragile). Lève une exception si ça
    échoue, pour laisser la main au repli texte."""
    tool = {
        "name": "submit_benefit_estimates",
        "description": "Soumet le bénéfice métier reformulé pour chaque action.",
        "input_schema": {
            "type": "object",
            "properties": {
                "estimates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "benefit": {"type": "string"},
                        },
                        "required": ["id", "benefit"],
                    },
                },
            },
            "required": ["estimates"],
        },
    }

    prompt = (
        "Tu es un ingénieur infra. Pour chaque action ci-dessous, reformule "
        "en une phrase concise et concrète le bénéfice métier attendu.\n\n"
        f"{actions_list}"
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        temperature=0.1,
        tools=[tool],
        tool_choice={"type": "tool", "name": "submit_benefit_estimates"},
        messages=[{"role": "user", "content": prompt}],
    )
    print("LLM structured response:", response)
    tool_use = next(b for b in response.content if b.type == "tool_use")
    enriched = {item["id"]: item["benefit"] for item in tool_use.input["estimates"]}

    for rec in recommendations:
        if rec["id"] in enriched:
            rec["benefit_estimate"] = enriched[rec["id"]]


def _enrich_with_llm_text(client: Any, recommendations: List[Dict[str, Any]], actions_list: str) -> None:
    """Repli (tentative n°2) : ancien prompt texte + parsing JSON manuel,
    utilisé seulement si la sortie structurée a échoué."""
    prompt = (
        "Tu es un ingénieur infra. Pour chaque action ci-dessous, reformule "
        "en une phrase concise et concrète le bénéfice métier attendu. "
        "Réponds uniquement en JSON: {\"id\": \"phrase\", ...}.\n\n"
        f"{actions_list}"
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        temperature=0.1,
        messages=[{"role": "user", "content": prompt}],
    )

    import json as _json
    text = "".join(b.text for b in response.content if hasattr(b, "text"))
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    enriched = _json.loads(text)

    for rec in recommendations:
        if rec["id"] in enriched:
            rec["benefit_estimate"] = enriched[rec["id"]]


def _enrich_with_llm(recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enrichit les benefit_estimate via Claude, si une clé API est disponible.
    Best-effort, à deux niveaux : on essaie d'abord une sortie structurée
    (tool use), plus fiable ; si ça échoue, on retombe sur l'ancien prompt
    texte + parsing JSON. Si les deux échouent (pas de clé, quota, réseau...),
    on garde silencieusement les recommandations rule-based telles quelles.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not recommendations:
        return recommendations

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        actions_list = "\n".join(f"- {r['id']}: {r['action']}" for r in recommendations)

        try:
            _enrich_with_llm_structured(client, recommendations, actions_list)
        except Exception:
            _enrich_with_llm_text(client, recommendations, actions_list)

    except Exception:
        # Best-effort uniquement : on ne casse jamais le pipeline pour l'IA.
        pass

    return recommendations


def _deduplicate_by_metric(anomalies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sur une fenêtre de logs, la même métrique peut être en anomalie sur
    plusieurs entrées consécutives (ex: CPU élevé sur 5 relevés d'affilée).
    On ne veut qu'UNE recommandation par métrique en anomalie, en gardant
    l'occurrence la plus sévère (et donc la plus représentative)."""
    best_by_metric: Dict[str, Dict[str, Any]] = {}
    severity_rank = {"low": 0, "medium": 1, "high": 2}

    for anomaly in anomalies:
        metric = anomaly["metric"]
        current = best_by_metric.get(metric)
        if current is None or severity_rank.get(anomaly["severity"], 0) > severity_rank.get(current["severity"], 0):
            best_by_metric[metric] = anomaly

    return list(best_by_metric.values())


def recommendation_node(state: PipelineState) -> PipelineState:
    anomalies = _deduplicate_by_metric(state.get("anomalies", []))

    recommendations = [
        _build_recommendation(anomaly, idx + 1)
        for idx, anomaly in enumerate(anomalies)
    ]

    recommendations = _enrich_with_llm(recommendations)

    return {"recommendations": recommendations}
