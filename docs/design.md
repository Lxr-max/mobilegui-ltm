# MobileGUI-LTM design contract

This document is the MVP contract for **MobileGUI-LTM**: a pluggable long-term
memory SDK for Android / MobileWorld-style mobile GUI agents.

## What this is / is not

- **Is:** write trajectories, persist across attempts, retrieve + inject,
  backends swappable or disableable.
- **Is not:** a full agent; a generic chat MemoryOS.

Native LTM in common mobile GUI eval stacks is often closed or coupled to one
agent. Desktop-oriented agent memory, and mobile agents that are not aligned
with pass@k multi-attempt protocols, leave a gap. This SDK is the pluggable
layer in between.

## MemoryStore API

```
write_attempt(task_id, attempt_k, traj, outcome)   # success AND failure
retrieve(query, task_id?, app_ids?, k) -> memories
inject(prompt_or_state, memories) -> augmented
export/import(session)                              # cross-machine repro
clear / namespace(agent_id)                          # multi-agent isolation
```

`create_store(path, agent_id=..., enabled=True)` is the factory. `enabled=False`
makes write/retrieve/inject no-ops (LTM-off ablation) without changing call
sites.

### Pass@k non-reset rule

Inside a k-attempt loop the **environment** may reset; **memory must not**.
Adapters must not call `clear()` between attempts when LTM is on. Isolation
across agents uses `namespace(agent_id)`, not a wipe of the attempt history.

## Plugin points

Callers swap these without forking an agent:

1. **Backend** — JSON file (MVP) → SQLite (stub) → vector DB (later).
2. **Encoder** — trajectory → UI facts / subgoal traces / failure notes /
   shortcuts.
3. **Retriever** — hybrid by task, app, subgoal, embedding. MVP is BM25 /
   keyword; embedding is an optional stub (cosine if `MemoryRecord.embedding`
   and `embed_query` are provided).
4. **Injector** — which segment of system / planner / worker (configurable).

MVP persistence is **structured JSON**. There is **no graph database**.

## Memory kinds (mobile GUI specific)

Do not copy generic chat logs. Store:

| Kind | Semantics |
| --- | --- |
| UI facts | Cross-step / cross-app entities (shop names, filters, account-looking state). |
| Subgoal trace | How far the attempt got, where it stuck. |
| Failure notes | Failure mode + next-time avoidance; supports recovery-after-failure metrics. |
| Shortcuts | Reusable action snippets. **Phase 2** — `ShortcutEncoder` returns no records. |

Optional embedding field on `MemoryRecord` is reserved for phase 2.

## Ecosystem hooks

1. **pass@k multi-attempt protocol** (`adapters/pass_at_k.py`): memory is not
   reset between attempts. The example produces an LTM on/off comparison
   (success rate / recovery after failure).
2. Thin adapters for MobileWorld / AndroidWorld (`adapters/android_world.py`)
   — no extra runtime dependency; hook points only.
3. One dummy agent (`adapters/dummy.py`) and one planner/worker reference
   adapter (`adapters/agent_s2.py`) proving pluggability.
4. README + `mobilegui-ltm-demo --ablate` for LTM on/off commands.

## Encoder / retriever / injector (MVP behaviour)

- **Encoder (`TrajectorySummarizer`):** copies explicit `metadata` lists
  (`ui_facts`, `subgoals`, `failure_notes`); mines app ids, seller-like
  `Shop*` entities, size/filter mentions, last screen; always writes a
  subgoal trace; on non-success, writes `outcome.reason` plus an avoidance
  note. No LLM.
- **Retriever (`BM25Retriever`):** in-memory BM25 over the current namespace.
  Same-task boost; app-id overlap filter (records with empty `app_ids` still
  match). Recency fallback if the query tokenizes to empty.
- **Injector (`PromptInjector`):** wraps the block in
  `<mobilegui_ltm>…</mobilegui_ltm>`, prepends (default) or appends, and for
  dict state writes into `system` / `planner` / `worker` (with common alias
  keys).

## Backend layout

```
{root}/{sanitized_agent_id}.json
```

Atomic replace. `export_session` dumps one namespace; `import_session` upserts
(optionally remapped onto the current `agent_id`).

## Phase 2 (TODOs only)

- Real vector encoder + hybrid retrieval
- Shortcut mining
- Integrity / poisoning research hooks (CoMemOffset-style signing)
- Thicker env adapters

## Package

- Python 3.10+, package name `mobilegui-ltm`, import `mobilegui_ltm`
- Type hints + pydantic v2
- Tests run fully offline (`pytest`)
