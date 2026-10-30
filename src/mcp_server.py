"""Standard Model Context Protocol (MCP) Server for Ubuntu Host management."""

from mcp.server.fastmcp import FastMCP
from . import tools

mcp = FastMCP("Adlos Ubuntu Host MCP Server")

# Register all host tools with FastMCP
for tool_fn in tools.ALL_TOOLS:
    mcp.tool()(tool_fn)


def run_mcp_stdio() -> None:
    """Runs the MCP server over standard input/output (stdio)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_mcp_stdio()
