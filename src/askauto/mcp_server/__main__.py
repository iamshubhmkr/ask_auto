"""Run the MCP server over stdio: python -m askauto.mcp_server"""

from askauto.mcp_server.server import create_server


def main() -> None:
    create_server().run()


if __name__ == "__main__":
    main()
