"""Manual check: launch the real MCP server over stdio and call every tool.

    python -m askauto.mcp_server.probe

A stand-in for MCP Inspector when Node.js is not installed. Read the output:
it is exactly what the agent will receive.
"""

import asyncio
import json
import sys
from typing import Any

from mcp import Client, StdioServerParameters

RUN_SQL_CASES = [
    ("valid join", "SELECT region, COUNT(*) AS units FROM sales "
                   "JOIN dealers USING (dealer_id) GROUP BY region ORDER BY units DESC"),
    ("trap 1: net revenue", "SELECT SUM(sale_price - discount_amount) AS net_revenue FROM sales"),
    ("trap 2: wrong spelling -> 0 rows, not an error",
     "SELECT COUNT(*) AS n FROM sales JOIN dealers USING (dealer_id) "
     "WHERE region = 'North East' GROUP BY region"),
    ("rejected: meta table", "SELECT * FROM meta_glossary"),
    ("rejected: write", "DROP TABLE sales"),
    ("rejected: unknown column", "SELECT revenue FROM sales"),
]


def show(title: str, payload: Any) -> None:
    print(f"\n=== {title}")
    print(json.dumps(payload, indent=2, default=str)[:1500])


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "askauto.mcp_server"])
    async with Client(params) as client:
        tools = await client.list_tools()
        print("Tools:", ", ".join(t.name for t in tools.tools))

        schema = (await client.call_tool("get_schema_context", {})).structured_content
        show("get_schema_context (summary)", {
            "tables": [t["name"] for t in schema["tables"]],
            "dealers.region allowed_values": next(
                c["allowed_values"] for t in schema["tables"] if t["name"] == "dealers"
                for c in t["columns"] if c["name"] == "region"),
            "glossary": {g["term"]: g["sql_expression"] for g in schema["glossary"]},
            "unanswerable": [u["topic"] for u in schema["unanswerable"]],
        })
        show("list_tables", (await client.call_tool("list_tables", {})).structured_content)
        show("sample_rows(sales, 3)",
             (await client.call_tool("sample_rows", {"table": "sales", "n": 3}))
             .structured_content)
        for title, sql in RUN_SQL_CASES:
            result = (await client.call_tool("run_sql", {"sql": sql})).structured_content
            show(f"run_sql - {title}", {k: result[k] for k in
                                       ("ok", "sql", "rows", "row_count", "error")})


if __name__ == "__main__":
    asyncio.run(main())
