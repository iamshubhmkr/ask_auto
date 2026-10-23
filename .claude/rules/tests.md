---
paths:
  - "tests/**/*.py"
---

# Testing rules

- pytest-asyncio for async tests. No event loop juggling by hand.
- MCP server tests run against a temp database built by the build scripts.
- Agent tests mock the LLM provider. Never call Groq in a unit test.
- Every planted trap in the spec gets a regression test.
