# Optimisation de l'Infrastructure Technique — Pipeline LangGraph

Pipeline modulaire à 3 nœuds qui analyse des logs d'infrastructure, détecte
des anomalies et génère des recommandations d'optimisation au format JSON.

## Architecture

```
raw_logs.json
      │
      ▼
┌─────────────┐     ┌──────────────┐     ┌────────────────────┐
│  ingestion  │ ──▶ │   analysis   │ ──▶ │   recommendation    │ ──▶ output.json
│  (validation)│    │ (insights +  │     │ (actions concrètes) │
│              │    │  anomalies)  │     │                      │
└─────────────┘     └──────────────┘     └────────────────────┘
```

Implémenté avec **LangGraph** (`StateGraph`) : chaque étape est un nœud pur
(fonction `state -> state partiel`), le graphe orchestre l'enchaînement.
Un `TypedDict` (`src/state.py`) définit le state partagé entre les nœuds.

## Choix techniques

- **Langage** : Python — écosystème mature pour LangGraph et le traitement
  de données JSON.
- **Détection d'anomalies "rule-based" (seuils)**, pas de LLM à cette étape :
  comparer un chiffre à un seuil est une opération déterministe. Un LLM
  n'apporterait ici que de la latence, du coût, et un risque
  d'hallucination sur des données chiffrées — l'inverse de ce qu'on veut
  pour un rapport d'infra fiable. Les seuils choisis sont documentés en
  commentaire dans `src/nodes/analysis.py`.
- **Recommandations hybrides** : un mapping métrique → action garantit une
  réponse fiable et déterministe pour chaque anomalie connue (fallback
  toujours disponible). Si une clé `ANTHROPIC_API_KEY` est présente dans
  l'environnement, le nœud appelle Claude pour reformuler le
  `benefit_estimate` en langage plus naturel — c'est un enrichissement
  "best-effort", jamais une dépendance bloquante : le pipeline fonctionne
  à l'identique sans elle.
- **Déduplication par métrique** : sur une fenêtre de plusieurs relevés,
  une même métrique peut être en anomalie sur plusieurs entrées
  consécutives (ex: CPU élevé 5 fois de suite). On ne génère qu'une seule
  recommandation par métrique (en gardant l'occurrence la plus sévère)
  pour éviter un rapport bruité de doublons.
- **Tolérance à l'ingestion** : une entrée de log avec des champs
  obligatoires manquants est écartée (et loguée dans
  `ingestion_errors`) plutôt que de faire planter tout le pipeline.

## Installation

```bash
pip install -r requirements.txt
```

L'appel au LLM est optionnel. Pour l'activer, créez un fichier `.env` à la
racine du projet (copiez `.env.example`) :

```bash
cp .env.example .env
# puis éditez .env et renseignez ANTHROPIC_API_KEY=sk-ant-...
```

`.env` est chargé automatiquement au démarrage (`python-dotenv`) et est
listé dans `.gitignore` — il ne sera donc jamais commité. Sans clé (ou
sans fichier `.env`), le pipeline fonctionne à l'identique en mode
rule-based pur.

## Utilisation

```bash
python -m src.main <input.json> [output.json]
```

`input.json` peut être un objet unique ou un tableau d'objets log (voir le
schéma d'entrée dans l'énoncé). Sans arguments, le script utilise
`sample_logs/example_critical.json` par défaut.

### Exemples fournis (`sample_logs/`)

| Fichier                        | Contenu                                              |
|---------------------------------|-------------------------------------------------------|
| `example_healthy.json`          | 1 relevé, aucune anomalie                             |
| `example_from_subject.json`     | L'exemple exact fourni dans l'énoncé (anomalies modérées) |
| `example_critical.json`         | 2 relevés, anomalies critiques + service offline       |

```bash
python -m src.main sample_logs/example_critical.json output/output.json
```

## Structure du projet

```
src/
  state.py                  # TypedDict du state partagé (PipelineState)
  graph.py                  # Construction du StateGraph LangGraph
  main.py                   # Point d'entrée CLI
  nodes/
    ingestion.py             # Nœud 1 : validation des logs
    analysis.py               # Nœud 2 : insights + détection d'anomalies
    recommendation.py         # Nœud 3 : génération des recommandations
sample_logs/                # Jeux de données de test
output/                     # Résultats générés
```

## Seuils de détection (documentés dans analysis.py)

| Métrique              | Medium | High |
|------------------------|--------|------|
| cpu_usage (%)          | 65     | 80   |
| memory_usage (%)       | 70     | 85   |
| latency_ms             | 150    | 300  |
| error_rate             | 0.01   | 0.05 |
| disk_usage (%)         | 75     | 90   |
| io_wait                | 10     | 20   |
| temperature_celsius    | 65     | 75   |

Services : `degraded` → medium, `offline` → high.

## Piste d'extension (pour l'entretien final)

Le graphe est volontairement linéaire mais LangGraph permet d'ajouter
facilement, par exemple :
- un nœud conditionnel qui route vers une alerte immédiate si une
  anomalie `high` est détectée (`add_conditional_edges`) ;
- un nœud de persistance qui archive l'historique des rapports générés ;
- un nœud de notification (email/Slack) déclenché sur seuil critique.
