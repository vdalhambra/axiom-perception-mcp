# Security Policy

## What this server does and doesn't do

axiom-perception-mcp stores workflow patterns in a **local SQLite database** (`~/.axiom/perception/patterns.db`).

- It does **not** collect telemetry, usage data, or personal information.
- It does **not** send your local patterns anywhere without your explicit action (`export_pattern`).
- The only outbound network request is `fetch_community_patterns()` — it pulls a JSON file from the public GitHub repo. Nothing is sent.

## macOS Accessibility Permission

The `get_app_ui_tree`, `find_element`, `click_element`, and `type_in_element` tools require macOS Accessibility permission. **This is an elevated, system-wide permission.**

**What it means:** any process running in the same terminal session gains the ability to read UI elements and simulate interactions across all running apps.

**Recommendations:**
- Only grant Accessibility permission to trusted terminal apps (Terminal.app, iTerm2, etc.)
- Do **not** use AX tools against apps that handle credentials: password managers, banking apps, or any app in the auto-blocklist below
- Revoke the permission in System Settings → Privacy & Security → Accessibility when not in use

**Auto-blocklist:** The following apps are hardcoded to reject all AX operations, regardless of permission:
- 1Password, Bitwarden, LastPass, Dashlane, Authy, Google Authenticator
- Keychain Access, Passwords (Apple)
- System Settings / System Preferences
- Any app whose bundle ID starts with `com.agilebits`, `com.apple.keychainaccess`, `com.apple.passwords`, `com.apple.securityagent`

## Community Patterns Security

Patterns fetched from the community database are **validated against a strict schema** before insertion:

- `task`: max 200 chars
- `app`: alphanumeric + dots/hyphens, max 50 chars
- `category`: must be one of `social`, `dev`, `productivity`, `research`, `ecommerce`, `content`, `general`
- `steps`: 1–50 items, each max 500 chars, no shell-injection patterns (`$()`, backticks, `; rm`, `&& curl`, etc.)
- `notes`: max 1000 chars
- `success_rate`: capped at 0.90 on import (real usage recalibrates)
- `execution_count`: always reset to 0 on import

Any pattern failing validation is **silently rejected** — it is never written to your database. Rejection details are returned in the tool response.

## HTTP Transport

When running with `PORT` env var (HTTP mode, e.g. for cloud deployment), the server warns at startup if no `AXIOM_API_KEY` is set. For production HTTP deployments:

1. Put a reverse proxy (nginx, Caddy, Cloudflare Access) in front with authentication
2. Restrict network access to the port — do not expose it publicly without auth
3. HTTP mode is primarily intended for managed platforms (MCPize, Smithery) that handle auth at the proxy layer

## Local Database Permissions

The database directory (`~/.axiom/perception/`) is created with `700` permissions, and the database file with `600` permissions. Only the owner can read or write pattern data.

## Dependency Supply Chain

All dependencies are pinned via `uv.lock`. To verify the lockfile matches your environment:

```bash
uv sync --frozen
```

## Reporting Vulnerabilities

If you discover a security vulnerability, please report it **privately** by opening a GitHub Security Advisory at:

> https://github.com/vdalhambra/axiom-perception-mcp/security/advisories/new

Do **not** open a public issue for security vulnerabilities. We will acknowledge reports within 48 hours and aim to release a fix within 7 days for critical issues.

Please include:
- Description of the vulnerability and potential impact
- Steps to reproduce
- Any suggested fix if you have one
