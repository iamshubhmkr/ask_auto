"""Graph wiring and the named edge functions.

Session 5 is the happy path: classify -> load_context -> generate_sql ->
validate -> execute -> inspect -> synthesize -> verify. Any other outcome makes
its edge function raise AgentError (fail loudly). Session 6 replaces those
raises with the refuse / clarify / retry routes; the nodes do not change.
"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from askauto.agent.nodes import AgentNodes
from askauto.agent.state import AgentState

NODE_ORDER = ("classify", "load_context", "generate_sql", "validate",
              "execute", "inspect", "synthesize", "verify")


class AgentError(RuntimeError):
    """The graph stopped on an outcome it does not handle yet. Carries the state."""

    def __init__(self, message: str, state: AgentState) -> None:
        super().__init__(message)
        self.state = state


def after_classify(state: AgentState) -> str:
    classification = state.classification
    if classification.label == "answerable":
        return "load_context"
    raise AgentError(f"classified as {classification.label}: {classification.reason} "
                     "(refuse/clarify routes arrive in session 6)", state)


def after_validate(state: AgentState) -> str:
    attempt = state.current_attempt
    if attempt.guard_ok:
        return "execute"
    raise AgentError(f"generated SQL failed validation: {attempt.error}", state)


def after_inspect(state: AgentState) -> str:
    if state.outcome == "rows":
        return "synthesize"
    detail = state.current_attempt.error if state.outcome == "error" else "query returned 0 rows"
    raise AgentError(f"inspect outcome '{state.outcome}': {detail}", state)


def after_verify(state: AgentState) -> str:
    if state.verification.ok:
        return END
    raise AgentError("answer contains figures not found in the rows: "
                     f"{', '.join(state.verification.unsupported)}", state)


def build_graph(nodes: AgentNodes) -> CompiledStateGraph:
    graph = StateGraph(AgentState)
    for name in NODE_ORDER:
        graph.add_node(name, getattr(nodes, name))

    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", after_classify, ["load_context"])
    graph.add_edge("load_context", "generate_sql")
    graph.add_edge("generate_sql", "validate")
    graph.add_conditional_edges("validate", after_validate, ["execute"])
    graph.add_edge("execute", "inspect")
    graph.add_conditional_edges("inspect", after_inspect, ["synthesize"])
    graph.add_edge("synthesize", "verify")
    graph.add_conditional_edges("verify", after_verify, [END])
    return graph.compile()


async def run_agent(graph: CompiledStateGraph, question: str) -> AgentState:
    return AgentState.model_validate(await graph.ainvoke(AgentState(question=question)))
