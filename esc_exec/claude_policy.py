from __future__ import annotations

from typing import Any


def tools_for_policy(policy_document: dict[str, Any]) -> list[str]:
    """
    Map a policy document's permissions onto the Claude Code CLI tool allowlist
    (`--tools`). Deny-by-default: a tool is included only if its owning permission
    category is exactly "allow" -- same discipline as OpenCode's tools_for_policy,
    re-derived here rather than shared, because the concrete tool surface (and thus
    the mapping) is genuinely per-adapter (see native-cli-provider-adapters.md,
    "Permission mapping is per-adapter, not shared").

    - permissions.read    -> Read, Glob, Grep
    - permissions.edit    -> Edit, Write, NotebookEdit
    - permissions.execute -> Bash
    - permissions.network -> WebFetch, WebSearch

    "ask" is treated as denied, not as a softer form of allow -- there is no mid-run
    human-escalation mechanism yet to actually honor an "ask" prompt (same deliberate
    simplification as the OpenCode adapter).

    Returns a list, never the CLI's own `--tools ""` (disable-all) string form --
    callers pass an empty list straight to `--tools` and get the same disable-all
    effect through comma-joining.

    NOT enforced by this function, on purpose (same documented gap as OpenCode's
    tools_for_policy): permissions.external_paths (needs path-scoping tool call
    arguments -- `--add-dir` grants a directory, not a per-call constraint) and the
    policy document's limits/approvals fields (need run-duration and approval-gating
    mechanisms, not a tool allowlist).
    """
    permissions = policy_document.get("permissions", {})

    def granted(category: str) -> bool:
        return permissions.get(category) == "allow"

    tools: list[str] = []
    if granted("read"):
        tools += ["Read", "Glob", "Grep"]
    if granted("edit"):
        tools += ["Edit", "Write", "NotebookEdit"]
    if granted("execute"):
        tools.append("Bash")
    if granted("network"):
        tools += ["WebFetch", "WebSearch"]
    return tools


def granted_categories(policy_document: dict[str, Any]) -> list[str]:
    """
    The category-level granted list (read/edit/execute/network) a policy
    document allows -- same deny-by-default discipline as tools_for_policy, one
    level coarser (categories, not concrete tool names). Recorded into every
    run's bindings.consent (see ClaudeCodeAdapter.execute) so a task's actual
    historical scope stays reconstructable from run.json the same way its
    tool_grant already does -- see
    plan/future/pre-flight-consent-and-bounded-autonomy.md layer 1.
    """
    permissions = policy_document.get("permissions", {})
    return [category for category in ("read", "edit", "execute", "network") if permissions.get(category) == "allow"]


# A small, static, universal deny list -- see plan/future/pre-flight-consent-and-
# bounded-autonomy.md layer 3. Applies to every invocation regardless of what a
# task's policy otherwise grants (a task can be scoped to less than this implies,
# never more) -- fixed in code, not task- or policy-configurable, and deliberately
# kept short: the goal is a short list of things that are *never* fine
# autonomously, not an attempt to enumerate everything that *is* fine (that's what
# the tool-category grant already covers). `rm -rf` is denied unconditionally
# rather than trying to distinguish "safe" targets (e.g. `rm -rf build/`) from
# unsafe ones, because `--settings` patterns match the command string, not a
# resolved path -- there's no reliable way to tell those apart at the pattern
# level. Legitimate cache-clearing goes through the build tool's own clean task
# (`./gradlew clean`) instead, already reachable once `execute` is granted at all.
# Verified live 2026-07-24: `--permission-mode auto` alone, with no explicit
# `--settings` rule, let an `rm -rf` execute with zero intervention
# (`permission_denials: []`) -- an explicit deny pattern is the only mechanism
# confirmed to actually block a destructive command headlessly, cleanly, with no
# hang (`bypassPermissions` is kept as the permission mode; see that plan doc's
# "What we found" for why `auto` was tried and dropped).
HARD_DENY_SETTINGS: dict[str, Any] = {
    "permissions": {
        "deny": [
            "Bash(git push --force*)",
            "Bash(git push -f*)",
            "Bash(git reset --hard*)",
            "Bash(git clean -f*)",
            "Bash(git branch -D*)",
            "Bash(git filter-branch*)",
            "Bash(rm -rf*)",
            "Bash(sudo*)",
            "Read(**/*firebase-adminsdk*.json)",
            "Read(**/*.pem)",
            "Read(**/.env)",
            "Edit(**/.git/**)",
        ],
    },
}

