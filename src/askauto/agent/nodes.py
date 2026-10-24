"""The graph nodes: the eight from the spec plus four deterministic terminals.

Each node takes the state, returns only the fields it changes, and appends one
trace entry. Nodes never raise for a business outcome (unanswerable, rejected
SQL, zero rows, unverified figure): they record it, and the edge functions in
graph.py decide what happens next. Data access is only through the MCP client.
"""

from askauto.agent.literals import check_literals
from askauto.agent.prompts import classify_messages, sql_messages, synthesize_messages
from askauto.agent.state import (
    AgentState,
    Answer,
    Classification,
    SQLAttempt,
    SQLPlan,
)
from askauto.agent.verify import describe, verify_answer
from askauto.mcp_client import McpTools
from askauto.mcp_server.models import SchemaContext
from askauto.mcp_server.sql_guard import guard_sql
from askauto.providers import LLMProvider

Update = dict[str, object]


class AgentNodes:
    def __init__(
        self,
        provider: LLMProvider,
        tools: McpTools,
        startup_schema: SchemaContext,
        max_rows: int = 500,
    ) -> None:
        """`startup_schema` is the bundle cached at startup, so classify needs no MCP call."""
        self.provider = provider
        self.tools = tools
        self.startup_schema = startup_schema
        self.max_rows = max_rows

    async def classify(self, state: AgentState) -> Update:
        result = await self.provider.complete(
            "classify", classify_messages(state.question, self.startup_schema), Classification
        )
        return {"classification": result,
                "trace": [f"classify: {result.label} - {result.reason}"]}

    async def load_context(self, state: AgentState) -> Update:
        context = await self.tools.get_schema_context(state.question)
        return {"schema_context": context,
                "trace": [f"load_context: {len(context.tables)} tables, "
                          f"{len(context.glossary)} glossary terms (mode={context.mode})"]}

    async def generate_sql(self, state: AgentState) -> Update:
        failed = [a for a in state.sql_attempts if a.error]
        plan = await self.provider.complete(
            "generate_sql", sql_messages(state.question, state.schema_context, failed), SQLPlan
        )
        attempts = [*state.sql_attempts, SQLAttempt(plan=plan)]
        return {"sql_attempts": attempts, "retry_count": len(attempts) - 1,
                "trace": [f"generate_sql: attempt {len(attempts)}"]}

    async def validate(self, state: AgentState) -> Update:
        attempt = state.current_attempt
        guarded = guard_sql(attempt.plan.sql, state.schema_context.catalog(), self.max_rows)
        reason = guarded.reason if not guarded.ok else check_literals(
            attempt.plan.sql, state.schema_context)
        error = f"rejected by validator: {reason}" if reason else None
        updated = attempt.model_copy(update={"guard_ok": error is None, "error": error})
        return {"sql_attempts": [*state.sql_attempts[:-1], updated],
                "trace": [f"validate: {error or 'ok'}"]}

    async def execute(self, state: AgentState) -> Update:
        attempt = state.current_attempt
        result = await self.tools.run_sql(attempt.plan.sql, limit=self.max_rows)
        error = None if result.ok else f"{result.error.kind}: {result.error.message}"
        updated = attempt.model_copy(update={"error": error})
        note = f"{result.row_count} rows" if result.ok else error
        return {"result": result, "sql_attempts": [*state.sql_attempts[:-1], updated],
                "trace": [f"execute: {note}"]}

    async def inspect(self, state: AgentState) -> Update:
        result = state.result
        if not result.ok:
            outcome = "error"
        elif result.row_count == 0:
            outcome = "zero_rows"
        else:
            outcome = "rows"
        return {"outcome": outcome, "trace": [f"inspect: {outcome}"]}

    async def synthesize(self, state: AgentState) -> Update:
        previous = state.verification
        unsupported = previous.unsupported if previous and not previous.ok else []
        answer = await self.provider.complete(
            "synthesize",
            synthesize_messages(state.question, state.current_attempt.plan, state.result,
                                unsupported),
            Answer,
        )
        retries = state.synth_retry_count + (1 if state.answer is not None else 0)
        return {"answer": answer, "synth_retry_count": retries,
                "trace": [f"synthesize: {len(answer.text)} chars"]}

    async def verify(self, state: AgentState) -> Update:
        plan = state.current_attempt.plan
        verification = verify_answer(
            state.answer.text, state.result,
            question=state.question, assumptions=plan.assumptions, sql=plan.sql,
        )
        update: Update = {"verification": verification,
                          "trace": [f"verify: {describe(verification)}"]}
        if verification.ok:
            update["status"] = "answered"
        return update

    # --- Terminal nodes: deterministic, no LLM ---------------------------------

    async def refuse(self, state: AgentState) -> Update:
        """Name what is missing: the classifier's reason plus any matching known gap."""
        reasons = [u.reason for u in self.startup_schema.unanswerable
                   if _mentions(state.question, u.topic)]
        text = " ".join(["I can't answer that from this data.",
                         state.classification.reason, *reasons])
        return {"answer": Answer(text=text), "status": "refused",
                "trace": [f"refuse: {len(reasons)} known gap(s) matched"]}

    async def clarify(self, state: AgentState) -> Update:
        classification = state.classification
        text = classification.clarifying_question or (
            f"Could you clarify the question? {classification.reason}")
        return {"answer": Answer(text=text), "status": "clarification_needed",
                "trace": ["clarify: asked the user to clarify"]}

    async def give_up(self, state: AgentState) -> Update:
        attempts = len(state.sql_attempts)
        text = (f"I couldn't build a working query for this question after {attempts} "
                f"attempts. The last attempt failed: {state.current_attempt.error}")
        return {"answer": Answer(text=text), "status": "failed",
                "trace": [f"give_up: {attempts} SQL attempts failed"]}

    async def add_caveat(self, state: AgentState) -> Update:
        figures = ", ".join(state.verification.unsupported)
        text = (f"{state.answer.text}\n\nNote: these figures could not be verified "
                f"against the query results: {figures}.")
        return {"answer": Answer(text=text), "status": "unverified",
                "trace": [f"add_caveat: unverified {figures}"]}


def _mentions(question: str, topic: str) -> bool:
    """True if the question uses a significant word of the topic ('credit scores')."""
    text = question.lower()
    return any(word.rstrip("s") in text for word in topic.lower().split() if len(word) > 3)
