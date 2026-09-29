"""The `grounding_check` stage of the `document` procedure (esc_exec.procedures.GROUNDING_CHECK).

`document` asks an agent to describe the repository as it is. Two things can be checked mechanically, and they are
all that is checked:

1. Only documentation changed. A `document` run that edits source, tests or build files has stopped documenting;
   restricting it also bounds what an agent can do under a verb that sounds harmless.
2. Every file or directory the documentation *points at* exists. A document that cites `src/export/Csv.kt:42` for
   behaviour that lives elsewhere -- or in a file that was never there -- is exactly the "documentation from
   assumptions" the workflow exists to prevent.

What this does NOT check, and never claims to: that the prose is accurate. A sentence can cite a real file and still
be wrong about it. The gate proves references resolve, nothing more; review is still the check on the meaning.

To keep false positives from teaching people to ignore the gate, a token is treated as a path claim only when it is
unmistakably one: a markdown link target (an explicit claim), or an inline-code span whose last segment has a file
extension or whose first segment is a real entry at the repository root. Code fences (examples and output), URLs,
hostnames, globs and placeholders are skipped, so `read/write`, `TCP/IP` or `npm run build` are never "missing files".

Pure: the caller supplies the documents' text and a function that says whether a path exists.
"""
from __future__ import annotations

import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass

from esc_exec.read_only import relevant

DOC_SUFFIXES = (".md", ".mdx", ".rst", ".txt", ".adoc")
DOC_DIRECTORIES = ("docs/", "doc/")

_INLINE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_LINE_SUFFIX = re.compile(r":\d+(?:[-:]\d+)*$")
_EXTENSION = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,7}$")
_HOSTNAME = re.compile(r"^[\w-]+(\.[\w-]+)+$")
_NOT_A_PATH = re.compile(r"[\s*<>{}$|\\\"']")
_EXTERNAL = ("http://", "https://", "mailto:", "ftp://", "tel:", "data:")


@dataclass(frozen=True)
class Reference:
    path: str  # as written, cleaned of a :line suffix and an #anchor
    line: int
    explicit: bool  # a markdown link target, rather than an inline-code span


def is_documentation(path: str) -> bool:
    return path.lower().endswith(DOC_SUFFIXES) or path.startswith(DOC_DIRECTORIES)


def non_documentation_changes(changed_paths: list[str]) -> list[str]:
    """Paths a `document` run changed that are not documentation (escape-ai's own bookkeeping directories excluded)."""
    return sorted(path for path in changed_paths if relevant(path) and not is_documentation(path))


def _clean(token: str) -> str | None:
    token = token.strip()
    if not token or token.startswith(_EXTERNAL) or token.startswith(("#", "/", "~")):
        return None
    token = token.split("#", 1)[0]
    token = _LINE_SUFFIX.sub("", token)
    if not token or _NOT_A_PATH.search(token):
        return None
    return token


def extract_references(text: str) -> list[Reference]:
    """Path claims in a document, in order. Fenced code blocks are skipped."""
    references: list[Reference] = []
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        for match in _LINK.finditer(line):
            path = _clean(match.group(1))
            if path:
                references.append(Reference(path, number, explicit=True))
        for match in _INLINE.finditer(line):
            path = _clean(match.group(1))
            if path and "/" in path:
                references.append(Reference(path, number, explicit=False))
    return references


def _claims_a_path(reference: Reference, exists: Callable[[str], bool]) -> bool:
    if reference.explicit:
        return True
    parts = reference.path.rstrip("/").split("/")
    if _EXTENSION.search(parts[-1]) and not (_HOSTNAME.match(parts[0]) and not exists(parts[0])):
        return True
    return exists(parts[0])  # `src/nothing-here`: the top-level directory is real, so this claims a path inside it


def _resolves(reference: Reference, document: str, exists: Callable[[str], bool]) -> bool:
    candidates = [posixpath.normpath(reference.path.rstrip("/"))]
    if reference.explicit or reference.path.startswith(("./", "../")):
        # markdown links are relative to the document that contains them
        candidates.append(posixpath.normpath(posixpath.join(posixpath.dirname(document), reference.path.rstrip("/"))))
    return any(not candidate.startswith("..") and exists(candidate) for candidate in candidates)


def grounding_blockers(documents: dict[str, str], exists: Callable[[str], bool]) -> list[str]:
    """One blocker per reference in `documents` ({repository-relative path: text}) that does not resolve."""
    blockers: list[str] = []
    for document, text in sorted(documents.items()):
        seen: set[tuple[str, int]] = set()
        for reference in extract_references(text):
            key = (reference.path, reference.line)
            if key in seen or not _claims_a_path(reference, exists):
                continue
            seen.add(key)
            if not _resolves(reference, document, exists):
                blockers.append(f"{document}:{reference.line}: `{reference.path}` does not exist in the repository")
    return blockers


def count_references(documents: dict[str, str], exists: Callable[[str], bool]) -> int:
    """How many path claims were checked (for the report)."""
    return sum(
        1 for text in documents.values() for reference in extract_references(text) if _claims_a_path(reference, exists)
    )
