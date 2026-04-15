"""
Axiom Perception MCP — Persistent Memory & Pattern Learning for AI Agents.

Gives Claude a long-term memory for multi-step workflows. Save patterns that work,
recall them before starting a task, record outcomes to track reliability, and
sync with community-contributed patterns so you never start from zero.

Works as an intelligence layer on top of any automation tool
(Playwright MCP, Computer Use, etc.).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastmcp import FastMCP
from perception.tools.memory import register_memory_tools
from perception.tools.community import register_community_tools

mcp = FastMCP(
    name="Axiom Perception",
    instructions=(
        "Persistent memory and pattern learning for multi-step workflows. "
        "ALWAYS call recall_pattern() BEFORE starting any multi-step task — "
        "if a pattern exists, follow its steps to skip trial-and-error. "
        "After completing a task (success or failure), call record_outcome() "
        "to track reliability. When you find a better approach, call update_pattern() "
        "to improve the shared knowledge. "
        "On first use: call fetch_community_patterns() to load battle-tested "
        "workflows for Twitter, GitHub, LinkedIn, and more — no cold start. "
        "Works alongside Playwright MCP, Computer Use, or any automation tool: "
        "this MCP handles the 'what to do', your automation tool handles the 'how'."
    ),
    version="1.0.0",
)

register_memory_tools(mcp)
register_community_tools(mcp)


def main() -> None:
    import os
    if os.environ.get("PORT"):
        mcp.run(transport="http", host="0.0.0.0", port=int(os.environ["PORT"]))
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
