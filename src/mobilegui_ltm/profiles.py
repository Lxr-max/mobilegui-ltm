"""Write/retrieve kind profiles for pass@k ablation."""

from __future__ import annotations

from dataclasses import dataclass

from mobilegui_ltm.schema import MemoryKind

MATRIX_MODES = ("off", "failures-only", "shortcuts-only", "anchors", "full")


@dataclass(frozen=True)
class MemoryProfile:
    name: str
    enabled: bool = True
    kinds: tuple[MemoryKind, ...] | None = None
    expand_hops: int = 0


PROFILES: dict[str, MemoryProfile] = {
    "off": MemoryProfile("off", enabled=False),
    "ltm-off": MemoryProfile("off", enabled=False),
    "on": MemoryProfile("full", enabled=True, expand_hops=1),
    "full": MemoryProfile("full", enabled=True, expand_hops=1),
    "ltm-on": MemoryProfile("full", enabled=True, expand_hops=1),
    "failures-only": MemoryProfile(
        "failures-only",
        kinds=(MemoryKind.FAILURE_NOTE,),
    ),
    "shortcuts-only": MemoryProfile(
        "shortcuts-only",
        kinds=(MemoryKind.SHORTCUT,),
    ),
    "anchors": MemoryProfile(
        "anchors",
        kinds=(MemoryKind.CAUSAL_ANCHOR,),
        expand_hops=1,
    ),
}


def resolve_profile(name: str | MemoryProfile | None) -> MemoryProfile | None:
    if name is None:
        return None
    if isinstance(name, MemoryProfile):
        return name
    key = name.strip().lower()
    if key not in PROFILES:
        raise ValueError(
            f"Unknown memory profile {name!r}. Use one of: {', '.join(MATRIX_MODES)}."
        )
    return PROFILES[key]
