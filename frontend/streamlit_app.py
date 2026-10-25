"""AskAuto web client: a thin Streamlit page over the API.

    python -m uvicorn askauto.api:app --port 8000          # terminal 1: the API
    python -m streamlit run frontend/streamlit_app.py      # terminal 2: this page

A separate process from the API. It knows only the HTTP contract (POST /ask,
GET /health) and never imports askauto.
"""

import os
from typing import Any

import httpx
import streamlit as st

DEFAULT_API_URL = os.environ.get("ASKAUTO_API_URL", "http://localhost:8000")
TIMEOUT_S = 120.0
START_API = "python -m uvicorn askauto.api:app --port 8000"
EXAMPLES = [
    "What was our net revenue last year?",
    "Which three dealers had the highest net revenue in 2025?",
    "How many cars did the North East region sell in 2024?",
    "What is the average credit score of our buyers?",
    "Which dealer is the best?",
]


def escape_markdown(text: str) -> str:
    """Streamlit renders $...$ as LaTeX, which mangles '$52.2M ... $4.61M'."""
    return text.replace("$", "\\$")


def check_health(api_url: str) -> dict[str, Any] | None:
    try:
        response = httpx.get(f"{api_url}/health", timeout=5)
    except httpx.HTTPError:
        return None
    return response.json() if response.status_code == 200 else None


def ask(api_url: str, question: str) -> dict[str, Any]:
    """{"response": body} on success, {"error": message} otherwise."""
    try:
        response = httpx.post(f"{api_url}/ask", json={"question": question}, timeout=TIMEOUT_S)
    except httpx.ConnectError:
        return {"error": f"The API is not reachable at {api_url}. Start it with: {START_API}"}
    except httpx.TimeoutException:
        return {"error": f"The API did not answer within {TIMEOUT_S:.0f} s."}
    if response.status_code == 200:
        return {"response": response.json()}
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    return {"error": f"API error {response.status_code}: {detail}"}


def render_answer(entry: dict[str, Any]) -> None:
    if "error" in entry:
        st.error(entry["error"])
        return
    body = entry["response"]
    status = body["status"]
    text = escape_markdown(body["answer"])

    # 1. The answer, framed by how the run ended.
    if status == "answered":
        st.markdown(text)
    elif status in ("refused", "clarification_needed"):
        st.info(text)
    elif status == "unverified":
        st.warning(text)
    else:
        st.error(text)

    # 2. The SQL that produced every number, and the assumptions behind it.
    if body["sql"] or body["assumptions"]:
        with st.expander("SQL & assumptions"):
            if body["sql"]:
                st.code(body["sql"], language="sql")
            if body["assumptions"]:
                st.markdown("**Assumptions**\n" + "\n".join(
                    f"- {escape_markdown(a)}" for a in body["assumptions"]))
            if body["rows"]:
                st.dataframe(body["rows"])

    # 3. Footer: the path through the graph and how long it took.
    steps = " → ".join(step.split(":")[0] for step in body["trace"])
    st.caption(f"{status} · {body['latency_ms']} ms · {steps}")


def sidebar() -> str:
    with st.sidebar:
        api_url = st.text_input("API URL", DEFAULT_API_URL)
        health = check_health(api_url)
        if health:
            st.success(f"API connected · {health['model']}")
            st.caption(f"Tables: {', '.join(health['tables'])}")
        else:
            st.error("API not reachable. Start it with:")
            st.code(START_API, language="bash")
        st.subheader("Try a question")
        for i, example in enumerate(EXAMPLES):
            if st.button(example, key=f"example-{i}"):
                st.session_state["pending"] = example
        if st.button("Clear history"):
            st.session_state["history"] = []
    return api_url


def main() -> None:
    st.set_page_config(page_title="AskAuto", page_icon=":car:")
    st.title("AskAuto")
    st.caption("Ask about dealer sales in plain English. "
               "Every answer comes with the SQL that produced it.")
    api_url = sidebar()

    history: list[dict[str, Any]] = st.session_state.setdefault("history", [])
    for entry in history:
        with st.chat_message("user"):
            st.markdown(escape_markdown(entry["question"]))
        with st.chat_message("assistant"):
            render_answer(entry)

    question = st.chat_input("Ask about sales, dealers or vehicles")
    question = question or st.session_state.pop("pending", None)
    if question:
        with st.chat_message("user"):
            st.markdown(escape_markdown(question))
        with st.chat_message("assistant"):
            with st.spinner("Working out the SQL..."):
                entry = {"question": question, **ask(api_url, question)}
            render_answer(entry)
        history.append(entry)


main()
