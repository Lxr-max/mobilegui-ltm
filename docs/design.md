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
retrieve(..., block=?, blocks=?)                    # optional named view
inject(..., block=?) / inject_block(prompt, block, query)
promote / demote                                    # shortcut/anchor gate
quarantine / unquarantine                           # metadata flag; retrieve skips
register_app_prior                                  # LocalRAG catalog (no ADB)
poison                                              # integrity eval fixture
format_worker_shortcuts(memories)
export/import(session)
clear / namespace(agent_id)
rebuild_index()
```

`create_store(..., profile=..., kind_weights=..., expand_hops=...,
local_rag=..., blend_local_rag=..., integrity=..., hmac_key=...,
promote_after=..., include_candidates=..., diagnostics=True|OnlineDiagnostics|False)`
is the factory. `enabled=False` makes write/retrieve/inject into no-ops
(LTM-off ablation) without changing call sites.

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
4. **Injector** — which segment of system / planner / worker; optional named
   memory **block** so a planner is not forced to ingest worker shortcuts.
5. **Embedder** (optional) — hashing (default local), FakeEmbedder (tests),
   sentence-transformers (optional extra).
6. **LocalRAG** (optional, default off) — installed-app / package catalog.
   Stub (`NullLocalRAG`) or in-memory `CatalogLocalRAG`. No device I/O.
7. **IntegrityGuard** (optional, default off) — SHA-256 + optional HMAC
   research hooks for poisoning experiments. Not a crypto product.
8. **OnlineDiagnostics** (optional, default off) — after `write_attempt`,
   attribute retrieved memories, classify failures, propose promote /
   demote / quarantine. `apply(dry_run=True)` by default.
9. **EpisodeReflector / OutcomeProvider** — plugin interfaces only.
   `NullEpisodeReflector` and `NullOutcomeProvider` are the defaults.
   No LLM client or API keys on the CI path.

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
| App priors | Optional LocalRAG catalog rows (`MemoryKind.APP_PRIOR`). Default off. |

`MemoryRecord.embedding` holds an optional dense vector. JSON export/import
preserves it. A sidecar `{agent}.vectors.json` stores `{id: vector}` plus
embedder name/dim for rebuilds. `status` is `active` / `superseded` /
`deleted`. `stability` is `candidate` / `stable` / `retired` (promotion
gate). `block` names the memory view. `content_hash` / `signature` /
`signer` are integrity stamps when enabled.

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
4. README + `mobilegui-ltm-demo --ablate` / `--matrix` / `--diagnose`.

### How to run the ablation matrix

```bash
mobilegui-ltm-demo --matrix --k 2
# or
python examples/pass_at_k_demo/run_demo.py --ltm matrix --k 2
```

Metrics in the table: `pass@1`, `pass@k`, `recovery_after_failure`,
`solved_at`. On the dummy shopping task, **full** recovers and **off** does
not; **failures-only** recovers; **anchors** recover because failure evidence
is copied into the anchor; **shortcuts-only** does not (attempt 1 is a
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
  `<mobilegui_ltm>…</mobilegui_ltm>`. Named blocks add a `[block=name]`
  section so planner/worker inject can stay isolated. Shortcuts render as
  callable-style blocks; anchors include evidence / depends_on.

## Named memory blocks

Default registry (overridable via `create_store(blocks=…)`):

- `planner_failures` — failure notes, subgoal traces, causal anchors
- `worker_shortcuts` — shortcuts
- `ui_state` — UI facts
- `app_priors` — LocalRAG catalog hits

`remember(..., block=)` and `retrieve(..., block=)` / `inject(..., block=)`
select a view. `inject_block(prompt, block, query)` retrieves then injects
one view. The planner/worker reference adapter retrieves by these names.

## Promotion gate

Promotable kinds (default: shortcuts + causal anchors) start as `candidate`
when the field was not set explicitly. `write_attempt` increments
`success_count` on success and `fail_count` on failure for same-`task_id`
promotable rows (counts are inherited across logical-key supersede). After
`promote_after` successes the row becomes `stable`. After `demote_after`
failures, `stable` → `candidate` and `candidate` → `retired`. Explicit
`promote()` / `demote()` always win. Retrieve: `prefer_stable` score bonus;
`include_candidates` (default True); retired omitted unless
`include_retired`. A failure→success **recovery delta** on `write_attempt`
adds one extra `success_count` increment for retrieved promotable rows
(so a skill that actually recovered a retry can reach `promote_after`
sooner).

## OnlineDiagnostics

Optional runtime loop (no LLM):

```
write_attempt → encode/commit → promotion gate → on_episode_end(trace)
```

`EpisodeTrace` carries the trajectory, outcome, retrieved rows, and the
previous attempt's outcome. `OnlineDiagnostics.on_episode_end` returns a
`DiagnosticReport`:

- **Attribution** (`helped` / `hurt` / `unused`): success + retrieved
  shortcut aligned with the trajectory → helped; failure after injecting
  a shortcut → hurt; otherwise unused. Other retrieved kinds may count as
  helped on success when their content overlaps the episode.
- **Failure class** (heuristic, no LLM): `state_loss`, `misbinding`,
  `context_drift`, `unverified_progress`, `interruption`, `unknown`.
- **Actions**: conservative promote / demote / quarantine proposals.
- **Reflector**: `EpisodeReflector.reflect` may attach tip/shortcut
  rewrite proposals. They are **not** applied unless
  `apply(..., include_reflector=True)`.

`EvalAuditor.scan(store)` looks for polarity contradictions (avoid X vs
tap X), integrity hash mismatches, and stale candidates (promotable
candidate with `fail_count >= demote_after` and no successes). Suggestions
are quarantine, not hard-delete.

**Auto vs human-in-the-loop**

| Step | Automatic? |
| --- | --- |
| Score episode, classify, propose actions | yes, when `diagnostics=True` |
| Apply promote / demote / quarantine | no (dry-run); CLI `--apply-diagnostics` or `apply(dry_run=False)` |
| Apply reflector rewrites | never unless explicitly requested |
| Quarantine | metadata `quarantined=true`; retrieve excludes by default |

CLI: `mobilegui-ltm-demo --diagnose --k 2` prints attribution counts,
promote/demote proposals, and auditor contradictions on the dummy
shopping task.

## LocalRAG

Default `NullLocalRAG` (off). `CatalogLocalRAG` is an in-memory package
list. `retrieve(..., blend_local_rag=True)` appends ephemeral `app_prior`
records after LTM ranking. `AndroidWorldAdapter.register_app_prior` writes
catalog rows without importing or calling ADB.

## Integrity research hooks

`IntegrityGuard` stamps `content_hash` (SHA-256 of canonical content
fields; embeddings excluded) and optional HMAC (`hmac_key` or
`MOBILEGUI_LTM_HMAC_KEY`). Retrieve verifies and drops mismatches when
`drop_unverified=True`. `poison()` / `tamper()` mutate content without
refreshing the hash. This is a measurement rail for poisoning studies, not
a production signing scheme.

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

- Thicker env adapters
- SQLite / external vector DB backends
- Optional LLM client implementing `EpisodeReflector` (plugin only)

## Package

- Python 3.10+, package name `mobilegui-ltm`, import `mobilegui_ltm`
- Type hints + pydantic v2
- Tests run fully offline (`pytest`) with `FakeEmbedder` / hashing
