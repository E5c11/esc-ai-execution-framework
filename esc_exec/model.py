from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ManifestState(str, Enum):
    VALID = "VALID"
    INCOMPLETE = "INCOMPLETE"
    STALE = "STALE"
    INVALID = "INVALID"


@dataclass
class ValidationResult:
    state: ManifestState
    path: str
    messages: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return {
            ManifestState.VALID: 0,
            ManifestState.INVALID: 1,
            ManifestState.INCOMPLETE: 2,
            ManifestState.STALE: 3,
        }[self.state]

