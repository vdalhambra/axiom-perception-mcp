# axiom-perception-mcp

**Persistent memory and pattern learning for AI agents.**

Claude forgets how to use your computer between sessions. axiom-perception-mcp fixes that — it gives Claude a long-term memory for multi-step workflows so it never re-learns the same thing twice.

```
claude: recall_pattern("post tweet")
→ "Found pattern v3 (97% success rate, 42 executions):
    1. Navigate to x.com
    2. Click 'Post' in the left sidebar
    3. ..."
```

Zero API keys. No setup. Just install and Claude starts remembering.

---

## Install

```bash
# Claude Desktop / Claude Code
uvx axiom-perception-mcp
```

Add to your MCP client config:
```json
{
  "mcpServers": {
    "perception": {
      "command": "uvx",
      "args": ["axiom-perception-mcp"]
    }
  }
}
```

---

## The problem it solves

When Claude tries to do something in your browser — post a tweet, create a PR, fill a form — it often spends 5-10 minutes figuring it out through trial and error. Every session, from scratch.

axiom-perception-mcp stores what worked. Next time, Claude skips straight to the answer.

**Works alongside any automation tool**: Playwright MCP, Computer Use, browser tools. This MCP handles *what to do*, your automation tool handles *how to do it*.

---

## 8 tools

| Tool | What it does |
|------|-------------|
| `recall_pattern(task, app?)` | Get the best known workflow **before** starting a task |
| `save_pattern(task, steps, app, category)` | Save a workflow that worked |
| `update_pattern(id, steps, reason?)` | Improve a pattern with a better approach |
| `record_outcome(id, success, time_ms?)` | Track executions to build success rate |
| `list_patterns(app?, category?)` | Browse all known workflows |
| `search_patterns(query)` | Search across all patterns |
| `export_pattern(id)` | Export as JSON to share with the community |
| `fetch_community_patterns(app?)` | Import proven patterns from the shared database |

---

## Community patterns — no cold start

On first use, run `fetch_community_patterns()` to load proven workflows contributed by users worldwide:

- **Twitter/X**: post tweet, post thread, reply, follow user, like
- **LinkedIn**: publish post, comment
- **GitHub**: create PR, create issue, comment on PR
- **DEV.to**: publish article (browser + API)
- **Bluesky**: post via AT Protocol API
- **Hacker News**: search, submit Show HN
- **Generic**: login form, screenshot, copy page content
- **Dev tools**: deploy to MCPize, publish to PyPI, claim on Glama

---

## How the collective intelligence works

1. **You** discover that a 3-step approach works where the community uses 8 steps
2. Call `update_pattern()` — the pattern upgrades to your faster version
3. Call `export_pattern()` — share it in a GitHub issue
4. **Everyone** who installs axiom-perception-mcp gets your improvement via `fetch_community_patterns()`

Patterns are ranked by success rate. Better solutions automatically get promoted. The more users contribute, the smarter every agent gets.

---

## Usage pattern

```
# Before starting any multi-step task:
recall_pattern("create github PR")
→ Follow the steps

# After completing (success or failure):
record_outcome("a3f9b2c1", success=True, time_ms=4200)

# If you find a faster way:
update_pattern("a3f9b2c1", new_steps=[...], reason="2 fewer clicks")

# Share your improvement:
export_pattern("a3f9b2c1")
→ Paste the JSON in a GitHub issue
```

---

## Platforms

| Platform | Link |
|----------|------|
| PyPI | `pip install axiom-perception-mcp` |
| GitHub | [vdalhambra/axiom-perception-mcp](https://github.com/vdalhambra/axiom-perception-mcp) |
| Smithery | [vdalhambra/axiom-perception](https://smithery.ai/server/vdalhambra/axiom-perception) |
| Glama | Auto-indexed |

---

## Data storage

Patterns are stored locally in `~/.axiom/perception/patterns.db` (SQLite). Nothing leaves your machine unless you explicitly call `fetch_community_patterns()` or `export_pattern()`.

---

## License

MIT — by [Axiom](https://github.com/vdalhambra)
