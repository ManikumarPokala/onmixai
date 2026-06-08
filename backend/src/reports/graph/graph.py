"""The fixed knowledge→report graph (ADR 0017). It is LINEAR with NO dynamic routing:
knowledge_agent → report_agent → END, every time. The only conditionality is a typed terminal
carried in the state (``error``) — node 2 passes through when node 1 declined — not a
model-decided next node. Dynamic multi-agent orchestration is V2. The graph is assembled once
per request (deps bound by ``functools.partial``) and run with ``ainvoke``; node errors are
typed terminal state, never exceptions escaping the graph.
"""

from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph

from src.ai.gateway import LLMGateway
from src.ai.prompt_registry import PromptRegistry
from src.identity.schemas import AuthContext
from src.reports.graph.nodes import knowledge_agent, report_agent
from src.reports.graph.state import ReportState
from src.shared.config import Settings


def build_report_graph(
    *,
    retriever: Any,
    gateway: LLMGateway,
    registry: PromptRegistry,
    actor: AuthContext,
    settings: Settings,
) -> Any:
    """Compile the fixed linear report graph with its dependencies bound. Returns a compiled
    LangGraph runnable; call ``await graph.ainvoke(initial_state)``."""
    graph: StateGraph = StateGraph(ReportState)
    graph.add_node(
        "knowledge_agent",
        partial(knowledge_agent, retriever=retriever, actor=actor, settings=settings),
    )
    graph.add_node(
        "report_agent",
        partial(report_agent, gateway=gateway, registry=registry, actor=actor),
    )
    graph.set_entry_point("knowledge_agent")
    graph.add_edge("knowledge_agent", "report_agent")  # linear, unconditional
    graph.add_edge("report_agent", END)
    return graph.compile()
