"""Graph wiring and the named edge functions.

Forward path: classify -> load_context -> generate_sql -> validate -> execute ->
inspect -> synthesize -> verify. The edges below are the reason LangGraph is
here: three go backwards, two go sideways. Retry ceilings live in these edge
functions, never in the nodes.

    validate fails     -> generate_sql   (shares MAX_SQL_ATTEMPTS)
    execute errors     -> generate_sql   (shares MAX_SQL_ATTEMPTS)
    verify fails       -> synthesize     (MAX_RESYNTHESIS)
    classify           -> refuse | clarify
"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from askauto.agent.nodes import AgentNodes
from askauto.agent.state import AgentState

MAX_SQL_ATTEMPTS = 3  # total SQL generations per question (user decision)
MAX_RESYNTHESIS = 1  # extra synthesize calls after a failed verify

NODE_ORDER = ("classify", "load_context", "generate_sql", "validate",
              "execute", "inspect", "synthesize", "verify")
TERMINAL_NODES = ("refuse", "clarify", "give_up", "add_caveat")


def after_classify(state: AgentState) -> str:
    return {"answerable": "load_context", "ambiguous": "clarify",
            "unanswerable": "refuse"}[state.classification.label]


def retry_sql_or_give_up(state: AgentState) -> str:
    return "generate_sql" if len(state.sql_attempts) < MAX_SQL_ATTEMPTS else "give_up"


def after_validate(state: AgentState) -> str:
    return "execute" if state.current_attempt.guard_ok else retry_sql_or_give_up(state)


def after_inspect(state: AgentState) -> str:
    # Zero rows is an answer to explain, not an error to retry.
    if state.outcome in ("rows", "zero_rows"):
        return "synthesize"
    return retry_sql_or_give_up(state)


def after_verify(state: AgentState) -> str:
    if state.verification.ok:
        return END
    return "synthesize" if state.synth_retry_count < MAX_RESYNTHESIS else "add_caveat"


def build_graph(nodes: AgentNodes) -> CompiledStateGraph:
    graph = StateGraph(AgentState)
    for name in (*NODE_ORDER, *TERMINAL_NODES):
        graph.add_node(name, getattr(nodes, name))

    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", after_classify,
                                ["load_context", "clarify", "refuse"])
    graph.add_edge("load_context", "generate_sql")
    graph.add_edge("generate_sql", "validate")
    graph.add_conditional_edges("validate", after_validate,
                                ["execute", "generate_sql", "give_up"])
    graph.add_edge("execute", "inspect")
    graph.add_conditional_edges("inspect", after_inspect,
                                ["synthesize", "generate_sql", "give_up"])
    graph.add_edge("synthesize", "verify")
    graph.add_conditional_edges("verify", after_verify, [END, "synthesize", "add_caveat"])
    for name in TERMINAL_NODES:
        graph.add_edge(name, END)
    return graph.compile()


async def run_agent(graph: CompiledStateGraph, question: str) -> AgentState:
    return AgentState.model_validate(await graph.ainvoke(AgentState(question=question)))
