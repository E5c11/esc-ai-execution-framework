"""Stopping a run's process tree (used by the orchestrator's `task stop`).

A run's agent, gate commands and Gradle builds are children (and grandchildren) of the `escape-ai task run`
process, started with blocking subprocess calls in the same session, so the only reliable handle is the parent's
PID: walk its descendants and signal them all, deepest first.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return _state(pid) != "Z"


def descendants(pid: int) -> list[int]:
    """Every descendant of `pid`, deepest first (so children are signalled before their parents)."""
    children: dict[int, list[int]] = {}
    for parent, child in _parent_child_pairs():
        children.setdefault(parent, []).append(child)
    ordered: list[int] = []
    frontier = [pid]
    while frontier:
        current = frontier.pop()
        for child in children.get(current, []):
            ordered.append(child)
            frontier.append(child)
    return list(reversed(ordered))


def kill_process_tree(pid: int, grace_seconds: float = 10.0) -> list[int]:
    """
    SIGTERM `pid` and all its descendants, wait up to `grace_seconds`, then SIGKILL whatever is left. Returns the
    PIDs that were signalled. Refuses to touch the calling process or any of its ancestors.
    """
    protected = _self_and_ancestors()
    if pid in protected:
        raise ValueError(f"refusing to stop process {pid}: it is this process or one of its ancestors")
    targets = [p for p in [*descendants(pid), pid] if p not in protected]
    signalled = [p for p in targets if _signal(p, signal.SIGTERM)]
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline and any(process_alive(p) for p in signalled):
        time.sleep(0.2)
    for p in signalled:
        if process_alive(p):
            _signal(p, signal.SIGKILL)
    return signalled


def _signal(pid: int, sig: int) -> bool:
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _parent_child_pairs() -> list[tuple[int, int]]:
    result = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, check=True)
    pairs: list[tuple[int, int]] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            pairs.append((int(parts[1]), int(parts[0])))
    return pairs


def _self_and_ancestors() -> set[int]:
    parent_of = {child: parent for parent, child in _parent_child_pairs()}
    protected: set[int] = set()
    current = os.getpid()
    while current and current not in protected:
        protected.add(current)
        current = parent_of.get(current, 0)
    return protected


def _state(pid: int) -> str:
    result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
    return result.stdout.strip()[:1]
