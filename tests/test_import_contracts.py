"""Architecture fitness checks (PYTEST-ARCH-01): the layer contracts run as part of the suite."""
import configparser
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "esc_exec"


def _lint_imports() -> str | None:
    return shutil.which("lint-imports") or (
        str(Path(sys.executable).with_name("lint-imports")) if Path(sys.executable).with_name("lint-imports").exists() else None
    )


class ArchitectureTests(unittest.TestCase):
    def test_import_contracts_hold(self):
        lint_imports = _lint_imports()
        if lint_imports is None:
            self.skipTest("import-linter is not installed")
        result = subprocess.run([lint_imports], cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_every_module_is_classified_in_the_contracts(self):
        """A new module must be placed in a group (entrypoint / application / gateway / core) in
        .importlinter; otherwise it silently escapes every contract (PYDOM-TABLE-02)."""
        parser = configparser.ConfigParser()
        parser.read(ROOT / ".importlinter")
        mentioned: set[str] = set()
        for section in parser.sections():
            for key in ("layers", "source_modules", "forbidden_modules"):
                for line in parser[section].get(key, "").splitlines():
                    mentioned |= {part.strip() for part in line.split("|") if part.strip().startswith("esc_exec.")}
        modules = {f"esc_exec.{p.stem}" for p in PACKAGE.glob("*.py") if p.stem != "__init__"}
        self.assertEqual(set(), modules - mentioned, "modules missing from every contract in .importlinter")


if __name__ == "__main__":
    unittest.main()
