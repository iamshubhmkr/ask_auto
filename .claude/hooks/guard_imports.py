"""PreToolUse hook: block edits that break the import boundaries in CLAUDE.md.

Claude Code pipes the pending tool call to stdin as JSON. Exit code 2 blocks the
call and sends stderr back to Claude as the reason. Exit code 0 lets it through.

Scope: .py files under src/askauto/ (the runtime package). scripts/ and tests/
are build-time and deliberately not checked. Writes made through Bash are not
seen by this hook.
"""

import json
import os
import re
import sys
from pathlib import PurePosixPath

# (pattern, package dir where it is allowed, invariant text)
RULES: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"^\s*(?:from|import)\s+(?:[\w.]+\s*,\s*)*(sqlite3|aiosqlite)\b", re.M),
        "mcp_server",
        "Only the MCP server opens the database at runtime. No module may import "
        "sqlite3 or aiosqlite outside src/askauto/mcp_server/. "
        "Fetch data through the MCP client instead.",
    ),
    (
        re.compile(r"^\s*(?:from|import)\s+(?:[\w.]+\s*,\s*)*(groq)\b", re.M),
        "providers",
        "Provider calls go through the LLMProvider interface. Never import the Groq "
        "SDK outside src/askauto/providers/.",
    ),
]


def runtime_subpath(file_path: str, project_dir: str) -> PurePosixPath | None:
    """Return the path relative to src/askauto/, or None if outside it."""
    path = os.path.normcase(os.path.abspath(file_path))
    pkg = os.path.normcase(os.path.abspath(os.path.join(project_dir, "src", "askauto")))
    if os.path.commonpath([path, pkg]) != pkg:
        return None
    return PurePosixPath(os.path.relpath(path, pkg).replace(os.sep, "/"))


def main() -> int:
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    tool_input = payload.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    if not file_path.endswith(".py"):
        return 0

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd", ".")
    sub = runtime_subpath(file_path, project_dir)
    if sub is None:
        return 0

    text = tool_input.get("content") or tool_input.get("new_string") or ""
    for pattern, allowed_dir, invariant in RULES:
        match = pattern.search(text)
        if match and sub.parts[0] != allowed_dir:
            sys.stderr.write(
                f"BLOCKED by .claude/hooks/guard_imports.py: '{match.group(1)}' imported in "
                f"src/askauto/{sub}.\nInvariant (CLAUDE.md): {invariant}\n"
            )
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
