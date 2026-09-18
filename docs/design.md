# MobileGUI-LTM design contract

This document is the contract for **MobileGUI-LTM**: a pluggable long-term
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
rebuild_index()                                     # re-embed current namespace
```

`create_store(path, agent_id=..., enabled=True, retriever=..., embedder=...)`
is the factory. `enabled=False` makes write/retrieve/inject no-ops (LTM-off
ablation) without changing call sites.

### Pass@k non-reset rule

Inside a k-attempt loop the **environment** may reset; **memory must not**.
Adapters must not call `clear()` between attempts when LTM is on. Isolation
across agents uses `namespace(agent_id)`, not a wipe of the attempt history.

## Plugin points

Callers swap these without forking an agent:

1. **Backend** — JSON file (default) → SQLite (stub).
2. **Encoder** — trajectory → UI facts / subgoal traces / failure notes /
   shortcuts.
3. **Retriever** — BM25, hybrid (BM25 + cosine), or vector-only.
4. **Injector** — which segment of system / planner / worker (configurable).
5. **Embedder** (optional) — hashing (default local), FakeEmbedder (tests),
   sentence-transformers (optional extra).

Persistence is **structured JSON**. There is **no graph database**.

## Memory kinds (mobile GUI specific)

Do not copy generic chat logs. Store:

| Kind | Semantics |
| --- | --- |
| UI facts | Cross-step / cross-app entities (shop names, filters, account-looking state). |
| Subgoal trace | How far the attempt got, where it stuck. |
| Failure notes | Failure mode + next-time avoidance; supports recovery-after-failure metrics. |
| Shortcuts | Reusable action snippets from successful (or clean partial) trajectories: tags for task/app/subgoal, action sequence, optional preconditions. |

`MemoryRecord.embedding` holds an optional dense vector. JSON export/import
preserves it. A sidecar `{agent}.vectors.json` stores `{id: vector}` plus
embedder name/dim for rebuilds.

## Shortcuts

`ShortcutEncoder` (wired into `TrajectorySummarizer` by default):

- Emits on `OutcomeStatus.SUCCESS`.
- Also emits on `PARTIAL` when `metrics["progress"] >= 0.8`, the last screen
  looks completed (cart/checkout/…), or `metadata["emit_shortcuts"]` is set.
- Never mines shortcuts from failures (failed actions are not reusable).
- Writes an **episode** shortcut (full action list) plus up to three **skill**
  windows around filter/cart/search/… actions.
- Copies explicit `Trajectory.metadata["shortcuts"]` (strings or dicts).

The worker-side planner/worker adapter prefers UI facts and shortcuts; the
planner still prefers failure notes and subgoal traces.

## Vector retrieval

- **Default store:** BM25 only (`create_store(path)`).
- **Hybrid:** `create_store(path, retriever="hybrid")` or `embedder="hashing"`.
  Score = `lexical_weight * norm(BM25) + vector_weight * norm(cosine)`.
- **Vector-only:** `retriever="vector"`.
- **Tests/CI:** `embedder=FakeEmbedder(...)` with optional substring overrides.
  No network, no model download.
- **Neural:** `embedder="sentence-transformers"` requires
  `pip install 'mobilegui-ltm[embed]'` (alias `[vector]`).

`write_attempt` stamps embeddings when an embedder is configured. Missing
vectors are filled on retrieve. After switching embedders:

```
store.rebuild_index()
```

## Ecosystem hooks

1. **pass@k multi-attempt protocol** (`adapters/pass_at_k.py`): memory is not
   reset between attempts. The example produces an LTM on/off comparison
   (success rate / recovery after failure). Demo default retriever remains
   BM25; `--retriever hybrid` is optional.
2. Thin adapters for MobileWorld / AndroidWorld (`adapters/android_world.py`)
   — no extra runtime dependency; hook points only.
3. One dummy agent (`adapters/dummy.py`) and one planner/worker reference
   adapter (`adapters/agent_s2.py`) proving pluggability.
4. README + `mobilegui-ltm-demo --ablate` for LTM on/off commands.

## Encoder / retriever / injector

- **Encoder (`TrajectorySummarizer`):** copies explicit `metadata` lists
  (`ui_facts`, `subgoals`, `failure_notes`, `shortcuts`); mines app ids,
  seller-like `Shop*` entities, size/filter mentions, last screen; always
  writes a subgoal trace; on non-success, writes `outcome.reason` plus an
  avoidance note; on success, `ShortcutEncoder` mines action snippets. No LLM.
- **Retriever:** `BM25Retriever` (default) or `HybridRetriever` /
  `EmbeddingRetriever`. Same-task boost; app-id overlap filter.
- **Injector (`PromptInjector`):** wraps the block in
  `<mobilegui_ltm>…</mobilegui_ltm>`. Shortcuts include action lists and
  preconditions.

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
