from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from esc_exec.claude_policy import HARD_DENY_SETTINGS


def claude_cli_available(binary: str = "claude") -> bool:
    """
    Just a PATH check -- not an auth check (see claude_auth_status for that). Enough
    to stop the subscription route from being offered as if it works when the CLI
    isn't even installed, without pretending to verify auth state too.
    """
    return shutil.which(binary) is not None


def claude_auth_status(binary: str = "claude") -> dict[str, Any] | None:
    """
    Real login-state confirmation, not just PATH presence -- `claude auth status`
    (verified live 2026-07-19) prints a JSON object: {"loggedIn": bool, "authMethod":
    str, "apiProvider": str, "email": str, "subscriptionType": str, ...}. Returns None
    on any failure (CLI missing, non-zero exit, unparseable output) rather than
    raising -- this is a best-effort confirm step for the connect flow, not something
    that should crash it.
    """
    try:
        result = subprocess.run([binary, "auth", "status"], capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return status if isinstance(status, dict) else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def result_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    The terminal `result`-type message from a claude -p stream-json run -- shared
    between ClaudeCodeAdapter (task execution) and esc_exec.conversation (multi-turn
    conversations), both of which parse the same NDJSON stream shape.
    """
    for message in reversed(messages):
        if message.get("type") == "result":
            return message
    return None


class ClaudeCodeError(RuntimeError):
    pass


class ClaudeCodeClient:
    """
    Shells out to the real `claude` CLI in headless print mode. `--tools` is the
    actual enforcement boundary (a tool absent from the list literally isn't
    available to the model), so `--permission-mode bypassPermissions` is safe here --
    it only skips the interactive confirmation prompt a human can't answer in headless
    execution, it does not widen the tool allowlist. Verified live 2026-07-19 against
    claude-code 2.1.215: `--output-format stream-json` in print mode requires
    `--verbose` (undocumented in `--help`, confirmed by the CLI's own runtime error).
    Every invocation also carries `HARD_DENY_SETTINGS` via `--settings` -- a second,
    independent layer underneath the tool allowlist, not a substitute for it.
    """

    def __init__(self, binary: str = "claude", timeout: float = 3600.0):
        self.binary, self.timeout = binary, timeout

    def _invoke(
        self, directory: Path, prompt: str, tools: list[str], output_format: str,
        model: str | None = None, resume_session_id: str | None = None, verbose: bool = False,
    ) -> str:
        command = [
            self.binary, "-p", "--output-format", output_format,
            "--permission-mode", "bypassPermissions",
            "--tools", ",".join(tools),
            "--settings", json.dumps(HARD_DENY_SETTINGS),
        ]
        if verbose:
            command.append("--verbose")
        if model:
            command += ["--model", model]
        if resume_session_id:
            command += ["--resume", resume_session_id]
        try:
            result = subprocess.run(
                command, cwd=directory, input=prompt, capture_output=True, text=True, timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise ClaudeCodeError(f"`{self.binary}` not found -- is Claude Code installed and on PATH?") from exc
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCodeError(f"claude -p timed out after {self.timeout}s") from exc
        if result.returncode != 0:
            raise ClaudeCodeError(f"claude -p exited {result.returncode}: {result.stderr.strip()[:500]}")
        return result.stdout

    def run(
        self, directory: Path, prompt: str, tools: list[str],
        model: str | None = None, resume_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        The full-fidelity path: NDJSON message stream (init/assistant/user/result),
        used by ClaudeCodeAdapter.execute for real task runs where per-tool events
        matter. `--verbose` is required alongside stream-json in print mode (verified
        live 2026-07-19 against claude-code 2.1.215, undocumented in --help).
        """
        stdout = self._invoke(directory, prompt, tools, "stream-json", model, resume_session_id, verbose=True)
        messages: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ClaudeCodeError(f"could not parse claude -p stream-json line: {line[:200]}") from exc
        return messages

    def ask(self, directory: Path, prompt: str, tools: list[str], model: str | None = None) -> dict[str, Any]:
        """
        The lightweight path: a single aggregated JSON result object, no per-tool
        event stream, no --verbose requirement. For a bounded question-answering call
        (e.g. suggest_purposes below) that isn't a task run at all -- no run.json, no
        events.jsonl, no verification plan, none of ClaudeCodeAdapter.execute's
        artifact machinery. Same underlying `result`-message shape `run()` gets as its
        last stream-json line, just without needing to stream to get it.
        """
        stdout = self._invoke(directory, prompt, tools, "json", model)
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ClaudeCodeError(f"could not parse claude -p json output: {stdout[:200]}") from exc

