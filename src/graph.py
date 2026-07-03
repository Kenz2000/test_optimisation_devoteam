"""
Construction du pipeline LangGraph : 3 nœuds séquentiels.

    ingestion_node -> analysis_node -> recommendation_node

On utilise un StateGraph linéaire simple. LangGraph est pertinent ici
même sans branchement conditionnel car il donne une structure claire,
inspectable et facilement extensible (ex: ajouter un nœud d'alerte,
un nœud de branchement selon la sévérité, etc. — cf. la partie
"fonctionnalité additionnelle" de l'entretien final).
"""

from langgraph.graph import StateGraph, END

from src.state import PipelineState
from src.nodes.ingestion import ingestion_node
from src.nodes.analysis import analysis_node
from src.nodes.recommendation import recommendation_node


def build_graph():
    graph = StateGraph(PipelineState)

    graph.add_node("ingestion", ingestion_node)
    graph.add_node("analysis", analysis_node)
    graph.add_node("recommendation", recommendation_node)

    graph.set_entry_point("ingestion")
    graph.add_edge("ingestion", "analysis")
    graph.add_edge("analysis", "recommendation")
    graph.add_edge("recommendation", END)

    return graph.compile()
