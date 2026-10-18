"""Static import extraction and resolution for TypeScript and JavaScript sources.

Used by the architecture fitness check's ``forbidden-import`` rule. It is deliberately a small,
dependency-free reader and not a TypeScript compiler. What it does and does not see:

* Sees: ``import ... from 'x'`` (including ``import type`` and multi-line forms), side-effect
  ``import 'x'``, ``export ... from 'x'``, dynamic ``import('x')`` and ``require('x')`` with a string
  literal, in ``.ts .tsx .mts .cts .js .jsx .mjs .cjs`` files.
* Resolves: relative specifiers, and aliases declared in ``tsconfig.json`` (``paths`` and ``baseUrl``,
  following relative ``extends`` chains), to a component-relative path.
* Does not see: a dynamic import whose argument is not a string literal, a specifier assembled at run
  time, or a regular-expression literal that contains a quote character. Those are the limits of reading
  source without running it, and they are the reason a rule that scans nothing fails (see
  ``esc_exec.architecture``).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import re


TS_SUFFIXES = frozenset({".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"})

# Directories that hold generated or third-party code, never the component's own source.
EXCLUDED_DIRECTORIES = frozenset({"node_modules", ".next", "out", "dist", "build", "coverage", ".git", ".turbo"})

_TEST_FILE = re.compile(r"\.(test|spec|contract)\.[cm]?[jt]sx?$")

_SPECIFIER_PATTERNS = (
    re.compile(r"""\bfrom\s*(['"])([^'"\n]+)\1"""),
    re.compile(r"""\bimport\s*(['"])([^'"\n]+)\1"""),
    re.compile(r"""\bimport\s*\(\s*(['"])([^'"\n]+)\1"""),
    re.compile(r"""\brequire\s*\(\s*(['"])([^'"\n]+)\1\s*\)"""),
)


def is_test_file(relative: Path) -> bool:
    return bool(_TEST_FILE.search(relative.name)) or "__tests__" in relative.parts


def strip_comments(source: str) -> str:
    """Blank out comments, keeping every line break so line numbers stay true.

    String and template literals are respected, so ``"//"`` inside a string is not a comment. A ``'`` or
    ``"`` string ends at a line break, which keeps an apostrophe in JSX text from swallowing the rest of
    the file.
    """
    out: list[str] = []
    i, n = 0, len(source)
    quote = ""
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(source[i + 1])
                i += 2
                continue
            if ch == quote or (ch == "\n" and quote != "`"):
                quote = ""
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            while i < n and source[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if ch == "/" and nxt == "*":
            out.append("  ")
            i += 2
            while i < n and not (source[i] == "*" and i + 1 < n and source[i + 1] == "/"):
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
            if i < n:
                out.append("  ")
                i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def module_specifiers(source: str) -> list[tuple[int, str]]:
    """Return ``(line_number, specifier)`` for every module specifier in ``source``, in file order."""
    stripped = strip_comments(source)
    found: set[tuple[int, str]] = set()
    for pattern in _SPECIFIER_PATTERNS:
        for match in pattern.finditer(stripped):
            line = stripped.count("\n", 0, match.start()) + 1
            found.add((line, match.group(2)))
    return sorted(found)


@dataclass(frozen=True)
class TsPaths:
    """Alias configuration read from ``tsconfig.json``."""

    base_dir: Path | None = None
    has_base_url: bool = False
    aliases: tuple[tuple[str, tuple[str, ...]], ...] = ()


def _load_jsonc(path: Path) -> dict:
    text = strip_comments(path.read_text(encoding="utf-8"))
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    return document if isinstance(document, dict) else {}


def _collect_options(path: Path, seen: frozenset[Path]) -> dict[str, tuple[object, Path]]:
    """compilerOptions that matter, each with the directory of the config that declared it."""
    document = _load_jsonc(path)
    options: dict[str, tuple[object, Path]] = {}
    parent = document.get("extends")
    if isinstance(parent, str) and parent.startswith("."):
        candidate = (path.parent / parent).resolve()
        if candidate.suffix != ".json":
            candidate = candidate.with_name(candidate.name + ".json")
        if candidate.is_file() and candidate not in seen:
            options = _collect_options(candidate, seen | {candidate})
    compiler = document.get("compilerOptions")
    if isinstance(compiler, dict):
        for key in ("baseUrl", "paths"):
            if key in compiler:
                options[key] = (compiler[key], path.parent)
    return options


def load_tsconfig_paths(component_root: Path) -> TsPaths:
    """Read ``tsconfig.json`` under ``component_root``; no file means no aliases."""
    config = component_root / "tsconfig.json"
    if not config.is_file():
        return TsPaths()
    options = _collect_options(config.resolve(), frozenset({config.resolve()}))
    base_dir: Path | None = None
    has_base_url = False
    if "baseUrl" in options and isinstance(options["baseUrl"][0], str):
        has_base_url = True
        base_dir = (options["baseUrl"][1] / options["baseUrl"][0]).resolve()
    aliases: list[tuple[str, tuple[str, ...]]] = []
    if "paths" in options and isinstance(options["paths"][0], dict):
        if base_dir is None:
            base_dir = options["paths"][1].resolve()
        for pattern, targets in options["paths"][0].items():
            if isinstance(pattern, str) and isinstance(targets, list) and targets:
                aliases.append((pattern, tuple(str(target) for target in targets)))
    aliases.sort(key=lambda item: len(item[0].split("*", 1)[0]), reverse=True)
    return TsPaths(base_dir=base_dir, has_base_url=has_base_url, aliases=tuple(aliases))


def _alias_capture(pattern: str, specifier: str) -> str | None:
    if "*" not in pattern:
        return "" if pattern == specifier else None
    prefix, _, suffix = pattern.partition("*")
    if specifier.startswith(prefix) and specifier.endswith(suffix) and len(specifier) >= len(prefix) + len(suffix):
        return specifier[len(prefix):len(specifier) - len(suffix)]
    return None


def _strip_suffix(path: str) -> str:
    for suffix in TS_SUFFIXES:
        if path.endswith(suffix):
            return path[: -len(suffix)]
    return path


def _component_relative(target: Path, component_root: Path) -> str:
    return _strip_suffix(Path(os.path.relpath(target, component_root.resolve())).as_posix())


def _module_exists(candidate: Path) -> bool:
    return candidate.is_dir() or any(candidate.with_name(candidate.name + suffix).is_file() for suffix in TS_SUFFIXES)


def resolve_import(specifier: str, importer: Path, component_root: Path, ts_paths: TsPaths) -> str | None:
    """Component-relative path (no extension) the specifier points at, or ``None`` for a package."""
    if specifier.startswith("."):
        return _component_relative(Path(os.path.normpath(importer.resolve().parent / specifier)), component_root)
    if ts_paths.base_dir is not None:
        for pattern, targets in ts_paths.aliases:
            captured = _alias_capture(pattern, specifier)
            if captured is not None:
                target = targets[0].replace("*", captured)
                return _component_relative(Path(os.path.normpath(ts_paths.base_dir / target)), component_root)
        if ts_paths.has_base_url:
            candidate = Path(os.path.normpath(ts_paths.base_dir / specifier))
            if _module_exists(candidate):
                return _component_relative(candidate, component_root)
    return None


@lru_cache(maxsize=512)
def glob_regex(pattern: str) -> re.Pattern[str]:
    """``**`` crosses directories, ``*`` stays inside one path segment, ``?`` is one character."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def glob_matches(pattern: str, text: str) -> bool:
    return glob_regex(pattern).match(text) is not None
