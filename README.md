# Optimisation de l'Infrastructure Technique — Pipeline LangGraph

Pipeline modulaire qui ingère des logs d'infrastructure (JSON), détecte des
anomalies techniques et génère un rapport de recommandations d'optimisation,
au format JSON demandé dans l'énoncé.

## Sommaire

- [Réponse à l'énoncé](#réponse-à-lénoncé)
- [Architecture générale](#architecture-générale)
- [Détail du pipeline, nœud par nœud](#détail-du-pipeline-nœud-par-nœud)
- [Choix techniques et justifications](#choix-techniques-et-justifications)
- [Conformité au schéma de sortie](#conformité-au-schéma-de-sortie)
- [Installation](#installation)
- [Utilisation](#utilisation)
- [Structure du projet](#structure-du-projet)
- [Limites connues et pistes d'extension](#limites-connues-et-pistes-dextension)

---

## Réponse à l'énoncé

| Exigence de l'énoncé | Implémentation |
|---|---|
| Ingestion et analyse de données techniques (logs JSON) | Nœud `ingestion` : validation + normalisation (`src/nodes/ingestion.py`) |
| Détection d'anomalies (CPU, latence, etc.) | Nœud `analysis` : seuils documentés par métrique (`src/nodes/analysis.py`) |
| Génération de recommandations concrètes | Nœud `recommendation` : mapping règles + enrichissement LLM optionnel (`src/nodes/recommendation.py`) |
| Architecture multi-nœuds | `StateGraph` LangGraph à 3 nœuds séquentiels (`src/graph.py`) |
| Documentation des choix techniques | Ce README + docstrings/commentaires dans chaque module |
| JSON de sortie conforme au schéma fourni | Voir [Conformité au schéma de sortie](#conformité-au-schéma-de-sortie) |

## Architecture générale

```
input.json (1 log ou tableau de logs)
        │
        ▼
┌─────────────┐     ┌──────────────┐     ┌──────────────────────┐
│  ingestion  │ ──▶ │   analysis   │ ──▶ │    recommendation     │ ──▶ output.json
│ (validation │     │ (insights +  │     │ (actions concrètes +  │
│  + filtre)  │     │  anomalies)  │     │  enrichissement LLM)   │
└─────────────┘     └──────────────┘     └──────────────────────┘
```

Implémenté avec **LangGraph** (`StateGraph`) : chaque nœud est une fonction
pure `state -> state partiel`, et le graphe orchestre l'enchaînement de
façon déclarative. Un `TypedDict` (`src/state.py`, `PipelineState`) définit
le state partagé et transmis entre les nœuds — pas de variable globale, pas
d'effet de bord caché.

Le graphe est **linéaire** (pas de branchement conditionnel) car les trois
étapes s'exécutent toujours dans le même ordre pour ce cas d'usage. LangGraph
reste pertinent malgré tout : il rend le pipeline **inspectable** (chaque
étape et son contrat de données sont explicites) et **facilement extensible**
sans réécrire l'orchestration (cf. [pistes d'extension](#limites-connues-et-pistes-dextension)).

## Détail du pipeline, nœud par nœud

### 1. `ingestion_node` (`src/nodes/ingestion.py`)

**Entrée** : `raw_logs` — un objet log unique ou un tableau d'objets (normalisé
en liste dès `main.py`).

**Rôle** :
1. Vérifie que les champs indispensables sont présents (`REQUIRED_FIELDS` :
   `timestamp`, `cpu_usage`, `memory_usage`, `latency_ms`, `error_rate`,
   `uptime_seconds`, `service_status`).
2. Vérifie le **type** de chaque champ (`FIELD_TYPES`) — évite qu'un
   `cpu_usage` fourni en chaîne de caractères fasse planter les comparaisons
   numériques du nœud suivant.
3. Vérifie que chaque métrique reste dans une **plage physiquement plausible**
   (`FIELD_RANGES`, ex : `cpu_usage` entre 0 et 100) — une valeur hors plage
   trahit une donnée corrompue plutôt qu'une vraie mesure.
4. Toute entrée invalide est **écartée** (pas de crash du pipeline) et
   consignée dans `ingestion_errors`, affiché en fin d'exécution.

**Sortie** : `raw_logs` filtré (entrées valides uniquement) + `ingestion_errors`.

### 2. `analysis_node` (`src/nodes/analysis.py`)

**Entrée** : `raw_logs` validés par le nœud précédent.

**Rôle** :
1. Calcule des **insights agrégés** sur l'ensemble de la fenêtre de logs :
   latence moyenne, pics CPU/mémoire, taux d'erreur moyen, uptime max.
2. Détecte les **anomalies** en comparant chaque métrique de chaque entrée à
   des seuils fixes (`THRESHOLDS`), avec deux niveaux de sévérité
   (`medium`/`high`) — voir la [table des seuils](#table-des-seuils-de-détection).
3. Détecte les anomalies de **statut de service** (`degraded` → medium,
   `offline` → high) séparément, car ce ne sont pas des métriques numériques
   à la base (encodage documenté dans le code, `SERVICE_STATUS_LEVEL`).
4. Construit le `service_status_summary` (services en ligne / dégradés /
   hors ligne) à partir du **dernier relevé** de la fenêtre (l'état courant,
   pas un historique).

**Sortie** : `insights`, `anomalies` (liste brute, non dédupliquée),
`service_status_summary`.

Choix volontaire : **aucun appel LLM à cette étape**. Comparer un chiffre à
un seuil est déterministe ; un LLM n'apporterait ici que de la latence, du
coût, et un risque d'hallucination sur des données chiffrées — l'inverse de
ce qu'on veut pour un rapport d'infra fiable.

### 3. `recommendation_node` (`src/nodes/recommendation.py`)

**Entrée** : `anomalies` (issues du nœud précédent).

**Rôle** :
1. **Déduplique par métrique** : sur une fenêtre de plusieurs relevés, la
   même métrique peut être en anomalie sur plusieurs entrées consécutives
   (ex : CPU élevé 5 fois de suite). On ne garde qu'une seule occurrence par
   métrique — la plus sévère — pour éviter un rapport bruité de doublons.
2. Génère une recommandation **rule-based** pour chaque anomalie dédupliquée,
   via un mapping métrique → action (`ACTION_CATALOG`) : action concrète,
   cible technique (`target`), paramètres, et une estimation de bénéfice par
   défaut. Ce mapping garantit une réponse fiable et déterministe même sans
   accès à un LLM.
3. **Enrichissement optionnel par LLM** (Claude, via `ANTHROPIC_API_KEY`) :
   si une clé API est disponible, le nœud demande à Claude de reformuler le
   `benefit_estimate` en langage plus naturel et contextualisé. Stratégie en
   deux temps :
   - tentative n°1 : sortie **structurée** (tool use / function calling) —
     le schéma JSON est garanti par le modèle, pas de parsing fragile ;
   - repli (tentative n°2) : prompt texte + parsing JSON manuel, si la
     sortie structurée échoue pour une raison quelconque.
   - si les deux échouent (pas de clé, quota épuisé, réseau...), le pipeline
     **continue silencieusement** avec les `benefit_estimate` rule-based.
     Le LLM est un "plus", jamais une dépendance bloquante.

**Sortie** : `recommendations` (liste finale, une par métrique en anomalie).

## Choix techniques et justifications

- **Langage : Python** — écosystème mature pour LangGraph, le traitement de
  données JSON, et le SDK Anthropic.
- **LangGraph / `StateGraph`** — structure le pipeline en étapes explicites
  et testables indépendamment, plutôt qu'un script monolithique ; facilite
  l'ajout d'une étape (alerte, branchement, persistance...) sans réécrire
  l'existant.
- **`TypedDict` plutôt que Pydantic pour le state** (`src/state.py`) — le
  state ne fait que transiter entre 3 nœuds séquentiels internes, sans besoin
  de validation avancée à ce niveau (la validation du JSON d'entrée est déjà
  faite explicitement et de façon plus fine dans le nœud d'ingestion).
- **Rule-based pour la détection d'anomalies, hybride (règles + LLM) pour les
  recommandations** — le LLM est réservé à l'étape où la génération de texte
  apporte une vraie valeur (reformulation), jamais pour des comparaisons
  numériques ou des décisions binaires.
- **Best-effort LLM, jamais bloquant** — le pipeline produit un JSON valide et
  complet avec ou sans clé API, avec ou sans connexion réseau. Reproductible
  par défaut (mode rule-based pur), enrichi seulement si l'IA est disponible.
- **Tolérance à l'ingestion** — une entrée corrompue est écartée et loguée
  plutôt que de faire planter tout le pipeline sur un rapport de plusieurs
  centaines de logs.

## Conformité au schéma de sortie

Le JSON produit par `main.py` respecte exactement le schéma générique fourni
dans l'énoncé :

```jsonc
{
  "timestamp": "string (ISO 8601)",       // heure de génération du rapport (UTC)
  "insights": {
    "average_latency_ms": "number",
    "max_cpu_usage": "number",
    "max_memory_usage": "number",
    "error_rate": "number",
    "uptime_seconds": "number"
  },
  "anomalies": [
    {
      "metric": "string",
      "value": "number",
      "threshold": "number",
      "severity": "string (low|medium|high)",
      "description": "string"
    }
  ],
  "recommendations": [
    {
      "id": "string",
      "action": "string",
      "target": "string",
      "parameters": "object",
      "benefit_estimate": "string"
    }
  ],
  "service_status_summary": {
    "online": ["string"],
    "degraded": ["string"],
    "offline": ["string"]
  }
}
```

Note sur `severity` : le pipeline ne génère jamais `"low"`. Volontairement —
en dessous du seuil `medium`, la valeur est jugée normale et n'est pas
remontée comme anomalie (sinon chaque métrique de chaque log ferait remonter
une entrée, noyant le signal utile dans le rapport). `"low"` reste une valeur
valide du schéma, simplement non utilisée par cette implémentation.

## Installation

```bash
pip install -r requirements.txt
```

L'appel au LLM est **optionnel**. Pour l'activer, créez un fichier `.env` à
la racine du projet :

```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

`.env` est chargé automatiquement au démarrage (`python-dotenv`, voir
`src/main.py`) et est listé dans `.gitignore` — il ne sera donc jamais
commité. Sans clé (ou sans fichier `.env`), le pipeline fonctionne à
l'identique en mode rule-based pur.

## Utilisation

```bash
python -m src.main <input.json> [output.json]
```

`input.json` peut être un objet log unique ou un tableau d'objets (voir le
schéma d'entrée dans l'énoncé). Sans arguments, le script utilise
`sample_logs/rapport.json` en entrée et écrit dans `output/output.json`.

### Jeux de données fournis (`sample_logs/`)

| Fichier                      | Contenu                                              |
|------------------------------|-------------------------------------------------------|
| `example_from_subject.json`  | L'exemple exact fourni dans l'énoncé (1 relevé)       |
| `rapport.json`                | Fenêtre de 500 relevés réalistes, avec anomalies variées et un service hors ligne |

```bash
python -m src.main sample_logs/rapport.json output/output.json
```

## Structure du projet

```
src/
  state.py                   # TypedDict du state partagé (PipelineState)
  graph.py                   # Construction du StateGraph LangGraph
  main.py                    # Point d'entrée CLI
  nodes/
    ingestion.py              # Nœud 1 : validation des logs
    analysis.py                # Nœud 2 : insights + détection d'anomalies
    recommendation.py          # Nœud 3 : génération des recommandations
sample_logs/                 # Jeux de données de test
output/                      # Résultats générés (output.json)
```

### Table des seuils de détection

Seuils documentés en commentaire dans `src/nodes/analysis.py` :

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

## Limites connues et pistes d'extension

- Le résumé de statut de service (`service_status_summary`) reflète l'état
  du **dernier** relevé de la fenêtre, pas un historique agrégé — choix
  cohérent avec l'idée d'un "état courant" de l'infrastructure.
- Le champ `timestamp` en sortie est l'heure de **génération du rapport**
  (UTC, `datetime.now()`), pas dérivé des timestamps des logs analysés.
- Le graphe est volontairement linéaire ; LangGraph permet d'ajouter
  facilement, par exemple :
  - un nœud conditionnel qui route vers une alerte immédiate si une anomalie
    `high` est détectée (`add_conditional_edges`) ;
  - un nœud de persistance qui archive l'historique des rapports générés ;
  - un nœud de notification (email/Slack) déclenché sur seuil critique.
