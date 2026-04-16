"""
Axiom Perception MCP — Empirical Memory & Workflow Intelligence for AI Agents.

Agents forget. This fixes it. Every workflow you complete gets stored as a pattern
with a real success rate — not rules you wrote, but things the agent discovered.
Next session: recall_pattern() returns the exact steps that worked, ranked by
how reliable they've proven to be. First time: 40 min. After that: 2 min.

Four capabilities:
  1. Pattern memory     — save/recall/update proven workflows with success tracking
  2. Failure learning   — record what approaches failed so future agents skip them
  3. Workflow checkpoints — resume long tasks across sessions, no re-doing done steps
  4. Multi-agent notes  — share state, results, and locks between concurrent agents

Works as an intelligence layer on top of any automation tool
(Playwright MCP, Computer Use, etc.).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastmcp import FastMCP
from perception.tools.memory import register_memory_tools
from perception.tools.community import register_community_tools
from perception.tools.macos_ax import register_macos_ax_tools
from perception.tools.checkpoints import register_checkpoint_tools
from perception.tools.coordination import register_coordination_tools

mcp = FastMCP(
    name="Axiom Perception",
    instructions=(
        "Empirical memory and workflow intelligence for AI agents. "
        "ALWAYS call recall_pattern() BEFORE starting any multi-step task — "
        "pass context= with your current environment (tech, URL, framework) for "
        "context-aware matching. If a pattern exists, follow its steps to skip trial-and-error. "
        "After every execution (success OR failure), call record_outcome() — "
        "pass approach= to build the failure knowledge base (what NOT to try next time). "
        "For long workflows (5+ steps): call save_checkpoint() after each step so "
        "resume_checkpoint() can pick up where you left off after any interruption. "
        "For multi-agent coordination: use share_note() / read_note() to pass state "
        "between concurrent agents or sequential sessions. "
        "On first use: call fetch_community_patterns() to load battle-tested "
        "workflows for Twitter, GitHub, LinkedIn, and more — no cold start. "
        "On macOS: use check_accessibility_permissions() then get_app_ui_tree() "
        "to control any native app directly via the Accessibility API."
    ),
    version="2.1.0",
)

register_memory_tools(mcp)
register_community_tools(mcp)
register_macos_ax_tools(mcp)
register_checkpoint_tools(mcp)
register_coordination_tools(mcp)


def main() -> None:
    import os
    import sys
    if os.environ.get("PORT"):
        port = int(os.environ["PORT"])
        if not os.environ.get("AXIOM_API_KEY"):
            print(
                "[axiom-perception] WARNING: HTTP transport started without AXIOM_API_KEY. "
                "The server is accessible to anyone who can reach this port. "
                "Set AXIOM_API_KEY and put a reverse proxy with auth in front for production use.",
                file=sys.stderr,
            )
        mcp.run(transport="http", host="0.0.0.0", port=port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
