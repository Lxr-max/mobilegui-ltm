"""Named memory blocks: Letta-inspired views over MemoryKind records.

Blocks are SDK-side buckets (planner_failures, worker_shortcuts, ui_state, …).
They are not a separate database and not a generic chat-memory OS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from mobilegui_ltm.schema import InjectionTarget, MemoryKind, MemoryRecord


@dataclass(frozen=True)
class MemoryBlock:
    """A named view over one or more memory kinds."""

    name: str
    kinds: tuple[MemoryKind, ...]
    description: str = ""
    targets: tuple[str, ...] = ()


DEFAULT_KIND_BLOCK: dict[MemoryKind, str] = {
    MemoryKind.FAILURE_NOTE: "planner_failures",
    MemoryKind.SUBGOAL_TRACE: "planner_failures",
    MemoryKind.CAUSAL_ANCHOR: "planner_failures",
    MemoryKind.SHORTCUT: "worker_shortcuts",
    MemoryKind.UI_FACT: "ui_state",
    MemoryKind.APP_PRIOR: "app_priors",
}

DEFAULT_BLOCKS: dict[str, MemoryBlock] = {
    "planner_failures": MemoryBlock(
        "planner_failures",
        (
            MemoryKind.FAILURE_NOTE,
            MemoryKind.SUBGOAL_TRACE,
            MemoryKind.CAUSAL_ANCHOR,
        ),
        description="Failure notes, progress traces, and causal evidence for the planner.",
        targets=(InjectionTarget.PLANNER.value, InjectionTarget.SYSTEM.value),
    ),
    "worker_shortcuts": MemoryBlock(
        "worker_shortcuts",
        (MemoryKind.SHORTCUT,),
        description="Executable shortcuts for the worker.",
        targets=(InjectionTarget.WORKER.value, InjectionTarget.SYSTEM.value),
    ),
    "ui_state": MemoryBlock(
        "ui_state",
        (MemoryKind.UI_FACT,),
        description="Entities, filters, and account-looking GUI state.",
        targets=(
            InjectionTarget.WORKER.value,
            InjectionTarget.PLANNER.value,
            InjectionTarget.SYSTEM.value,
        ),
    ),
    "app_priors": MemoryBlock(
        "app_priors",
        (MemoryKind.APP_PRIOR,),
        description="Installed-app / package-catalog priors (LocalRAG).",
        targets=(
            InjectionTarget.SYSTEM.value,
            InjectionTarget.PLANNER.value,
            InjectionTarget.WORKER.value,
        ),
    ),
}

TARGET_BLOCKS: dict[str, tuple[str, ...]] = {
    InjectionTarget.PLANNER.value: ("planner_failures", "ui_state"),
    InjectionTarget.WORKER.value: ("worker_shortcuts", "ui_state"),
    InjectionTarget.SYSTEM.value: tuple(DEFAULT_BLOCKS),
}


def resolve_block_registry(
    custom: dict[str, MemoryBlock | Sequence[MemoryKind | str]] | Sequence[MemoryBlock] | None = None,
) -> dict[str, MemoryBlock]:
    registry = dict(DEFAULT_BLOCKS)
    if custom is None:
        return registry
    if isinstance(custom, Sequence) and not isinstance(custom, (str, bytes)):
        for item in custom:
            if isinstance(item, MemoryBlock):
                registry[item.name] = item
        return registry
    for name, value in dict(custom).items():
        if isinstance(value, MemoryBlock):
            registry[name] = value
            continue
        kinds = tuple(
            item if isinstance(item, MemoryKind) else MemoryKind(item) for item in value
        )
        previous = registry.get(name)
        registry[name] = MemoryBlock(
            name,
            kinds,
            description=previous.description if previous else "",
            targets=previous.targets if previous else (),
        )
    return registry


def block_for_kind(
    kind: MemoryKind,
    registry: dict[str, MemoryBlock] | None = None,
) -> str:
    table = registry or DEFAULT_BLOCKS
    mapped = DEFAULT_KIND_BLOCK.get(kind)
    if mapped and mapped in table:
        return mapped
    for block in table.values():
        if kind in block.kinds:
            return block.name
    return mapped or "ui_state"


def record_block(
    record: MemoryRecord,
    registry: dict[str, MemoryBlock] | None = None,
) -> str:
    if record.block:
        return record.block
    return block_for_kind(record.kind, registry)


def filter_blocks(
    records: Iterable[MemoryRecord],
    names: Sequence[str] | str | None,
    *,
    registry: dict[str, MemoryBlock] | None = None,
) -> list[MemoryRecord]:
    if not names:
        return list(records)
    wanted = {names} if isinstance(names, str) else set(names)
    return [record for record in records if record_block(record, registry) in wanted]


def blocks_for_target(
    target: InjectionTarget | str,
    registry: dict[str, MemoryBlock] | None = None,
) -> tuple[str, ...]:
    key = target.value if isinstance(target, InjectionTarget) else str(target)
    table = registry or DEFAULT_BLOCKS
    if key in TARGET_BLOCKS:
        return tuple(name for name in TARGET_BLOCKS[key] if name in table)
    if key in table:
        return (key,)
    return tuple(table)


def normalize_block_names(
    block: str | None = None,
    blocks: Sequence[str] | None = None,
) -> tuple[str, ...]:
    names: list[str] = []
    if block:
        names.append(block)
    if blocks:
        names.extend(blocks)
    return tuple(dict.fromkeys(names))
