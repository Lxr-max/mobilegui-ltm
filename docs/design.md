# MobileGUI-LTM design contract

This document is the contract for **MobileGUI-LTM**: a pluggable long-term
memory SDK for Android / MobileWorld-style mobile GUI agents.

## What this is / is not

- **Is:** write trajectories, persist across attempts, retrieve + inject,
  backends swappable or disableable. Mid-episode CRUD for UI facts.
- **Is not:** a full agent; a generic chat MemoryOS.

Native LTM in common mobile GUI eval stacks is often closed or coupled to one
agent. Desktop-oriented agent memory, and mobile agents that are not aligned
with pass@k multi-attempt protocols, leave a gap. This SDK is the pluggable
layer in between.

## MemoryStore API

```
write_attempt(task_id, attempt_k, traj, outcome)   # success AND failure
remember / update / delete / get                    # mid-episode CRUD
retrieve(query, task_id?, app_ids?, k, screen?, …) -> memories
inject(prompt_or_state, memories) -> augmented
format_worker_shortcuts(memories)                   # callable-style shortcut text
export/import(session)                              # cross-machine repro
clear / namespace(agent_id)                          # multi-agent isolation
rebuild_index()                                     # re-embed current namespace
```

`create_store(path, agent_id=..., enabled=True, retriever=..., embedder=...,
profile=..., kind_weights=..., expand_hops=..., strict_task=...)` is the
factory. `enabled=False` makes write/retrieve/inject into no-ops (LTM-off
ablation) without changing call sites.

Profiles (`off` / `ltm-off`, `failures-only`, `shortcuts-only`, `anchors`,
`full` / `on` / `ltm-on`) set write/retrieve kinds and whether 1-hop expand
is on.

### Pass@k non-reset rule

Inside a k-attempt loop the **environment** may reset; **memory must not**.
Adapters must not call `clear()` between attempts when LTM is on. Isolation
across agents uses `namespace(agent_id)`, not a wipe of the attempt history.

### UI fact supersede

Records may carry a `logical_key` (for example `ui:seller`, `ui:filters`,
`ui:account`). A later write with the same `(kind, logical_key)` marks the
previous **active** row `superseded` and points `superseded_by` at the new
id. Retrieve/inject/list skip superseded and deleted rows so pass@k does not
keep contradictory seller/filter/account facts. Failure notes are left
without keys so attempt history still accumulates for metrics.

`remember(content, key=...)` inserts (and supersedes). `update` patches an
active row in place. `delete` soft-deletes by default; `hard=True` removes
the JSON row.

## Plugin points

Callers swap these without forking an agent:

1. **Backend** — JSON file (default) → SQLite (stub).
2. **Encoder** — trajectory → UI facts / subgoal traces / failure notes /
   shortcuts / causal anchors.
3. **Retriever** — BM25, hybrid (BM25 + cosine), or vector-only. Kind
   weights and shortcut precondition soft-match are applied at score time.
4. **Injector** — which segment of system / planner / worker (configurable).
5. **Embedder** (optional) — hashing (default local), FakeEmbedder (tests),
   sentence-transformers (optional extra).

Persistence is **structured JSON**. There is **no graph database**.

## Memory kinds (mobile GUI specific)

Do not copy generic chat logs. Store:

| Kind | Semantics |
| --- | --- |
| UI facts | Cross-step / cross-app entities (shop names, filters, account-looking state). Logical keys supersede. |
| Subgoal trace | How far the attempt got, where it stuck. |
| Failure notes | Failure mode + next-time avoidance; supports recovery-after-failure metrics. |
| Shortcuts | Executable specs from successful (or clean partial) trajectories. |
| Causal anchors | `content` + `evidence` (app/screen/widget) + `depends_on[]` (ids or logical keys). |

`MemoryRecord.embedding` holds an optional dense vector. JSON export/import
preserves it. A sidecar `{agent}.vectors.json` stores `{id: vector}` plus
embedder name/dim for rebuilds. `status` is `active` / `superseded` /
`deleted`.

## Shortcuts

`ShortcutEncoder` (wired into `TrajectorySummarizer` by default):

- Emits on `OutcomeStatus.SUCCESS`.
- Also emits on `PARTIAL` when `metrics["progress"] >= 0.8`, the last screen
  looks completed (cart/checkout/…), or `metadata["emit_shortcuts"]` is set.
- Never mines shortcuts from failures (failed actions are not reusable).
- Writes an **episode** shortcut (full `atomic_actions`) plus up to three
  **skill** windows around filter/cart/search/… actions.
- Copies explicit `Trajectory.metadata["shortcuts"]` (strings or dicts).

`ShortcutSpec` fields: `name`, `description`, `preconditions`, `arguments`,
`atomic_actions[]`, `tags`, `app_ids`, `subgoal`. Records store
`metadata["shortcut"]` plus a dual-read `metadata["actions"]` list so older
text-only shortcuts still parse via `ShortcutSpec.from_record`.

The injector surfaces shortcuts as callable-style blocks:

```
- [shortcut | call filter_cart(size=9)]
  description: …
  when: app=com.shop; start_screen=search
  steps:
    1. apply_filter:size=9
    2. tap:add_to_cart
```

`format_shortcut_for_worker` is the same helper for worker prompts.

The worker-side planner/worker adapter prefers UI facts and shortcuts; the
planner still prefers failure notes and subgoal traces.

## Causal anchors (1-hop, no graph DB)

`CausalAnchorEncoder` emits:

- Screen-to-screen transitions (`Subgoal moved search -> pdp`) with evidence
  and `depends_on` pointing at `ui:screens` / the task's subgoal-progress key.
- A failure-point anchor whose content includes the outcome reason.

`retrieve(..., expand_hops=1)` (or `create_store(..., expand_hops=1)` /
`profile="full"` / `profile="anchors"`) appends active records linked by id
or `logical_key` after the primary ranking. This is in-memory pointer
chasing, not a graph store.

## Vector retrieval

- **Default store:** BM25 only (`create_store(path)`).
- **Hybrid:** `create_store(path, retriever="hybrid")` or `embedder="hashing"`.
  Score = `lexical_weight * norm(BM25) + vector_weight * norm(cosine)`, then
  kind weight and shortcut-precondition bonus.
- **Vector-only:** `retriever="vector"`.
- **Tests/CI:** `embedder=FakeEmbedder(...)` with optional substring overrides.
  No network, no model download.
- **Neural:** `embedder="sentence-transformers"` requires
  `pip install 'mobilegui-ltm[embed]'` (alias `[vector]`).

Hard filters (applied before ranking):

- `app_ids` — drop records whose `app_ids` are disjoint (unscoped records stay).
- `screen` — drop records that declare a screen (field, metadata, evidence,
  or `start_screen=` precondition) that does not match; records with no
  screen still match.
- `strict_task=True` — keep only `task_id` matches. Default is a same-task
  **score boost**, not a hard drop.

`write_attempt` stamps embeddings when an embedder is configured. Missing
vectors are filled on retrieve. After switching embedders:

```
store.rebuild_index()
```

## Ecosystem hooks

1. **pass@k multi-attempt protocol** (`adapters/pass_at_k.py`): memory is not
   reset between attempts. The example produces an LTM on/off comparison
   (success rate / recovery after failure) and a **kind-profile matrix**
   (`off`, `failures-only`, `shortcuts-only`, `anchors`, `full`). Demo
   default retriever remains BM25; `--retriever hybrid` is optional.
2. Thin adapters for MobileWorld / AndroidWorld (`adapters/android_world.py`)
   — no extra runtime dependency; hook points only.
3. One dummy agent (`adapters/dummy.py`) and one planner/worker reference
   adapter (`adapters/agent_s2.py`) proving pluggability.
4. README + `mobilegui-ltm-demo --ablate` / `--matrix`.

### How to run the ablation matrix

```bash
mobilegui-ltm-demo --matrix --k 2
# or
python examples/pass_at_k_demo/run_demo.py --ltm matrix --k 2
```

Metrics in the table: `pass@1`, `pass@k`, `recovery_after_failure`,
`solved_at`. On the dummy shopping task, **full** recovers and **off** does
not; **failures-only** recovers; **shortcuts-only** does not (attempt 1 is a
failure, so no shortcut is stored).

## Encoder / retriever / injector

- **Encoder (`TrajectorySummarizer`):** copies explicit `metadata` lists
  (`ui_facts`, `subgoals`, `failure_notes`, `shortcuts`); mines app ids,
  seller-like `Shop*` entities, size/filter mentions, last screen; always
  writes a subgoal trace; on non-success, writes `outcome.reason` plus an
  avoidance note; on success, `ShortcutEncoder` mines executable snippets;
  `CausalAnchorEncoder` writes transition/failure anchors. No LLM.
- **Retriever:** `BM25Retriever` (default) or `HybridRetriever` /
  `EmbeddingRetriever`. Same-task boost; app-id / screen / optional strict
  task filters; per-kind weights; shortcut precondition soft-match; optional
  1-hop expand.
- **Injector (`PromptInjector`):** wraps the block in
  `<mobilegui_ltm>…</mobilegui_ltm>`. Shortcuts render as callable-style
  blocks; anchors include evidence / depends_on.

## Backend layout

```
{root}/{sanitized_agent_id}.json
{root}/{sanitized_agent_id}.vectors.json   # optional embedding sidecar
```

Atomic replace. Sidecar files are not loaded as memories. `export_session`
dumps one namespace; `import_session` upserts (optionally remapped onto the
current `agent_id`) and stamps embeddings if the destination store has an
embedder.

## Later work

- Integrity / poisoning research hooks (CoMemOffset-style signing)
- Thicker env adapters
- SQLite / external vector DB backends

## Package

- Python 3.10+, package name `mobilegui-ltm`, import `mobilegui_ltm`
- Type hints + pydantic v2
- Tests run fully offline (`pytest`) with `FakeEmbedder` / hashing
