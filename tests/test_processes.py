import os
import subprocess
import sys
import time
import unittest

from esc_exec.processes import descendants, kill_process_tree, process_alive


def _spawn_tree() -> tuple[subprocess.Popen, int]:
    """A parent python process that starts a sleeping child, and prints the child's PID."""
    script = (
        "import subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "print(child.pid, flush=True); time.sleep(60)"
    )
    parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    child_pid = int(parent.stdout.readline().strip())
    return parent, child_pid


class ProcessTreeTests(unittest.TestCase):
    def test_descendants_include_grandchildren_and_kill_stops_the_whole_tree(self):
        parent, child_pid = _spawn_tree()
        try:
            self.assertIn(child_pid, descendants(parent.pid))
            signalled = kill_process_tree(parent.pid, grace_seconds=5)
            self.assertIn(parent.pid, signalled)
            self.assertIn(child_pid, signalled)
            parent.wait(timeout=5)
            deadline = time.monotonic() + 5
            while process_alive(child_pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertFalse(process_alive(child_pid))
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=5)
            parent.stdout.close()

    def test_refuses_to_stop_itself_or_an_ancestor(self):
        with self.assertRaises(ValueError):
            kill_process_tree(os.getpid())
        with self.assertRaises(ValueError):
            kill_process_tree(os.getppid())

    def test_an_exited_process_is_not_alive(self):
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        process.wait(timeout=5)
        self.assertFalse(process_alive(process.pid))


if __name__ == "__main__":
    unittest.main()
