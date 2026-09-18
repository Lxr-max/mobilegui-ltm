"""Injector: fold retrieved memories into system / planner / worker prompts.

Markers ``<mobilegui_ltm>`` / ``</mobilegui_ltm>`` wrap the block so agents and
tests can detect whether long-term memory was applied.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from mobilegui_ltm.schema import InjectionTarget, MemoryKind, MemoryRecord, ShortcutSpec

LTM_START = "<mobilegui_ltm>"
LTM_END = "</mobilegui_ltm>"

_KIND_HEADINGS = {
    MemoryKind.FAILURE_NOTE: "Failure notes (avoid on retry)",
    MemoryKind.UI_FACT: "UI facts (entities, filters, app state)",
    MemoryKind.SUBGOAL_TRACE: "Subgoal trace (progress / where stuck)",
    MemoryKind.SHORTCUT: "Shortcuts (reusable actions)",
    MemoryKind.CAUSAL_ANCHOR: "Causal anchors (why / evidence)",
}

_KIND_ORDER = (
    MemoryKind.FAILURE_NOTE,
    MemoryKind.UI_FACT,
    MemoryKind.SUBGOAL_TRACE,
    MemoryKind.SHORTCUT,
    MemoryKind.CAUSAL_ANCHOR,
)


def format_memories(memories: Sequence[MemoryRecord], *, header: str | None = None) -> str:
    """Render memories as a prompt block. Empty input → empty string."""
    if not memories:
        return ""
    title = header or "Long-term memory for this mobile GUI attempt"
    grouped: dict[MemoryKind, list[MemoryRecord]] = defaultdict(list)
    for record in memories:
        grouped[record.kind].append(record)
    lines = [LTM_START, title, "Do not reset these notes between pass@k attempts.", ""]
    for kind in _KIND_ORDER:
        bucket = grouped.get(kind) or []
        if not bucket:
            continue
        lines.append(f"### {_KIND_HEADINGS[kind]}")
        for record in bucket:
            apps = f" apps={','.join(record.app_ids)}" if record.app_ids else ""
            if record.kind is MemoryKind.SHORTCUT:
                lines.append(format_shortcut_for_worker(record))
                continue
            lines.append(
                f"- [{record.kind.value} | task={record.task_id} | "
                f"attempt={record.attempt_k}{apps}] {record.content}"
            )
            if record.kind is MemoryKind.CAUSAL_ANCHOR:
                evidence = record.metadata.get("evidence") or {}
                if evidence:
                    bits = [f"{k}={v}" for k, v in evidence.items() if v]
                    if bits:
                        lines.append("  evidence: " + ", ".join(bits))
                if record.depends_on:
                    lines.append("  depends_on: " + ", ".join(record.depends_on))
        lines.append("")
    lines.append(LTM_END)
    return "\n".join(lines).strip() + "\n"


def format_shortcut_for_worker(record: MemoryRecord) -> str:
    """Renderable callable-style block for a worker prompt."""
    spec = ShortcutSpec.from_record(record)
    if spec is None:
        return f"- [shortcut] {record.content}"
    args = ", ".join(f"{k}={v}" for k, v in spec.arguments.items())
    header = f"call {spec.name}({args})" if args else f"call {spec.name}()"
    lines = [f"- [shortcut | {header}]"]
    if spec.description:
        lines.append(f"  description: {spec.description}")
    if spec.preconditions:
        lines.append("  when: " + "; ".join(spec.preconditions))
    if spec.atomic_actions:
        lines.append("  steps:")
        for i, action in enumerate(spec.atomic_actions, start=1):
            lines.append(f"    {i}. {action.as_text()}")
    elif record.metadata.get("actions"):
        lines.append("  steps: " + " -> ".join(str(a) for a in record.metadata["actions"]))
    return "\n".join(lines)


class PromptInjector:
    """Configurable injector for string prompts or planner/worker dict state."""

    def __init__(
        self,
        *,
        header: str | None = None,
        placement: str = "prepend",
        default_target: InjectionTarget = InjectionTarget.SYSTEM,
    ) -> None:
        if placement not in {"prepend", "append"}:
            raise ValueError("placement must be 'prepend' or 'append'")
        self.header = header
        self.placement = placement
        self.default_target = default_target

    def inject(
        self,
        prompt_or_state: Any,
        memories: Sequence[MemoryRecord],
        *,
        target: InjectionTarget | str | None = None,
    ) -> Any:
        block = format_memories(memories, header=self.header)
        resolved = InjectionTarget(target or self.default_target)
        if not block:
            return prompt_or_state
        if prompt_or_state is None:
            return block
        if isinstance(prompt_or_state, str):
            return self._merge_text(prompt_or_state, block)
        if isinstance(prompt_or_state, dict):
            return self._merge_dict(prompt_or_state, block, resolved, memories)
        text = str(prompt_or_state)
        return self._merge_text(text, block)

    def _merge_text(self, original: str, block: str) -> str:
        if self.placement == "append":
            if not original:
                return block
            return original.rstrip() + "\n\n" + block
        if not original:
            return block
        return block + "\n" + original.lstrip()

    def _merge_dict(
        self,
        state: dict[str, Any],
        block: str,
        target: InjectionTarget,
        memories: Sequence[MemoryRecord],
    ) -> dict[str, Any]:
        merged = dict(state)
        key = target.value
        fallbacks = {
            InjectionTarget.SYSTEM: ("system", "system_prompt", "instruction"),
            InjectionTarget.PLANNER: ("planner", "planner_prompt", "plan_prompt"),
            InjectionTarget.WORKER: ("worker", "worker_prompt", "actor_prompt"),
        }
        dest = key if key in merged else None
        if dest is None:
            for candidate in fallbacks[target]:
                if candidate in merged:
                    dest = candidate
                    break
        if dest is None:
            dest = key
        previous = merged.get(dest, "")
        if previous is None:
            previous = ""
        if not isinstance(previous, str):
            previous = str(previous)
        merged[dest] = self._merge_text(previous, block)
        merged["_ltm_target"] = target.value
        merged["_ltm_memory_ids"] = [record.id for record in memories]
        return merged
