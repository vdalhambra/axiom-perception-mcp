"""macOS Accessibility API tools — native app UI control without screenshots.

Requires Accessibility permission: System Settings → Privacy & Security → Accessibility.
macOS only. Falls back gracefully on other platforms.

SECURITY: AX tools will refuse to operate on apps in the SENSITIVE_APPS blocklist.
This prevents accidental or malicious automation of password managers, banking apps,
and system security dialogs. Extend the list as needed.
"""

import json
from typing import Annotated, Optional

from fastmcp import FastMCP
from pydantic import Field

# --- Sensitive app blocklist ---
# AX operations on these apps are blocked to prevent credential theft.
# Bundle ID prefixes (case-insensitive) take priority over name matching.

_BLOCKED_BUNDLE_PREFIXES = {
    "com.agilebits",           # 1Password family
    "com.bitwarden",           # Bitwarden
    "com.lastpass",            # LastPass
    "com.dashlane",            # Dashlane
    "com.apple.keychainaccess",
    "com.apple.passwords",
    "com.apple.securityagent",
    "com.apple.security",
    "com.apple.systempreferences",  # System Preferences / System Settings
}

_BLOCKED_NAME_KEYWORDS = {
    "1password", "keychain access", "passwords", "bitwarden",
    "lastpass", "dashlane", "authy", "google authenticator",
    "security agent", "system preferences", "system settings",
}


def _is_blocked_app(name: str, bundle_id: str = "") -> bool:
    name_lower = name.lower()
    bundle_lower = bundle_id.lower()
    if any(name_lower == kw or kw in name_lower for kw in _BLOCKED_NAME_KEYWORDS):
        return True
    if any(bundle_lower.startswith(prefix) for prefix in _BLOCKED_BUNDLE_PREFIXES):
        return True
    return False


def _blocked_app_error(app_name: str) -> dict:
    return {
        "status": "error",
        "reason": "blocked_app",
        "app": app_name,
        "message": (
            f"'{app_name}' is in the blocked apps list. "
            "AX automation is disabled for password managers, credential stores, "
            "and system security apps to prevent accidental exposure of sensitive data."
        ),
    }


# --- Helpers ---

def _ax_available() -> bool:
    try:
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except ImportError:
        return False


def _not_trusted_error() -> dict:
    return {
        "status": "error",
        "reason": "accessibility_permission_required",
        "fix": (
            "Open System Settings → Privacy & Security → Accessibility → "
            "add your terminal app (Terminal, iTerm2, etc.) and enable the toggle. "
            "Then retry this tool."
        ),
    }


def _not_macos_error() -> dict:
    return {
        "status": "error",
        "reason": "macos_only",
        "message": "macOS Accessibility tools require macOS. Use Playwright MCP on other platforms.",
    }


def _get_running_apps() -> list[dict]:
    from AppKit import NSWorkspace
    apps = NSWorkspace.sharedWorkspace().runningApplications()
    result = []
    seen_bundle_ids: set[str] = set()
    for app in apps:
        name = app.localizedName()
        if not name:
            continue
        bundle_id = app.bundleIdentifier() or ""
        # Deduplicate: skip additional processes with the same bundle ID
        if bundle_id and bundle_id in seen_bundle_ids:
            continue
        if bundle_id:
            seen_bundle_ids.add(bundle_id)
        result.append({
            "name": name,
            "pid": app.processIdentifier(),
            "bundle_id": bundle_id,
            "foreground": app.activationPolicy() == 0,
        })
    return sorted(result, key=lambda x: x["name"].lower())


def _find_app_pid(app_name: str) -> Optional[int]:
    info = _find_app_info(app_name)
    return info["pid"] if info else None


def _find_app_info(app_name: str) -> Optional[dict]:
    """Return {pid, name, bundle_id} for the best matching running app, or None.

    Prefers: exact foreground match > exact match > foreground substring > any substring.
    Skips helper/service processes that contain the app name as a substring.
    """
    name_lower = app_name.lower()
    apps = _get_running_apps()

    # 1. Exact name match, foreground preferred
    exact = [a for a in apps if a["name"].lower() == name_lower]
    if exact:
        fg = [a for a in exact if a["foreground"]]
        return (fg or exact)[0]

    # 2. Foreground apps where the app name starts with or equals the search term
    starts = [a for a in apps if a["foreground"] and a["name"].lower().startswith(name_lower)]
    if starts:
        return starts[0]

    # 3. Substring match — prefer foreground and shorter names (avoids "Service (App)" patterns)
    matches = [a for a in apps if name_lower in a["name"].lower()]
    if matches:
        fg = [a for a in matches if a["foreground"]]
        candidates = fg or matches
        return min(candidates, key=lambda a: len(a["name"]))

    return None


def _attr(element, attr: str):
    """Get a single AX attribute value. Returns None on failure."""
    try:
        from ApplicationServices import AXUIElementCopyAttributeValue, kAXErrorSuccess
        err, val = AXUIElementCopyAttributeValue(element, attr, None)
        if err == kAXErrorSuccess:
            return val
    except Exception:
        pass
    return None


def _element_summary(element, index: int = 0) -> dict:
    """Build a minimal dict from an AX element."""
    info: dict = {"_idx": index}
    role = _attr(element, "AXRole")
    if role:
        info["role"] = str(role)
    title = _attr(element, "AXTitle")
    if title:
        info["title"] = str(title)[:80]
    desc = _attr(element, "AXDescription")
    if desc:
        info["description"] = str(desc)[:80]
    value = _attr(element, "AXValue")
    if value is not None:
        info["value"] = str(value)[:80]
    label = _attr(element, "AXLabel")
    if label:
        info["label"] = str(label)[:80]
    placeholder = _attr(element, "AXPlaceholderValue")
    if placeholder:
        info["placeholder"] = str(placeholder)[:80]
    enabled = _attr(element, "AXEnabled")
    if enabled is not None:
        info["enabled"] = bool(enabled)
    return info


def _element_path_id(role: str, title: str, index: int) -> str:
    safe_title = (title or "")[:20].replace(" ", "_").replace("/", "-")
    return f"{role}:{safe_title}:{index}"


def _walk_tree(element, depth: int, max_depth: int, path: str = "root") -> list[dict]:
    """Walk accessibility tree, return flat list of interactable elements."""
    results = []
    if depth > max_depth:
        return results

    children = _attr(element, "AXChildren")
    if not children:
        return results

    for i, child in enumerate(children[:30]):  # cap at 30 per level
        summary = _element_summary(child, i)
        role = summary.get("role", "")
        child_path = f"{path}/{i}"
        summary["_path"] = child_path

        # Compute a stable click ID
        title = summary.get("title") or summary.get("description") or summary.get("label") or ""
        summary["_id"] = _element_path_id(role, title, i)

        # Only include roles that are useful
        useful_roles = {
            "AXButton", "AXTextField", "AXTextArea", "AXCheckBox",
            "AXRadioButton", "AXComboBox", "AXMenuItem", "AXMenuBarItem",
            "AXLink", "AXTabGroup", "AXList", "AXOutline", "AXPopUpButton",
            "AXStaticText", "AXHeading", "AXImage",
        }
        if role in useful_roles or depth <= 2:
            results.append(summary)

        # Recurse into containers
        container_roles = {
            "AXWindow", "AXGroup", "AXScrollArea", "AXSplitGroup",
            "AXTabGroup", "AXToolbar", "AXMenuBar", "AXList", "AXOutline",
        }
        if role in container_roles or depth < 2:
            results.extend(_walk_tree(child, depth + 1, max_depth, child_path))

    return results


def _get_element_by_path(root, path_str: str):
    """Navigate to an element by path like 'root/2/1/0'."""
    parts = path_str.split("/")
    if parts[0] == "root":
        parts = parts[1:]

    element = root
    for part in parts:
        children = _attr(element, "AXChildren")
        if not children:
            return None
        idx = int(part)
        if idx >= len(children):
            return None
        element = children[idx]
    return element


# --- Tool registration ---

def register_macos_ax_tools(mcp: FastMCP) -> None:

    @mcp.tool
    def check_accessibility_permissions() -> dict:
        """Check if macOS Accessibility API permissions are granted.

        Run this first before using any other accessibility tools.
        If not trusted, it shows exactly how to grant the permission.

        On non-macOS systems, returns a clear 'macos_only' message.
        """
        try:
            from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions
        except ImportError:
            return _not_macos_error()

        trusted = bool(AXIsProcessTrusted())
        if trusted:
            return {
                "status": "ok",
                "trusted": True,
                "message": "Accessibility permission granted. All ax_* tools are available.",
            }
        else:
            # Trigger the system prompt to ask for permission
            AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": True})
            return {
                "status": "permission_required",
                "trusted": False,
                "instructions": [
                    "1. A system dialog should have appeared asking for Accessibility permission.",
                    "2. If no dialog: open System Settings → Privacy & Security → Accessibility",
                    "3. Find your terminal app (Terminal, iTerm2, etc.) in the list",
                    "4. Enable the toggle next to it",
                    "5. Call check_accessibility_permissions() again to verify",
                ],
            }

    @mcp.tool
    def list_running_apps(
        foreground_only: Annotated[bool, Field(description="Only list apps with UI windows (filter out background daemons)")] = True,
    ) -> dict:
        """List all running macOS applications with their names and PIDs.

        Use this to find the exact app name before calling other accessibility tools.
        Does NOT require Accessibility permission.
        """
        try:
            apps = _get_running_apps()
        except ImportError:
            return _not_macos_error()

        if foreground_only:
            apps = [a for a in apps if a["foreground"]]

        return {
            "count": len(apps),
            "apps": apps,
            "note": "Use the 'name' field as app_name in other accessibility tools.",
        }

    @mcp.tool
    def get_app_ui_tree(
        app_name: Annotated[str, Field(description="App name, e.g. 'Safari', 'TextEdit', 'Finder'")],
        max_depth: Annotated[int, Field(description="How deep to traverse the UI tree (1-4, default 3)", ge=1, le=4)] = 3,
    ) -> dict:
        """Get the accessibility UI tree of a running macOS app.

        Returns a flat list of all interactable elements (buttons, text fields,
        checkboxes, menu items, etc.) with their roles, titles, and click IDs.

        Use _id values with click_element() and type_in_element() to interact
        with specific elements — no selectors, no XPath, just semantic roles.

        Requires Accessibility permission.
        """
        try:
            from ApplicationServices import AXUIElementCreateApplication
        except ImportError:
            return _not_macos_error()

        if not _ax_available():
            return _not_trusted_error()

        app_info = _find_app_info(app_name)
        if not app_info:
            running = [a["name"] for a in _get_running_apps() if a["foreground"]]
            return {
                "status": "error",
                "message": f"App '{app_name}' not found in running apps.",
                "running_apps": running,
            }

        if _is_blocked_app(app_info["name"], app_info.get("bundle_id", "")):
            return _blocked_app_error(app_info["name"])

        root = AXUIElementCreateApplication(app_info["pid"])
        elements = _walk_tree(root, depth=0, max_depth=max_depth)

        # Group by role for readability
        by_role: dict = {}
        for e in elements:
            role = e.get("role", "unknown")
            by_role.setdefault(role, []).append(e)

        return {
            "app": app_name,
            "pid": app_info["pid"],
            "total_elements": len(elements),
            "elements_by_role": by_role,
            "tip": "Use _id or _path values with click_element() or type_in_element().",
        }

    @mcp.tool
    def find_element(
        app_name: Annotated[str, Field(description="App name, e.g. 'Safari', 'TextEdit'")],
        query: Annotated[str, Field(description="What to find, e.g. 'send button', 'search field', 'File menu'", max_length=200)],
        max_depth: Annotated[int, Field(description="Search depth (1-4)", ge=1, le=4)] = 3,
    ) -> dict:
        """Find a specific UI element in an app by semantic description.

        Searches by role, title, description, label, and placeholder text.
        Returns matching elements with their _id and _path for interaction.

        Example: find_element('Safari', 'address bar') → returns the URL field
        Example: find_element('TextEdit', 'bold button') → returns the Bold button

        Requires Accessibility permission.
        """
        try:
            from ApplicationServices import AXUIElementCreateApplication
        except ImportError:
            return _not_macos_error()

        if not _ax_available():
            return _not_trusted_error()

        app_info = _find_app_info(app_name)
        if not app_info:
            return {"status": "error", "message": f"App '{app_name}' not found."}

        if _is_blocked_app(app_info["name"], app_info.get("bundle_id", "")):
            return _blocked_app_error(app_info["name"])

        pid = app_info["pid"]

        root = AXUIElementCreateApplication(pid)
        elements = _walk_tree(root, depth=0, max_depth=max_depth)

        query_words = [w.lower() for w in query.split() if len(w) >= 2]

        def score(e: dict) -> float:
            text = " ".join(str(v) for k, v in e.items() if k not in ("_idx", "_path", "_id", "enabled")).lower()
            hits = sum(1 for w in query_words if w in text)
            return hits / len(query_words) if query_words else 0

        scored = [(score(e), e) for e in elements]
        scored.sort(key=lambda x: x[0], reverse=True)
        matches = [e for s, e in scored if s > 0][:5]

        if not matches:
            return {
                "status": "not_found",
                "query": query,
                "message": f"No element matching '{query}' found in {app_name}.",
                "suggestion": f"Try get_app_ui_tree('{app_name}') to browse all elements.",
            }

        return {
            "status": "found",
            "query": query,
            "app": app_name,
            "matches": matches,
            "tip": "Use the _id or _path of the top match with click_element() or type_in_element().",
        }

    @mcp.tool
    def click_element(
        app_name: Annotated[str, Field(description="App name, e.g. 'Safari'")],
        element_path: Annotated[str, Field(description="Element _path from get_app_ui_tree or find_element, e.g. 'root/0/2/1'")],
    ) -> dict:
        """Click a UI element in a macOS app by its path.

        Use the _path value from find_element() or get_app_ui_tree().
        Works on any native macOS app — no browser required.

        Requires Accessibility permission.
        """
        try:
            from ApplicationServices import (
                AXUIElementCreateApplication,
                AXUIElementPerformAction,
                kAXPressAction,
                kAXErrorSuccess,
            )
        except ImportError:
            return _not_macos_error()

        if not _ax_available():
            return _not_trusted_error()

        app_info = _find_app_info(app_name)
        if not app_info:
            return {"status": "error", "message": f"App '{app_name}' not found."}

        if _is_blocked_app(app_info["name"], app_info.get("bundle_id", "")):
            return _blocked_app_error(app_info["name"])

        root = AXUIElementCreateApplication(app_info["pid"])
        target = _get_element_by_path(root, element_path)

        if target is None:
            return {
                "status": "error",
                "message": f"Element at path '{element_path}' not found.",
                "tip": "Paths change when app UI changes. Re-run find_element() to get fresh paths.",
            }

        err = AXUIElementPerformAction(target, kAXPressAction)
        if err == kAXErrorSuccess:
            summary = _element_summary(target)
            return {
                "status": "clicked",
                "element": summary,
                "path": element_path,
            }
        else:
            return {
                "status": "error",
                "ax_error_code": err,
                "message": "Click action failed. Element may not support press action.",
                "tip": "Some elements use other actions. Try get_app_ui_tree() to check available actions.",
            }

    @mcp.tool
    def type_in_element(
        app_name: Annotated[str, Field(description="App name, e.g. 'Safari', 'TextEdit'")],
        element_path: Annotated[str, Field(description="Element _path from find_element(), e.g. 'root/0/1'")],
        text: Annotated[str, Field(description="Text to type into the element", max_length=50000)],
        clear_first: Annotated[bool, Field(description="Clear existing content before typing")] = False,
    ) -> dict:
        """Type text into a text field or text area in a native macOS app.

        Use find_element(app, 'text field') or find_element(app, 'search') to get the path first.
        Much faster and more reliable than simulated keyboard input.

        Requires Accessibility permission.
        """
        try:
            from ApplicationServices import (
                AXUIElementCreateApplication,
                AXUIElementSetAttributeValue,
                kAXErrorSuccess,
                kAXValueAttribute,
            )
        except ImportError:
            return _not_macos_error()

        if not _ax_available():
            return _not_trusted_error()

        app_info = _find_app_info(app_name)
        if not app_info:
            return {"status": "error", "message": f"App '{app_name}' not found."}

        if _is_blocked_app(app_info["name"], app_info.get("bundle_id", "")):
            return _blocked_app_error(app_info["name"])

        pid = app_info["pid"]

        root = AXUIElementCreateApplication(pid)
        target = _get_element_by_path(root, element_path)

        if target is None:
            return {"status": "error", "message": f"Element at path '{element_path}' not found."}

        if clear_first:
            AXUIElementSetAttributeValue(target, "AXValue", "")

        err = AXUIElementSetAttributeValue(target, "AXValue", text)
        if err == kAXErrorSuccess:
            return {
                "status": "typed",
                "text_length": len(text),
                "element_path": element_path,
            }
        else:
            return {
                "status": "error",
                "ax_error_code": err,
                "message": "Could not set value. Element may be read-only or not a text field.",
            }

    @mcp.tool
    def get_focused_element() -> dict:
        """Get the currently focused UI element on screen (works across all apps).

        Returns the app name, element role, title, and value of whatever
        is currently focused — useful to verify state after clicking.

        Requires Accessibility permission.
        """
        try:
            from ApplicationServices import (
                AXUIElementCreateSystemWide,
                AXUIElementCopyAttributeValue,
                kAXErrorSuccess,
                kAXFocusedApplicationAttribute,
                kAXFocusedUIElementAttribute,
            )
            from AppKit import NSWorkspace
        except ImportError:
            return _not_macos_error()

        if not _ax_available():
            return _not_trusted_error()

        system = AXUIElementCreateSystemWide()
        err, focused_app = AXUIElementCopyAttributeValue(system, kAXFocusedApplicationAttribute, None)
        if err != kAXErrorSuccess:
            return {"status": "error", "message": "Could not get focused app."}

        err, focused_elem = AXUIElementCopyAttributeValue(system, kAXFocusedUIElementAttribute, None)
        if err != kAXErrorSuccess:
            return {"status": "no_focused_element"}

        # Get current app name
        apps = NSWorkspace.sharedWorkspace().runningApplications()
        front_app = NSWorkspace.sharedWorkspace().frontmostApplication()
        app_name = front_app.localizedName() if front_app else "unknown"

        summary = _element_summary(focused_elem)
        summary["app"] = app_name
        summary["status"] = "found"

        return summary
